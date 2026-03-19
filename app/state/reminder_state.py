"""
Reminder state — database-backed idempotency cache.

Replaces logs/reminder_state.json with a PostgreSQL table.

Why this matters
────────────────
Previously reminder state was stored in a JSON file that got wiped on
every Render redeploy. This meant every deploy caused a flood of
duplicate alerts. The database version survives restarts, redeploys,
and sleep/wake cycles on Render's free tier.

Logic is identical to the file-based version:
  • already_sent(task_name, label) → True if alert was sent and not expired
  • mark_sent(task_name, label)    → record the alert with a TTL
  • clear_state()                  → wipe all entries (admin action)

TTL
───
Entries expire after ENTRY_TTL_HOURS (default 24). After expiry, the
same task will re-alert on the next reminder cycle.
"""

from datetime import datetime, timezone, timedelta
from app.db.database import get_db
from app.db.models import ReminderState
from app.logger import get_logger

log = get_logger(__name__)

# How long before a sent alert becomes eligible to re-fire
ENTRY_TTL_HOURS = 24


def already_sent(task_name: str, label: str) -> bool:
    """
    Return True if this (task_name, label) pair was alerted recently.

    Checks for a non-expired row in the reminder_state table.
    Expired rows are treated as if they don't exist.
    """
    now = datetime.now(timezone.utc)
    with get_db() as db:
        row = (
            db.query(ReminderState)
            .filter(
                ReminderState.task_name == task_name,
                ReminderState.label     == label,
                ReminderState.expires_at > now,   # only count non-expired
            )
            .first()
        )
    return row is not None


def mark_sent(task_name: str, label: str) -> None:
    """
    Record that an alert was sent for this (task_name, label) pair.

    Upserts: if a row already exists (expired or not), update its
    expires_at. If no row exists, insert a new one.
    """
    now     = datetime.now(timezone.utc)
    expiry  = now + timedelta(hours=ENTRY_TTL_HOURS)

    with get_db() as db:
        row = (
            db.query(ReminderState)
            .filter(
                ReminderState.task_name == task_name,
                ReminderState.label     == label,
            )
            .first()
        )
        if row:
            # Update expiry on existing row
            row.expires_at = expiry
        else:
            # Insert new row
            db.add(ReminderState(
                task_name  = task_name,
                label      = label,
                expires_at = expiry,
                created_at = now,
            ))

    log.debug("Reminder state marked  task=%r  label=%s  expires=%s",
              task_name[:50], label, expiry.isoformat())


def clear_state() -> None:
    """
    Delete all reminder state entries.

    Called via DELETE /admin/clear-state. After this, every task will
    re-alert on the next reminder engine cycle.
    """
    with get_db() as db:
        deleted = db.query(ReminderState).delete()
    log.info("Reminder state cleared — %d entries deleted.", deleted)


def get_state_summary() -> dict:
    """
    Return a summary of the current reminder state for /admin/health.
    """
    now = datetime.now(timezone.utc)
    with get_db() as db:
        total   = db.query(ReminderState).count()
        active  = (
            db.query(ReminderState)
            .filter(ReminderState.expires_at > now)
            .count()
        )
    return {"total_entries": total, "active_entries": active}
