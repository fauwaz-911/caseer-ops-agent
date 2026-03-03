"""
APScheduler configuration.

Jobs
────
  morning_brief   — cron (env-configurable, default 10:00)
  evening_wrapup  — cron (env-configurable, default 18:00)
  reminder_engine — interval (env-configurable, default every 30 min)

Hardening
─────────
• Each job function is wrapped in a full try/except — one bad run never
  takes down the scheduler thread.
• APScheduler event listeners log every job execution and every error,
  including the exception traceback, for full observability.
• misfire_grace_time gives the scheduler a 5-minute window to catch up
  after a cold start or temporary downtime.
• Scheduler is idempotent — calling start_scheduler() twice is safe.
• get_scheduler_status() is called by the /health endpoint.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from app.config import settings
from app.models.workspace import Workspace
from app.core.execution_context import ExecutionContext
from app.services.reminder_service import (
    run_reminder_engine,
    run_morning_brief,
    run_evening_wrapup,
)
from app.logger import get_logger

log = get_logger(__name__)
_scheduler: BackgroundScheduler | None = None


# ── Job wrappers (exception boundaries) ─────────────────────────────────────

def _morning_job() -> None:
    try:
        ws  = Workspace.from_settings()
        ctx = ExecutionContext.new(job="morning_brief")
        run_morning_brief(ws, ctx)
    except Exception:
        log.exception("morning_job raised an unhandled exception")


def _evening_job() -> None:
    try:
        ws  = Workspace.from_settings()
        ctx = ExecutionContext.new(job="evening_wrapup")
        run_evening_wrapup(ws, ctx)
    except Exception:
        log.exception("evening_job raised an unhandled exception")


def _reminder_job() -> None:
    try:
        ws  = Workspace.from_settings()
        ctx = ExecutionContext.new(job="reminder_engine")
        run_reminder_engine(ws, ctx)
    except Exception:
        log.exception("reminder_job raised an unhandled exception")


# ── APScheduler listeners ────────────────────────────────────────────────────

def _on_job_executed(event) -> None:
    log.debug("Scheduler job finished  id=%s", event.job_id)


def _on_job_error(event) -> None:
    log.error(
        "Scheduler job error  id=%s  exception=%s",
        event.job_id, event.exception,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Initialise and start the background scheduler. Idempotent."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("start_scheduler called but scheduler is already running.")
        return

    _scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    _scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
    _scheduler.add_listener(_on_job_error,    EVENT_JOB_ERROR)

    _scheduler.add_job(
        _morning_job,
        trigger="cron",
        hour=settings.MORNING_HOUR,
        minute=settings.MORNING_MINUTE,
        id="morning_brief",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _evening_job,
        trigger="cron",
        hour=settings.EVENING_HOUR,
        minute=settings.EVENING_MINUTE,
        id="evening_wrapup",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _reminder_job,
        trigger="interval",
        minutes=settings.REMINDER_INTERVAL_MINUTES,
        id="reminder_engine",
        replace_existing=True,
    )

    _scheduler.start()
    log.info(
        "Scheduler started  tz=%s  morning=%02d:%02d  evening=%02d:%02d  interval=%dm",
        settings.SCHEDULER_TIMEZONE,
        settings.MORNING_HOUR, settings.MORNING_MINUTE,
        settings.EVENING_HOUR, settings.EVENING_MINUTE,
        settings.REMINDER_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    """Gracefully shut down. Called from app lifespan on shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")


def get_scheduler_status() -> dict:
    """Return job metadata for the /health endpoint."""
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}
    return {
        "running": True,
        "jobs": [
            {
                "id":       job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "trigger":  str(job.trigger),
            }
            for job in _scheduler.get_jobs()
        ],
    }
