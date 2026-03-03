"""
Telegram Webhook — the entry point for all incoming messages.

How Telegram webhooks work
──────────────────────────
Instead of polling (asking Telegram "any new messages?" every few seconds),
a webhook means Telegram calls YOUR server the moment a message arrives.
You give Telegram a URL, and it sends a POST request to that URL for every
update (message, button press, etc.).

This file registers that URL on startup and handles incoming updates.

Message flow
────────────
Telegram → POST /webhook/telegram → extract message text + chat_id
                                   → check if this is a confirmation reply
                                       YES → execute the pending action
                                       NO  → parse intent via AI
                                           → if actionable: ask for confirmation
                                           → if free_response: reply directly
                                           → if unknown: ask to clarify

Confirmation flow (the key design)
───────────────────────────────────
User: "check my tasks"
Bot:  "🤔 You want me to run the reminder engine. Confirm? (yes / no)"
User: "yes"
Bot:  [runs reminder engine and reports result]

Scheduled jobs (morning_brief, evening_brief, reminder_engine) bypass
this entirely — they run automatically as instructed by the schedule.
Only messages you send manually go through the confirmation gate.

Security
────────
• We validate the X-Telegram-Bot-Api-Secret-Token header on every request
• Only allow-listed chat IDs can interact (set in TELEGRAM_CHAT_ID)
  — prevents strangers from controlling your bot if they find your URL
"""

import requests as http_requests
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from app.config import settings
from app.models.intent import Intent
from app.models.workspace import Workspace
from app.core.execution_context import ExecutionContext
from app.services.ai_service import parse_intent, free_response
from app.services.reminder_service import (
    run_reminder_engine,
    run_morning_brief,
    run_evening_wrapup,
)
from app.services.telegram_service import send_message
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


# ── Telegram API helpers ──────────────────────────────────────────────────────

def _telegram_api(method: str, payload: dict) -> dict:
    """Low-level call to the Telegram Bot API. Used for setup operations."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    response = http_requests.post(url, json=payload, timeout=10)
    return response.json()


def register_webhook() -> None:
    """
    Tell Telegram where to send updates.

    Called once at application startup (from main.py lifespan).
    Telegram will POST every incoming message to this URL from now on.

    The secret_token is included in the X-Telegram-Bot-Api-Secret-Token
    header of every update — we use it to verify requests are really
    from Telegram and not someone else hitting our endpoint.
    """
    webhook_url = f"{settings.WEBHOOK_BASE_URL}/webhook/telegram"
    result = _telegram_api("setWebhook", {
        "url":          webhook_url,
        "secret_token": settings.WEBHOOK_SECRET,
        # Only process message updates — ignore unneeded update types
        "allowed_updates": ["message"],
    })
    if result.get("ok"):
        log.info("Telegram webhook registered → %s", webhook_url)
    else:
        log.error("Failed to register Telegram webhook: %s", result)


def delete_webhook() -> None:
    """
    Unregister the webhook — called on app shutdown.
    Stops Telegram from sending updates to a server that's no longer running.
    """
    result = _telegram_api("deleteWebhook", {})
    if result.get("ok"):
        log.info("Telegram webhook deleted.")
    else:
        log.warning("Could not delete Telegram webhook: %s", result)


# ── Incoming update handler ───────────────────────────────────────────────────

@router.post("/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive a Telegram update and process it.

    Telegram sends a JSON body with the message. We:
    1. Validate the secret token header
    2. Extract the chat_id and message text
    3. Check if this is a confirmation reply (yes/no)
    4. Otherwise parse intent with AI and handle accordingly

    We always return HTTP 200 to Telegram immediately — if we return
    anything else, Telegram will retry the update repeatedly.
    Processing happens in a background task so Telegram doesn't time out.
    """
    # ── Step 1: Validate secret token ────────────────────────────────────────
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.WEBHOOK_SECRET:
        log.warning("Webhook received with invalid secret token — ignoring.")
        # Still return 200 — we don't want Telegram to retry
        return {"ok": True}

    # ── Step 2: Parse the update body ────────────────────────────────────────
    body = await request.json()
    message = body.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = message.get("text", "").strip()

    if not chat_id or not text:
        # Ignore updates without a chat_id or text (e.g. photo messages)
        return {"ok": True}

    log.info("Webhook message received  chat_id=%s  text=%r", chat_id, text[:80])

    # ── Step 3: Security — only accept messages from the authorised chat ──────
    # This prevents anyone who finds your webhook URL from controlling the bot
    if chat_id != settings.TELEGRAM_CHAT_ID:
        log.warning("Message from unauthorised chat_id=%s — ignoring.", chat_id)
        return {"ok": True}

    # ── Step 4: Process in background so we return 200 quickly ───────────────
    background_tasks.add_task(_process_message, chat_id=chat_id, text=text)
    return {"ok": True}


