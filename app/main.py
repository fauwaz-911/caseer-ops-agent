"""
Ops Agent — FastAPI application entry point.

Lifespan (startup → shutdown)
──────────────────────────────
1. setup_logging()          — configure console + rotating file handlers
2. start_scheduler()        — register and start the 3 background jobs
3. register_webhook()       — tell Telegram to POST updates to our URL
   └── on shutdown:
4. delete_webhook()         — unregister so Telegram stops sending updates
5. stop_scheduler()         — gracefully drain and stop background jobs

Why register_webhook() at startup?
────────────────────────────────────
The webhook URL includes our Render deployment URL. Every time Render
deploys, the URL stays the same but it's good practice to re-register
on startup to ensure it's pointing to the correct endpoint and secret.

Routing
───────
/             → root liveness ping
/health       → basic uptime check
/admin/*      → manual control endpoints (reminder, morning, etc.)
/webhook/*    → Telegram incoming message handler
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.config import settings
from app.logger import setup_logging, get_logger
from app.core.scheduler import start_scheduler, stop_scheduler
from app.api.admin_routes import router as admin_router
from app.api.webhook_routes import router as webhook_router, register_webhook, delete_webhook

_STARTUP_TIME: datetime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STARTUP_TIME

    # ── Startup ───────────────────────────────────────────────────────────────
    setup_logging(log_level=settings.LOG_LEVEL, log_dir=settings.LOG_DIR)
    log = get_logger(__name__)

    log.info(
        "Ops Agent v3 starting — workspace=%s  tz=%s",
        settings.WORKSPACE_ID, settings.SCHEDULER_TIMEZONE,
    )

    _STARTUP_TIME = datetime.now(timezone.utc)

    # Start background scheduler (morning brief, evening wrap-up, reminder engine)
    start_scheduler()

    # Register the Telegram webhook so incoming messages reach us
    try:
        register_webhook()
    except Exception as exc:
        # Don't crash on webhook registration failure — the rest of the system
        # still works (scheduled jobs, admin endpoints). Log and continue.
        log.error("Webhook registration failed at startup: %s", exc)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Ops Agent shutting down …")

    # NOTE: We intentionally do NOT delete the webhook on shutdown.
    # Render redeploys frequently — if we unregister on every shutdown,
    # messages are lost during the gap before the new instance registers.
    # Telegram queues messages while the server is down and delivers them
    # on the next successful POST, so leaving the webhook registered is correct.
    stop_scheduler()


# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ops Agent — AI Executive Assistant",
    description=(
        "Notion → AI Layer → Telegram. "
        "Workspace-first, multi-user ready, AI-powered."
    ),
    version="3.1.0",
    lifespan=lifespan,
)

app.include_router(admin_router)
app.include_router(webhook_router)


# ── Root endpoints ────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"])
def root():
    return {"status": "alive", "service": "ops-agent", "version": "3.1.0"}


@app.get("/health", tags=["meta"])
def health():
    uptime = (
        int((datetime.now(timezone.utc) - _STARTUP_TIME).total_seconds())
        if _STARTUP_TIME else 0
    )
    return {"status": "ok", "uptime_seconds": uptime}
