"""
Ops Agent — FastAPI application entry point.

Lifespan
────────
Uses the modern asynccontextmanager lifespan pattern (not the deprecated
@app.on_event). Startup: configure logging → validate settings → start
scheduler. Shutdown: stop scheduler gracefully.

Routing
───────
All control endpoints live under /admin (see api/admin_routes.py).
Root and liveness endpoints are here.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app.config import settings
from app.logger import setup_logging, get_logger
from app.core.scheduler import start_scheduler, stop_scheduler
from app.api.admin_routes import router as admin_router

_STARTUP_TIME: datetime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STARTUP_TIME
    # ── Startup ───────────────────────────────────────────────────────────────
    setup_logging(log_level=settings.LOG_LEVEL, log_dir=settings.LOG_DIR)
    log = get_logger(__name__)
    log.info(
        "Ops Agent starting — workspace=%s  tz=%s",
        settings.WORKSPACE_ID, settings.SCHEDULER_TIMEZONE,
    )
    _STARTUP_TIME = datetime.now(timezone.utc)
    start_scheduler()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Ops Agent shutting down …")
    stop_scheduler()


app = FastAPI(
    title="Ops Agent — AI Executive Assistant",
    description="Notion → Reminder Engine → Telegram. Workspace-first, multi-user ready.",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(admin_router)


@app.get("/", tags=["meta"])
def root():
    return {"status": "alive", "service": "ops-agent", "version": "3.0.0"}


@app.get("/health", tags=["meta"])
def health():
    uptime = (
        int((datetime.now(timezone.utc) - _STARTUP_TIME).total_seconds())
        if _STARTUP_TIME else 0
    )
    return {"status": "ok", "uptime_seconds": uptime}