# ── Message processor (runs in background) ────────────────────────────────────

async def _process_message(chat_id: str, text: str) -> None:
    """
    The full message processing pipeline.

    Runs as a background task after the webhook has returned 200.
    This is where the confirmation flow and intent routing happen.
    """
    ws  = Workspace.from_settings()
    ctx = ExecutionContext.new(job="webhook")
    log_ctx = ctx.logger(__name__)

    # ── Check if user is responding to a pending confirmation ─────────────────
    pending_intent = get_pending(chat_id)
    if pending_intent:
        if is_confirmation(text):
            # User said yes — execute the action they originally asked for
            log_ctx.info(
                "Confirmation received  action=%s  chat_id=%s",
                pending_intent.action, chat_id,
            )
            clear_pending(chat_id)
            await _execute_action(pending_intent, ws, ctx, chat_id)

        elif is_cancellation(text):
            # User said no — cancel and acknowledge
            clear_pending(chat_id)
            log_ctx.info("Action cancelled by user  action=%s", pending_intent.action)
            await _send_reply(chat_id, ws, ctx, "❌ Cancelled. Let me know if you need anything else.")

        else:
            # User sent something else while we were waiting for yes/no
            # Treat it as a new message — clear the old pending action first
            log_ctx.info(
                "New message received while waiting for confirmation — "
                "clearing pending action=%s", pending_intent.action,
            )
            clear_pending(chat_id)
            await _handle_new_message(text, chat_id, ws, ctx)
        return

    # ── No pending confirmation — handle as a fresh message ───────────────────
    await _handle_new_message(text, chat_id, ws, ctx)


async def _handle_new_message(
    text: str,
    chat_id: str,
    ws: Workspace,
    ctx: ExecutionContext,
) -> None:
    """
    Parse the user's intent and either:
    - Ask for confirmation (if the intent maps to a system action)
    - Reply directly (if it's a free_response or unknown)
    """
    log_ctx = ctx.logger(__name__)

    # ── Parse intent through AI ───────────────────────────────────────────────
    intent = parse_intent(text)
    log_ctx.info(
        "Intent resolved  action=%s  confidence=%.2f",
        intent.action, intent.confidence,
    )

    # ── Route based on intent type ────────────────────────────────────────────

    if intent.action == "free_response":
        # AI already generated a reply — just send it
        reply = intent.ai_reply or free_response(text)
        await _send_reply(chat_id, ws, ctx, reply)

    elif intent.action == "unknown":
        # AI couldn't understand — send the clarification message
        reply = intent.ai_reply or (
            "I didn't quite understand that. You can ask me to:\n"
            "• Check urgent tasks\n"
            "• Send the morning or evening brief\n"
            "• Show current task list\n"
            "• Check system status"
        )
        await _send_reply(chat_id, ws, ctx, reply)

    elif intent.is_actionable and intent.requires_confirmation:
        # User wants a system action — ask for confirmation first
        confirmation_message = _build_confirmation_message(intent)
        store_pending(chat_id, intent)
        await _send_reply(chat_id, ws, ctx, confirmation_message)
        log_ctx.info(
            "Confirmation requested  action=%s  chat_id=%s",
            intent.action, chat_id,
        )

    else:
        # Shouldn't normally reach here, but handle gracefully
        await _send_reply(chat_id, ws, ctx, "I'm not sure what to do with that.")


