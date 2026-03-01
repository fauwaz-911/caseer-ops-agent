"""
Reminder Engine.

Urgency tiers (evaluated in priority order)
──────────────────────────────────────────
  🚨 OVERDUE      — past due
  ⚠️  CRITICAL     — due within 3 hours
  ⏳ DUE TODAY    — due within 24 hours
  📅 UPCOMING     — due within 48 hours

Features
────────
• Exception boundary — scheduler is never taken down by a bad run
• Separate Telegram dispatch per urgency tier for clarity
• Multi-user ready: pass chat_id to route alerts to different users
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .notion_client import Task, fetch_tasks
from .telegram import send_message
from .logger import get_logger

log = get_logger(__name__)


# ── Urgency thresholds (seconds) ─────────────────────────────────────────────
_OVERDUE   = 0
_CRITICAL  = 3  * 3600      # 3 h
_DUE_TODAY = 24 * 3600      # 24 h
_UPCOMING  = 48 * 3600      # 48 h


def _parse_iso(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Could not parse date string: %r", date_str)
        return None


def _classify(task: Task, now: datetime) -> Optional[str]:
    """Return urgency label or None if not actionable."""
    due = _parse_iso(task.due)
    if not due:
        return None

    # Ensure due is tz-aware
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)

    delta = (due - now).total_seconds()

    if delta < _OVERDUE:
        return "overdue"
    if delta <= _CRITICAL:
        return "critical"
    if delta <= _DUE_TODAY:
        return "due_today"
    if delta <= _UPCOMING:
        return "upcoming"
    return None


def build_reminders(tasks: list[Task]) -> dict[str, list[Task]]:
    """Classify tasks into urgency buckets."""
    now = datetime.now(timezone.utc)
    buckets: dict[str, list[Task]] = {
        "overdue":   [],
        "critical":  [],
        "due_today": [],
        "upcoming":  [],
    }
    for task in tasks:
        label = _classify(task, now)
        if label:
            buckets[label].append(task)
    return buckets


def _format_bucket(emoji: str, heading: str, tasks: list[Task]) -> str:
    header = f"{emoji} <b>{heading}</b>"
    lines = [header]
    for t in tasks:
        lines.append(f"  • {t.name}" + (f" (due {t.due})" if t.due else ""))
    return "\n".join(lines)


def run_reminder_engine(
    db_id: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> None:
    """
    Fetch tasks, classify them, and fire Telegram alerts.

    Parameters
    ----------
    db_id   : Override Notion database (multi-user).
    chat_id : Override Telegram chat (multi-user).
    """
    log.info("Reminder engine starting — db_id=%s  chat_id=%s", db_id, chat_id)

    try:
        tasks = fetch_tasks(db_id=db_id)
        buckets = build_reminders(tasks)

        sections = []
        if buckets["overdue"]:
            sections.append(
                _format_bucket("🚨", "OVERDUE TASKS", buckets["overdue"])
            )
        if buckets["critical"]:
            sections.append(
                _format_bucket("⚠️", "DUE WITHIN 3 HOURS", buckets["critical"])
            )
        if buckets["due_today"]:
            sections.append(
                _format_bucket("⏳", "DUE TODAY (24 h)", buckets["due_today"])
            )
        if buckets["upcoming"]:
            sections.append(
                _format_bucket("📅", "UPCOMING (48 h)", buckets["upcoming"])
            )

        if not sections:
            log.info("Reminder engine: no actionable tasks — skipping Telegram dispatch")
            return

        message = "🧠 <b>Smart Task Alerts</b>\n\n" + "\n\n".join(sections)
        send_message(message, chat_id=chat_id)
        log.info(
            "Reminder engine dispatched: overdue=%d  critical=%d  today=%d  upcoming=%d",
            len(buckets["overdue"]), len(buckets["critical"]),
            len(buckets["due_today"]), len(buckets["upcoming"]),
        )

    except Exception as exc:                        # exception boundary
        log.exception("Reminder engine encountered an unhandled error: %s", exc)
