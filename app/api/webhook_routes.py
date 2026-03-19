"""
Telegram Webhook — entry point for all incoming messages.

Updated in this version
───────────────────────
• Handles update_task intent — looks up task by number or name,
  confirms with user, then calls notion_write_service
• Handles add_task intent — confirms with user, then creates task
• Logs every inbound message and outbound reply to message_history table
• Uses DB-backed conversation_state (survives restarts)
"""

import requests as http_requests
from fastapi import APIRouter, Request, BackgroundTasks

from app.config import settings
from app.models.intent import Intent, VALID_NOTION_STATUSES
from app.models.workspace import Workspace
from app.core.execution_context import ExecutionContext
from app.services.ai_service import parse_intent, free_response
from app.services.reminder_service import (
    run_reminder_engine,
    run_morning_brief,
    run_evening_wrapup,
)
from app.services.telegram_service import send_message
from app.services.notion_write_service import lookup_task, update_task_status, add_task
from app.state.conversation_state import (
    store_pending,
    get_pending,
    clear_pending,
    is_confirmation,
    is_cancellation,
)
from app.state.reminder_state import clear_state, get_state_summary
from app.core.scheduler import get_scheduler_status
from app.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


# ── Message history logging ───────────────────────────────────────────────────

def _log_message(chat_id: str, direction: str, text: str, intent: str = None) -> None:
    """
    Persist a message to the message_history table.

    direction: 'in'  (user → bot)
               'out' (bot → user)

    Non-critical — never raises, logs warning on failure.
    """
    try:
        from app.db.database import get_db
        from app.db.models import MessageHistory
        with get_db() as db:
            db.add(MessageHistory(
                chat_id   = chat_id,
                direction = direction,
                text      = text[:4000],       # cap at 4000 chars
                intent    = intent,
            ))
    except Exception as exc:
        log.warning("Message history logging failed (non-critical): %s", exc)


# ── Telegram API helpers ──────────────────────────────────────────────────────