async def _execute_action(
    intent: Intent,
    ws: Workspace,
    ctx: ExecutionContext,
    chat_id: str,
) -> None:
    """
    Execute the confirmed action and send a result message back.

    Each case calls the appropriate service function and reports
    the outcome back to the user.
    """
    log_ctx = ctx.logger(__name__)
    log_ctx.info("Executing confirmed action  action=%s", intent.action)

    try:
        if intent.action == "force_reminder":
            result = run_reminder_engine(ws, ctx)
            sent = result.get("alerts_sent", 0)
            reply = (
                f"✅ Reminder engine ran.\n"
                f"Alerts sent: {sent}"
                if sent > 0
                else "✅ Reminder engine ran. No new alerts — all clear."
            )

        elif intent.action == "morning_brief":
            result = run_morning_brief(ws, ctx)
            reply = "✅ Morning brief sent." if result.get("status") == "sent" else f"❌ Failed: {result.get('error')}"

        elif intent.action == "evening_brief":
            result = run_evening_wrapup(ws, ctx)
            reply = "✅ Evening wrap-up sent." if result.get("status") == "sent" else f"❌ Failed: {result.get('error')}"

        elif intent.action == "send_update":
            # Import here to avoid circular import
            from app.services.notion_service import fetch_tasks
            tasks = fetch_tasks(ws, ctx)
            if tasks:
                lines = [f"  {i}. {t.name}" + (f" — {t.due_iso()}" if t.due else "")
                         for i, t in enumerate(tasks, 1)]
                body = "\n".join(lines)
            else:
                body = "  No tasks found."
            msg = f"🔔 <b>Ops Update</b>\n\n{body}"
            send_message(msg, ws, ctx)
            reply = f"✅ Task update sent. ({len(tasks)} tasks)"

        elif intent.action == "clear_state":
            clear_state()
            reply = "✅ Reminder state cleared. All alerts will re-fire on the next cycle."

        elif intent.action == "test_telegram":
            send_message("✅ <b>Ops Agent</b> — pipeline check OK", ws, ctx)
            reply = "✅ Telegram test sent successfully."

        elif intent.action == "status":
            sched  = get_scheduler_status()
            state  = get_state_summary()
            jobs   = sched.get("jobs", [])
            job_lines = "\n".join(
                f"  • {j['id']}: next run {j['next_run']}" for j in jobs
            ) or "  No jobs found."
            reply = (
                f"🟢 <b>System Status</b>\n\n"
                f"Scheduler: {'running' if sched.get('running') else 'stopped'}\n"
                f"{job_lines}\n\n"
                f"Reminder state: {state.get('active_entries', 0)} active entries"
            )
            # Status is informational — send directly without going through send_message
            # (it's already formatted for this chat)

        else:
            reply = "I don't know how to execute that action yet."

        await _send_reply(chat_id, ws, ctx, reply)

    except Exception as exc:
        log_ctx.exception("Action execution failed  action=%s  error=%s", intent.action, exc)
        await _send_reply(
            chat_id, ws, ctx,
            f"⚠️ Something went wrong while running <b>{intent.action}</b>.\nError: {exc}"
        )


# ── Helper: build the confirmation prompt ────────────────────────────────────

# Human-readable descriptions of each action for the confirmation message
_ACTION_DESCRIPTIONS = {
    "force_reminder":  "run the reminder engine and check for urgent tasks",
    "morning_brief":   "send the morning task brief",
    "evening_brief":   "send the evening wrap-up",
    "send_update":     "send the current full task list",
    "clear_state":     "clear all reminder state (alerts will re-fire)",
    "test_telegram":   "send a Telegram connectivity test",
    "status":          "report system status",
}


def _build_confirmation_message(intent: Intent) -> str:
    """
    Build the confirmation prompt the user sees before an action runs.

    Shows:
    - What the system understood
    - How confident it is
    - A clear yes/no prompt
    """
    description = _ACTION_DESCRIPTIONS.get(intent.action, intent.action)
    confidence_pct = int(intent.confidence * 100)

    return (
        f"🤔 I understood you want me to <b>{description}</b>.\n"
        f"Confidence: {confidence_pct}%\n\n"
        f"Shall I proceed? Reply <b>yes</b> to confirm or <b>no</b> to cancel."
    )


# ── Helper: send a reply to the user's chat ──────────────────────────────────

async def _send_reply(chat_id: str, ws: Workspace, ctx: ExecutionContext, text: str) -> None:
    """
    Send a message back to the user's specific chat_id.

    We create a temporary workspace override so the message goes to the
    chat that sent the webhook, not necessarily the default TELEGRAM_CHAT_ID.
    This is important when multi-user support is added.
    """
    # Override the chat_id for this reply
    reply_ws = Workspace(
        workspace_id     = ws.workspace_id,
        notion_token     = ws.notion_token,
        notion_db_id     = ws.notion_db_id,
        telegram_token   = ws.telegram_token,
        telegram_chat_id = chat_id,          # route to the sender
    )
    try:
        send_message(text, reply_ws, ctx)
    except Exception as exc:
        log.error("Failed to send reply to chat_id=%s: %s", chat_id, exc)
