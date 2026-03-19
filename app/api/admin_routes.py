"""
Admin API routes.

Every endpoint that triggers a job creates its own ExecutionContext so
log lines for that request are fully correlated and distinguishable from
scheduler-triggered runs.

Endpoints
─────────
  GET /admin/health          — service status + scheduler + state summary
  GET /admin/force-reminder  — trigger reminder engine
  GET /admin/force-morning   — trigger morning brief
  GET /admin/force-evening   — trigger evening wrap-up
  GET /admin/send-update     — push plain task list to Telegram
  GET /admin/test-telegram   — connectivity ping
  DELETE /admin/clear-state  — wipe idempotency cache
"""

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timezone

from app.models.workspace import Workspace
from app.core.execution_context import ExecutionContext
from app.core.scheduler import get_scheduler_status
from app.core.exceptions import NotionError, TelegramError
from app.services.reminder_service import (
    run_reminder_engine,
    run_morning_brief,
    run_evening_wrapup,
)
from app.services.notion_service import fetch_tasks
from app.services.telegram_service import send_message
from app.state.reminder_state import get_state_summary, clear_state
from app.logger import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])



def _workspace(
    notion_db_id: str | None = None,
    telegram_chat_id: str | None = None,
) -> Workspace:
    """Build a workspace, optionally overriding DB or chat for multi-user routing."""
    ws = Workspace.from_settings()
    if notion_db_id or telegram_chat_id:
        # dataclass is frozen — create a new one with overrides
        ws = Workspace(
            workspace_id     = ws.workspace_id,
            notion_token     = ws.notion_token,
            notion_db_id     = notion_db_id     or ws.notion_db_id,
            telegram_token   = ws.telegram_token,
            telegram_chat_id = telegram_chat_id or ws.telegram_chat_id,
        )
    return ws


@router.get("/health")
def health():
    """Full service health check."""
    return {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scheduler": get_scheduler_status(),
        "state":     get_state_summary(),
    }


@router.get("/force-reminder")
def force_reminder(
    chat_id: str | None = Query(default=None, description="Override Telegram chat ID"),
    db_id:   str | None = Query(default=None, description="Override Notion DB ID"),
):
    """Manually run the full reminder engine."""
    ws  = _workspace(notion_db_id=db_id, telegram_chat_id=chat_id)
    ctx = ExecutionContext.new(job="force_reminder")
    result = run_reminder_engine(ws, ctx)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/force-morning")
def force_morning(
    chat_id: str | None = Query(default=None),
    db_id:   str | None = Query(default=None),
):
    """Manually trigger the morning brief."""
    ws  = _workspace(notion_db_id=db_id, telegram_chat_id=chat_id)
    ctx = ExecutionContext.new(job="force_morning")
    result = run_morning_brief(ws, ctx)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/force-evening")
def force_evening(
    chat_id: str | None = Query(default=None),
    db_id:   str | None = Query(default=None),
):
    """Manually trigger the evening wrap-up."""
    ws  = _workspace(notion_db_id=db_id, telegram_chat_id=chat_id)
    ctx = ExecutionContext.new(job="force_evening")
    result = run_evening_wrapup(ws, ctx)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/send-update")
def send_update(
    chat_id: str | None = Query(default=None),
    db_id:   str | None = Query(default=None),
):
    """Fetch all tasks and push a numbered summary to Telegram."""
    ws  = _workspace(notion_db_id=db_id, telegram_chat_id=chat_id)
    ctx = ExecutionContext.new(job="send_update")
    log_ctx = ctx.logger(__name__)
    try:
        tasks = fetch_tasks(ws, ctx)
        if tasks:
            lines = [f"  {i}. {t.name}" + (f" — {t.due_iso()}" if t.due else "")
                     for i, t in enumerate(tasks, 1)]
            body = "\n".join(lines)
        else:
            body = "  No tasks found."
        message = f"🔔 <b>Ops Update</b>\n\n{body}"
        send_message(message, ws, ctx)
        log_ctx.info("send-update dispatched  tasks=%d", len(tasks))
        return {"status": "sent", "tasks": len(tasks), "execution_id": ctx.execution_id}
    except NotionError as exc:
        raise HTTPException(status_code=502, detail=f"Notion error: {exc}")
    except TelegramError as exc:
        raise HTTPException(status_code=502, detail=f"Telegram error: {exc}")


@router.get("/test-telegram")
def test_telegram(chat_id: str | None = Query(default=None)):
    """Send a connectivity test ping through the Telegram pipeline."""
    ws  = _workspace(telegram_chat_id=chat_id)
    ctx = ExecutionContext.new(job="test_telegram")
    try:
        result = send_message("✅ <b>Ops Agent</b> — pipeline check OK", ws, ctx)
        return {"status": "sent", "execution_id": ctx.execution_id, "telegram": result}
    except TelegramError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.delete("/clear-state")
def admin_clear_state():
    """Wipe the idempotency cache. All reminders will re-fire on next cycle."""
    clear_state()
    return {"status": "cleared"}
@router.get("/debug-notion", tags=["admin"])
def debug_notion():
    import requests
    from app.config import settings

    url = f"https://api.notion.com/v1/databases/{settings.NOTION_TASKS_DB_ID}/query"
    headers = {
        "Authorization":  f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
        "Content-Type":   "application/json",
    }
    response = requests.post(url, json={"page_size": 3}, headers=headers, timeout=15)
    data = response.json()

    results = data.get("results", [])
    if not results:
        return {"status": response.status_code, "total": 0, "raw": data}

    first = results[0]
    props = first.get("properties", {})
    return {
        "http_status":     response.status_code,
        "total_returned":  len(results),
        "property_names":  list(props.keys()),
        "property_types":  {k: v.get("type") for k, v in props.items()},
        "raw_properties":  props,
    }
```

Push, deploy, then hit:
```
GET https://caseer-ops-agent.onrender.com/admin/debug-notion
