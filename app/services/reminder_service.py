"""
Reminder service — the core intelligence of Ops Agent.

Urgency tiers (evaluated in priority order)
──────────────────────────────────────────
  🚨 OVERDUE    — past due                  (TTL: 24h before re-alerting)
  ⚠️  CRITICAL   — due within 3 hours
  ⏳ DUE TODAY  — due within 24 hours
  📅 UPCOMING   — due within 48 hours

Idempotency
───────────
Each (task_name, urgency_label) pair is checked against the persistent
reminder state before dispatch. Already-sent entries are skipped until
their TTL expires (default 24 hours), preventing spam on every poll cycle.

Execution Context
─────────────────
Every run receives an ExecutionContext. All log lines within a run share
the same execution_id, making it trivial to trace a full job in the logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models.task import Task
from app.models.workspace import Workspace
from app.core.execution_context import ExecutionContext
from app.core.exceptions import NotionError, TelegramError
from app.services.notion_service import fetch_tasks
from app.services.telegram_service import send_message
from app.state.reminder_state import already_sent, mark_sent
from app.logger import get_logger

_log = get_logger(__name__)

# ── Urgency thresholds (seconds) ─────────────────────────────────────────────
_CRITICAL  = 3  * 3600      #  3 hours
_DUE_TODAY = 24 * 3600      # 24 hours
_UPCOMING  = 48 * 3600      # 48 hours


def _classify(task: Task, now: datetime) -> Optional[str]:
    """Return urgency label or None if outside all windows."""
    if not task.due:
        return None
    delta = (task.due - now).total_seconds()
    if delta < 0:
        return "overdue"
    if delta <= _CRITICAL:
        return "critical"
    if delta <= _DUE_TODAY:
        return "due_today"
    if delta <= _UPCOMING:
        return "upcoming"
    return None


def _format_section(emoji: str, heading: str, tasks: list[Task]) -> str:
    header = f"{emoji} <b>{heading}</b>"
    lines  = [f"  • {t.name}" + (f" — {t.due_iso()}" if t.due else "") for t in tasks]
    return "\n".join([header] + lines)


# ── Public API ────────────────────────────────────────────────────────────────

def run_reminder_engine(
    workspace: Workspace,
    ctx: ExecutionContext,
) -> dict:
    """
    Fetch tasks, classify by urgency, dispatch new alerts.

    Returns a summary dict so callers (scheduler + admin API) can inspect
    the outcome without re-running the logic.
    """
    log = ctx.logger(__name__)
    log.info("Reminder engine starting  job=%s", ctx.job)

    summary = {
        "execution_id": ctx.execution_id,
        "job":          ctx.job,
        "tasks_fetched": 0,
        "alerts_sent":  0,
        "buckets":      {"overdue": 0, "critical": 0, "due_today": 0, "upcoming": 0},
        "error":        None,
    }

    try:
        tasks = fetch_tasks(workspace, ctx)
        summary["tasks_fetched"] = len(tasks)

        now = datetime.now(timezone.utc)
        buckets: dict[str, list[Task]] = {
            "overdue":   [],
            "critical":  [],
            "due_today": [],
            "upcoming":  [],
        }

        for task in tasks:
            label = _classify(task, now)
            if label and not already_sent(task.name, label):
                buckets[label].append(task)

        # Build message sections only for non-empty buckets
        sections = []
        configs = [
            ("overdue",   "🚨", "OVERDUE TASKS"),
            ("critical",  "⚠️",  "DUE WITHIN 3 HOURS"),
            ("due_today", "⏳", "DUE TODAY"),
            ("upcoming",  "📅", "UPCOMING (48 h)"),
        ]
        for key, emoji, heading in configs:
            if buckets[key]:
                sections.append(_format_section(emoji, heading, buckets[key]))
                summary["buckets"][key] = len(buckets[key])

        if sections:
            message = "🧠 <b>Smart Task Alerts</b>\n\n" + "\n\n".join(sections)
            send_message(message, workspace, ctx)

            # Mark all dispatched tasks as sent (after successful delivery)
            for key, task_list in buckets.items():
                for task in task_list:
                    mark_sent(task.name, key)

            total = sum(len(v) for v in buckets.values())
            summary["alerts_sent"] = total
            log.info(
                "Reminder engine dispatched %d alerts  overdue=%d  critical=%d  "
                "today=%d  upcoming=%d  elapsed_ms=%d",
                total,
                summary["buckets"]["overdue"],
                summary["buckets"]["critical"],
                summary["buckets"]["due_today"],
                summary["buckets"]["upcoming"],
                ctx.elapsed_ms(),
            )
        else:
            log.info(
                "Reminder engine: no new alerts to dispatch  elapsed_ms=%d",
                ctx.elapsed_ms(),
            )

    except NotionError as exc:
        log.error("Reminder engine — Notion error: %s", exc)
        summary["error"] = f"NotionError: {exc}"

    except TelegramError as exc:
        log.error("Reminder engine — Telegram error: %s", exc)
        summary["error"] = f"TelegramError: {exc}"

    except Exception as exc:
        log.exception("Reminder engine — unexpected error: %s", exc)
        summary["error"] = f"UnexpectedError: {exc}"

    return summary


def run_morning_brief(workspace: Workspace, ctx: ExecutionContext) -> dict:
    """Fetch all tasks and send a morning summary (no idempotency filter)."""
    log = ctx.logger(__name__)
    log.info("Morning brief starting")

    try:
        tasks = fetch_tasks(workspace, ctx)
        if tasks:
            lines = [f"  {i}. {t.name}" + (f" — {t.due_iso()}" if t.due else "")
                     for i, t in enumerate(tasks, 1)]
            body = "\n".join(lines)
        else:
            body = "  No open tasks found."

        message = f"🌅 <b>Morning Brief</b>\n\n{body}"
        send_message(message, workspace, ctx)
        log.info("Morning brief sent  tasks=%d  elapsed_ms=%d", len(tasks), ctx.elapsed_ms())
        return {"status": "sent", "tasks": len(tasks), "execution_id": ctx.execution_id}

    except (NotionError, TelegramError) as exc:
        log.error("Morning brief failed: %s", exc)
        return {"status": "error", "error": str(exc), "execution_id": ctx.execution_id}


def run_evening_wrapup(workspace: Workspace, ctx: ExecutionContext) -> dict:
    """Fetch all tasks and send an evening summary (no idempotency filter)."""
    log = ctx.logger(__name__)
    log.info("Evening wrap-up starting")

    try:
        tasks = fetch_tasks(workspace, ctx)
        if tasks:
            lines = [f"  {i}. {t.name}" + (f" — {t.due_iso()}" if t.due else "")
                     for i, t in enumerate(tasks, 1)]
            body = "\n".join(lines)
        else:
            body = "  All clear — no open tasks."

        message = f"🌙 <b>Evening Wrap-up</b>\n\n{body}"
        send_message(message, workspace, ctx)
        log.info("Evening wrap-up sent  tasks=%d  elapsed_ms=%d", len(tasks), ctx.elapsed_ms())
        return {"status": "sent", "tasks": len(tasks), "execution_id": ctx.execution_id}

    except (NotionError, TelegramError) as exc:
        log.error("Evening wrap-up failed: %s", exc)
        return {"status": "error", "error": str(exc), "execution_id": ctx.execution_id}
