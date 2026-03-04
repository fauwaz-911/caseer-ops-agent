"""
APScheduler setup.

Jobs
────
  morning_job   — cron (configurable, default 10:00 UTC)
  evening_job   — cron (configurable, default 18:00 UTC)
  reminder_job  — interval (configurable, default every 30 min)

Design choices
──────────────
• Each job is wrapped in a try/except so one failing job never
  crashes the entire scheduler thread.
• All timing comes from config.settings — no magic numbers here.
• Logging at job entry and exit for full observability.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from .config import settings
from .notion_client import fetch_tasks
from .telegram import send_message
from .reminder_engine import run_reminder_engine
from .logger import get_logger

log = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None

scheduler.add_job(
    _ping_self,
    trigger="interval",
    minutes=10,
    id="keepalive",
)

def _ping_self():
    import requests
    requests.get(f"{settings.WEBHOOK_BASE_URL}/health", timeout=5)

# ── Job implementations ───────────────────────────────────────────────────────

def morning_job() -> None:
    log.info("morning_job triggered")
    try:
        tasks = fetch_tasks()
        if tasks:
            lines = [f"  • {t.name}" + (f" — due {t.due}" if t.due else "") for t in tasks]
            body = "\n".join(lines)
        else:
            body = "  No tasks found for today."

        send_message(f"🌅 <b>Morning Brief</b>\n\n{body}")
        log.info("morning_job completed — %d tasks dispatched", len(tasks))
    except Exception as exc:
        log.exception("morning_job failed: %s", exc)


def evening_job() -> None:
    log.info("evening_job triggered")
    try:
        tasks = fetch_tasks()
        if tasks:
            lines = [f"  • {t.name}" + (f" — due {t.due}" if t.due else "") for t in tasks]
            body = "\n".join(lines)
        else:
            body = "  All clear — no open tasks."

        send_message(f"🌙 <b>Evening Wrap-up</b>\n\n{body}")
        log.info("evening_job completed — %d tasks dispatched", len(tasks))
    except Exception as exc:
        log.exception("evening_job failed: %s", exc)


def reminder_job() -> None:
    """Interval job: wraps the reminder engine (already has its own boundary)."""
    log.debug("reminder_job triggered")
    run_reminder_engine()


# ── APScheduler event listeners ───────────────────────────────────────────────

def _on_job_error(event) -> None:
    log.error(
        "Scheduler job error — job_id=%s  exception=%s",
        event.job_id, event.exception,
    )


def _on_job_executed(event) -> None:
    log.debug("Scheduler job finished — job_id=%s  retval=%s", event.job_id, event.retval)


# ── Public API ────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Initialise and start the background scheduler. Idempotent."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("start_scheduler called but scheduler is already running — skipping")
        return

    _scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)

    # Attach event listeners
    _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    _scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)

    # Morning brief
    _scheduler.add_job(
        morning_job,
        trigger="cron",
        hour=settings.morning_hour,
        minute=settings.morning_minute,
        id="morning_brief",
        replace_existing=True,
        misfire_grace_time=300,     # 5 min window if server was briefly down
    )

    # Evening wrap-up
    _scheduler.add_job(
        evening_job,
        trigger="cron",
        hour=settings.evening_hour,
        minute=settings.evening_minute,
        id="evening_wrapup",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Interval reminder engine
    _scheduler.add_job(
        reminder_job,
        trigger="interval",
        minutes=settings.reminder_interval_minutes,
        id="reminder_engine",
        replace_existing=True,
    )

    _scheduler.start()
    log.info(
        "Scheduler started — jobs: morning=%02d:%02d  evening=%02d:%02d  "
        "reminder_interval=%dm  tz=%s",
        settings.morning_hour, settings.morning_minute,
        settings.evening_hour, settings.evening_minute,
        settings.reminder_interval_minutes,
        settings.scheduler_timezone,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler (called on app shutdown)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")


def get_scheduler_status() -> dict:
    """Return a summary of scheduled jobs for the /health endpoint."""
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"running": True, "jobs": jobs}
