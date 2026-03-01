"""
AI Executive Assistant — FastAPI application entry point.

Endpoints
─────────
  GET /                  → root liveness ping
  GET /health            → service status + scheduler info
  GET /force-reminder    → manually trigger reminder engine
  GET /force-morning     → manually trigger morning brief
  GET /force-evening     → manually trigger evening wrap-up
  GET /send-update       → fetch tasks and dispatch a task summary
  GET /test-telegram     → pipeline connectivity test

All manual trigger endpoints accept an optional `chat_id` query
parameter so you can route messages to specific users without
redeploying.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import settings
from .logger import get_logger
from .scheduler import start_scheduler, stop_scheduler, get_scheduler_status
from .notion_client import fetch_tasks
from .telegram import send_message, send_test_ping
from .reminder_engine import run_reminder_engine

log = get_logger(__name__)

_STARTUP_TIME = datetime.now(timezone.utc)


# ── Lifespan (replaces deprecated @app.on_event) ─────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Ops Agent starting up …")
    start_scheduler()
    yield
    log.info("Ops Agent shutting down …")
    stop_scheduler()


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Executive Assistant",
    description="Modular orchestration layer: Notion → Reminder Engine → Telegram",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"])
def root():
    return {"status": "alive", "service": "ops-agent"}


@app.get("/health", tags=["meta"])
def health():
    uptime = (datetime.now(timezone.utc) - _STARTUP_TIME).total_seconds()
    return {
        "status": "ok",
        "uptime_seconds": round(uptime),
        "scheduler": get_scheduler_status(),
    }


@app.get("/test-telegram", tags=["control"])
def test_telegram(chat_id: str | None = Query(default=None, description="Override target chat ID")):
    """Send a test ping through the Telegram pipeline."""
    try:
        result = send_test_ping(chat_id=chat_id)
        log.info("/test-telegram → success")
        return {"status": "sent", "telegram": result}
    except RuntimeError as exc:
        log.error("/test-telegram → failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/force-reminder", tags=["control"])
def force_reminder(
    chat_id: str | None = Query(default=None),
    db_id:   str | None = Query(default=None),
):
    """Manually trigger the full reminder engine."""
    try:
        run_reminder_engine(db_id=db_id, chat_id=chat_id)
        log.info("/force-reminder → executed")
        return {"status": "executed"}
    except Exception as exc:
        log.exception("/force-reminder → error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/force-morning", tags=["control"])
def force_morning(chat_id: str | None = Query(default=None)):
    """Manually fire the morning brief job."""
    from .scheduler import morning_job
    try:
        morning_job()
        return {"status": "morning brief sent"}
    except Exception as exc:
        log.exception("/force-morning → error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/force-evening", tags=["control"])
def force_evening(chat_id: str | None = Query(default=None)):
    """Manually fire the evening wrap-up job."""
    from .scheduler import evening_job
    try:
        evening_job()
        return {"status": "evening wrap-up sent"}
    except Exception as exc:
        log.exception("/force-evening → error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/send-update", tags=["control"])
def send_update(
    chat_id: str | None = Query(default=None),
    db_id:   str | None = Query(default=None),
):
    """Fetch tasks from Notion and push a formatted summary to Telegram."""
    try:
        tasks = fetch_tasks(db_id=db_id)
        if not tasks:
            message = "🔔 <b>Ops Update</b>\n\nNo tasks found."
        else:
            lines = [f"  {i}. {t.name}" + (f" — due {t.due}" if t.due else "")
                     for i, t in enumerate(tasks, 1)]
            message = "🔔 <b>Ops Update</b>\n\n" + "\n".join(lines)

        send_message(message, chat_id=chat_id)
        log.info("/send-update → %d tasks dispatched", len(tasks))
        return {"status": "sent", "task_count": len(tasks)}

    except RuntimeError as exc:
        log.error("/send-update Telegram error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        log.exception("/send-update unexpected error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