def _telegram_api(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    response = http_requests.post(url, json=payload, timeout=10)
    return response.json()


def register_webhook() -> None:
    """Register our URL with Telegram. Called at startup."""
    webhook_url = f"{settings.WEBHOOK_BASE_URL.rstrip('/')}/webhook/telegram"
    result = _telegram_api("setWebhook", {
        "url":             webhook_url,
        "secret_token":    settings.WEBHOOK_SECRET,
        "allowed_updates": ["message"],
    })
    if result.get("ok"):
        log.info("Telegram webhook registered → %s", webhook_url)
    else:
        log.error("Failed to register Telegram webhook: %s", result)


def delete_webhook() -> None:
    """Unregister webhook — NOT called on shutdown (intentional)."""
    result = _telegram_api("deleteWebhook", {})
    if result.get("ok"):
        log.info("Telegram webhook deleted.")


# ── Incoming update handler ───────────────────────────────────────────────────

@router.post("/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive a Telegram update. Returns 200 immediately, processes in background.
    Validates the secret token header to reject non-Telegram requests.
    """
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.WEBHOOK_SECRET:
        log.warning("Webhook received with invalid secret token — ignoring.")
        return {"ok": True}

    body    = await request.json()
    message = body.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = message.get("text", "").strip()

    if not chat_id or not text:
        return {"ok": True}

    log.info("Webhook message received  chat_id=%s  text=%r", chat_id, text[:80])

    # Security — only authorised chat
    if chat_id != settings.TELEGRAM_CHAT_ID:
        log.warning("Message from unauthorised chat_id=%s — ignoring.", chat_id)
        return {"ok": True}

    background_tasks.add_task(_process_message, chat_id=chat_id, text=text)
    return {"ok": True}


# ── Message processor ─────────────────────────────────────────────────────────

async def _process_message(chat_id: str, text: str) -> None:
    ws  = Workspace.from_settings()
    ctx = ExecutionContext.new(job="webhook")

    # Log inbound message
    _log_message(chat_id, "in", text)

    pending_intent = get_pending(chat_id)

    if pending_intent:
        if is_confirmation(text):
            log.info("Confirmation received  action=%s", pending_intent.action)
            clear_pending(chat_id)
            await _execute_action(pending_intent, ws, ctx, chat_id)

        elif is_cancellation(text):
            clear_pending(chat_id)
            log.info("Action cancelled  action=%s", pending_intent.action)
            await _send_reply(chat_id, ws, ctx, "❌ Cancelled. Let me know if you need anything else.")

        else:
            # New message while waiting — clear old pending, handle as fresh
            log.info("New message while awaiting confirmation — clearing pending action=%s",
                     pending_intent.action)
            clear_pending(chat_id)
            await _handle_new_message(text, chat_id, ws, ctx)
        return

    await _handle_new_message(text, chat_id, ws, ctx)


async def _handle_new_message(
    text: str, chat_id: str, ws: Workspace, ctx: ExecutionContext
) -> None:
    """Parse intent and route to confirmation or direct reply."""
    intent = parse_intent(text)
    log.info("Intent resolved  action=%s  confidence=%.2f  params=%s",
             intent.action, intent.confidence, str(intent.parameters)[:80])

    if intent.action == "free_response":
        reply = intent.ai_reply or free_response(text)
        await _send_reply(chat_id, ws, ctx, reply)

    elif intent.action == "unknown":
        reply = intent.ai_reply or (
            "I didn't understand that. You can ask me to:\n"
            "• Check urgent tasks\n"
            "• Send the morning or evening brief\n"
            "• Update a task status\n"
            "• Add a new task\n"
            "• Show all tasks or check system status"
        )
        await _send_reply(chat_id, ws, ctx, reply)

    elif intent.is_actionable and intent.requires_confirmation:
        # Build the confirmation message — for Notion write actions,
        # we do the task lookup here so the confirmation shows the real task name
        confirm_msg = await _build_confirmation_message(intent)

        if confirm_msg is None:
            # Task lookup failed — already sent an error reply inside the function
            return

        store_pending(chat_id, intent)
        await _send_reply(chat_id, ws, ctx, confirm_msg)

    else:
        await _send_reply(chat_id, ws, ctx, "I'm not sure what to do with that.")


async def _build_confirmation_message(intent: Intent) -> Optional[str]:
    """
    Build the confirmation prompt shown to the user before an action runs.

    For update_task and add_task, includes the actual task name/details
    resolved from the cache. Returns None if task lookup fails.
    """
    confidence_pct = int(intent.confidence * 100)

    if intent.action == "update_task":
        task_ref   = intent.parameters.get("task_ref", "")
        new_status = intent.parameters.get("new_status", "")

        # Validate status value
        if new_status not in VALID_NOTION_STATUSES:
            return (
                f"⚠️ I don't recognise the status <b>{new_status}</b>.\n"
                f"Valid statuses are: Pending, In Progress, Stopped, Completed."
            )

        # Look up the task
        task = lookup_task(task_ref)
        if not task:
            return (
                f"⚠️ I couldn't find a task matching <b>{task_ref}</b>.\n"
                f"Try sending the task list first (\"show all tasks\") then refer to a task number."
            )

        # Store resolved task in parameters so execute_action has it
        intent.parameters["resolved_notion_id"] = task["notion_id"]
        intent.parameters["resolved_name"]      = task["name"]

        return (
            f"🤔 Update <b>{task['name']}</b> → <b>{new_status}</b>?\n"
            f"Confidence: {confidence_pct}%\n\n"
            f"Reply <b>yes</b> to confirm or <b>no</b> to cancel."
        )

    elif intent.action == "add_task":
        task_name = intent.parameters.get("task_name", "")
        due_date  = intent.parameters.get("due_date")

        if not task_name:
            return "⚠️ I couldn't extract a task name from your message. Could you rephrase?"

        due_str = f" due {due_date}" if due_date else ""
        return (
            f"🤔 Add new task: <b>{task_name}</b>{due_str}?\n"
            f"Confidence: {confidence_pct}%\n\n"
            f"Reply <b>yes</b> to confirm or <b>no</b> to cancel."
        )

    # Standard actions
    descriptions = {
        "force_reminder":  "run the reminder engine and check for urgent tasks",
        "morning_brief":   "send the morning task brief",
        "evening_brief":   "send the evening wrap-up",
        "send_update":     "send the current full task list",
        "clear_state":     "clear all reminder state (alerts will re-fire)",
        "test_telegram":   "send a Telegram connectivity test",
        "status":          "report system status",
    }
    description = descriptions.get(intent.action, intent.action)
    return (
        f"🤔 I understood you want me to <b>{description}</b>.\n"
        f"Confidence: {confidence_pct}%\n\n"
        f"Reply <b>yes</b> to confirm or <b>no</b> to cancel."
    )


# ── Action executor ───────────────────────────────────────────────────────────

async def _execute_action(
    intent: Intent, ws: Workspace, ctx: ExecutionContext, chat_id: str
) -> None:
    """Execute the confirmed action and send the result back."""
    log.info("Executing confirmed action  action=%s  params=%s",
             intent.action, str(intent.parameters)[:80])

    try:
        if intent.action == "force_reminder":
            result = run_reminder_engine(ws, ctx)
            sent   = result.get("alerts_sent", 0)
            reply  = (
                f"✅ Reminder engine ran. {sent} alert(s) sent."
                if sent > 0
                else "✅ Reminder engine ran. No new alerts — all clear."
            )

        elif intent.action == "morning_brief":
            result = run_morning_brief(ws, ctx)
            reply  = "✅ Morning brief sent." if result.get("status") == "sent" \
                     else f"❌ Failed: {result.get('error')}"

        elif intent.action == "evening_brief":
            result = run_evening_wrapup(ws, ctx)
            reply  = "✅ Evening wrap-up sent." if result.get("status") == "sent" \
                     else f"❌ Failed: {result.get('error')}"

        elif intent.action == "send_update":
            from app.services.notion_service import fetch_tasks
            tasks = fetch_tasks(ws, ctx)
            if tasks:
                lines = [
                    f"  {i}. {t.name}" + (f" — {t.due_iso()}" if t.due else "")
                    for i, t in enumerate(tasks, 1)
                ]
                body = "\n".join(lines)
            else:
                body = "  No active tasks found."
            send_message(f"🔔 <b>Task Update</b>\n\n{body}", ws, ctx)
            reply = f"✅ Task update sent. ({len(tasks)} tasks)"

        elif intent.action == "clear_state":
            clear_state()
            reply = "✅ Reminder state cleared. All alerts will re-fire on the next cycle."

        elif intent.action == "test_telegram":
            send_message("✅ <b>Ops Agent</b> — pipeline check OK", ws, ctx)
            reply = "✅ Telegram test sent successfully."

        elif intent.action == "status":
            sched = get_scheduler_status()
            state = get_state_summary()
            jobs  = sched.get("jobs", [])
            job_lines = "\n".join(
                f"  • {j['id']}: next {j['next_run']}" for j in jobs
            ) or "  No jobs"
            reply = (
                f"🟢 <b>System Status</b>\n\n"
                f"Scheduler: {'running' if sched.get('running') else 'stopped'}\n"
                f"{job_lines}\n\n"
                f"Reminder cache: {state.get('active_entries', 0)} active entries"
            )

        elif intent.action == "update_task":
            notion_id  = intent.parameters.get("resolved_notion_id")
            task_name  = intent.parameters.get("resolved_name", "task")
            new_status = intent.parameters.get("new_status")

            if not notion_id or not new_status:
                reply = "⚠️ Missing task details. Please try again."
            else:
                update_task_status(notion_id, new_status)
                reply = f"✅ <b>{task_name}</b> updated to <b>{new_status}</b> in Notion."

        elif intent.action == "add_task":
            task_name = intent.parameters.get("task_name", "")
            due_date  = intent.parameters.get("due_date")

            if not task_name:
                reply = "⚠️ No task name found. Please try again."
            else:
                add_task(task_name, due_date)
                due_str = f" (due {due_date})" if due_date else ""
                reply = f"✅ Task <b>{task_name}</b>{due_str} added to Notion."

        else:
            reply = "I don't know how to execute that action yet."

        await _send_reply(chat_id, ws, ctx, reply)

    except Exception as exc:
        log.exception("Action execution failed  action=%s", intent.action)
        await _send_reply(
            chat_id, ws, ctx,
            f"⚠️ Something went wrong running <b>{intent.action}</b>.\nError: {exc}"
        )


# ── Helper: send reply ────────────────────────────────────────────────────────

async def _send_reply(chat_id: str, ws: Workspace, ctx: ExecutionContext, text: str) -> None:
    """Send a reply to the specific chat_id and log it to message_history."""
    reply_ws = Workspace(
        workspace_id     = ws.workspace_id,
        notion_token     = ws.notion_token,
        notion_db_id     = ws.notion_db_id,
        telegram_token   = ws.telegram_token,
        telegram_chat_id = chat_id,
    )
    try:
        send_message(text, reply_ws, ctx)
        _log_message(chat_id, "out", text)
    except Exception as exc:
        log.error("Failed to send reply to chat_id=%s: %s", chat_id, exc)


# Fix missing Optional import
from typing import Optional
