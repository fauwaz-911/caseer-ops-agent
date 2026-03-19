"""
Ops Agent — FastAPI application entry point.

Startup sequence
────────────────
1. setup_logging()    — configure console + rotating file handlers
2. create_tables()    — create DB tables if they don't exist (idempotent)
3. start_scheduler()  — register and start background jobs
4. register_webhook() — tell Telegram to POST updates to our URL

Shutdown sequence
─────────────────
5. stop_scheduler()   — gracefully stop background jobs
   (webhook intentionally NOT deleted — survives redeploys)
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.config import settings
from app.logger import setup_logging, get_logger
from app.core.scheduler import start_scheduler, stop_scheduler
from app.api.admin_routes import router as admin_router
from app.api.webhook_routes import router as webhook_router, register_webhook

_STARTUP_TIME: datetime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STARTUP_TIME

    setup_logging(log_level=settings.LOG_LEVEL, log_dir=settings.LOG_DIR)
    log = get_logger(__name__)

    log.info(
        "Ops Agent v3 starting — workspace=%s  tz=%s  model=%s  provider=%s",
        settings.WORKSPACE_ID,
        settings.SCHEDULER_TIMEZONE,
        settings.GROQ_MODEL if settings.AI_PROVIDER == "groq" else settings.GEMINI_MODEL,
        settings.AI_PROVIDER,
    )

    _STARTUP_TIME = datetime.now(timezone.utc)

    # Create database tables (idempotent — safe to run on every startup)
    try:
        from app.db.database import create_tables
        create_tables()
    except Exception as exc:
        log.error("Database setup failed: %s", exc)
        raise   # Don't start if DB is unavailable — all state ops would fail

    start_scheduler()

    try:
        register_webhook()
    except Exception as exc:
        log.error("Webhook registration failed at startup (set it manually): %s", exc)

    yield

    log.info("Ops Agent shutting down …")
    stop_scheduler()


app = FastAPI(
    title="Ops Agent — AI Executive Assistant",
    description="Notion → AI → Telegram. Groq/Gemini. PostgreSQL state.",
    version="3.2.0",
    lifespan=lifespan,
)

app.include_router(admin_router)
app.include_router(webhook_router)


@app.get("/", tags=["meta"])
def root():
    return {"status": "alive", "service": "ops-agent", "version": "3.2.0"}


@app.get("/health", tags=["meta"])
def health():
    uptime = (
        int((datetime.now(timezone.utc) - _STARTUP_TIME).total_seconds())
        if _STARTUP_TIME else 0
    )
    return {"status": "ok", "uptime_seconds": uptime}
