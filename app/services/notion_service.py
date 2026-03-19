"""
Notion read service.

Fetches tasks from the Notion database and caches them locally in
PostgreSQL. The cache serves two purposes:
  1. Reduces Notion API calls — reminder engine reads from cache
     when tasks were recently synced (within CACHE_TTL_MINUTES)
  2. Enables task lookup by number — "task 1" maps to display_order=1
     in the task_cache table

Cache strategy
──────────────
fetch_tasks() always hits Notion directly and updates the cache.
The cache is used by the Notion write service for task lookups — it
does NOT replace fetch_tasks() for the reminder engine (reminders
always need fresh data to check urgency correctly).
"""

import requests
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.core.exceptions import NotionError
from app.core.execution_context import ExecutionContext
from app.models.task import Task
from app.models.workspace import Workspace
from app.logger import get_logger

log = get_logger(__name__)

_NOTION_VERSION = "2022-06-28"


def fetch_tasks(workspace: Workspace, ctx: ExecutionContext) -> list[Task]:
    """
    Fetch all incomplete tasks from Notion and update the task cache.

    Returns a list of Task objects ordered by due date (earliest first,
    tasks without due dates at the end).

    Also writes to task_cache so the Notion write service can look up
    tasks by number or name.

    Raises NotionError on API failure.
    """
    log_ctx = ctx.logger(__name__)
    log_ctx.info("Fetching tasks from Notion  db=%s", workspace.notion_db_id[:8])

    url = f"https://api.notion.com/v1/databases/{workspace.notion_db_id}/query"

    headers = {
        "Authorization":  f"Bearer {workspace.notion_token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }

    # Filter out completed tasks — only fetch active ones
    payload = {
        "filter": {
            "property": "Status",
            "select": {
                "does_not_equal": "Completed"
            }
        },
        "sorts": [
            {"property": "Due Date", "direction": "ascending"}
        ]
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Notion request failed: {exc}") from exc

    if response.status_code != 200:
        raise NotionError(
            f"Notion returned HTTP {response.status_code}: {response.text[:300]}"
        )

    results = response.json().get("results", [])
    tasks = []

    for page in results:
        task = _parse_page(page)
        if task:
            tasks.append(task)

    log_ctx.info("Fetched %d tasks from Notion", len(tasks))

    # Update the task cache with fresh data
    _update_task_cache(results)

    return tasks


def _parse_page(page: dict) -> Optional[Task]:
    """Extract a Task from a raw Notion page object."""
    props = page.get("properties", {})

    notion_id = page.get("id", "")
    name      = _safe_title(props)
    due       = _safe_date(props)
    status    = _safe_select(props, "Status")
    priority  = _safe_select(props, "Priority")

    if not name:
        return None

    return Task(
        id       = notion_id,
        name     = name,
        due      = due,
        status   = status,
        priority = priority,
    )


def _safe_title(props: dict) -> str:
    """Extract the title from a Notion properties dict."""
    for key in ("Name", "Task", "Title"):
        if key in props:
            title_arr = props[key].get("title", [])
            if title_arr:
                return title_arr[0].get("plain_text", "").strip()
    return ""


def _safe_date(props: dict) -> Optional[datetime]:
    """Extract and parse a due date from Notion properties."""
    for key in ("Due Date", "Due", "Date"):
        if key in props:
            date_obj = props[key].get("date")
            if date_obj:
                start = date_obj.get("start", "")
                if start:
                    try:
                        # Handle both date-only and datetime strings
                        if "T" in start:
                            dt = datetime.fromisoformat(start)
                        else:
                            dt = datetime.fromisoformat(f"{start}T00:00:00")
                        # Ensure timezone-aware
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except ValueError:
                        pass
    return None


def _safe_select(props: dict, key: str) -> Optional[str]:
    """Extract a select property value from Notion properties."""
    if key in props:
        select = props[key].get("select")
        if select:
            return select.get("name")
    return None


def _update_task_cache(pages: list) -> None:
    """
    Sync the fetched Notion pages into the task_cache table.

    Each task gets a display_order (1-indexed, ordered by due date)
    so "task 1" always refers to the first item in the last fetched list.
    Existing cache rows are updated; new rows are inserted; rows for
    tasks no longer returned are left (they'll be stale but harmless).
    """
    try:
        from app.db.database import get_db
        from app.db.models import TaskCache

        now = datetime.now(timezone.utc)

        with get_db() as db:
            for position, page in enumerate(pages, start=1):
                props     = page.get("properties", {})
                notion_id = page.get("id", "")
                name      = _safe_title(props)
                due       = _safe_date(props)
                status    = _safe_select(props, "Status")
                priority  = _safe_select(props, "Priority")

                if not notion_id or not name:
                    continue

                row = (
                    db.query(TaskCache)
                    .filter(TaskCache.notion_id == notion_id)
                    .first()
                )

                if row:
                    row.name          = name
                    row.due           = due
                    row.status        = status
                    row.priority      = priority
                    row.display_order = position
                    row.synced_at     = now
                else:
                    db.add(TaskCache(
                        notion_id     = notion_id,
                        name          = name,
                        due           = due,
                        status        = status,
                        priority      = priority,
                        display_order = position,
                        synced_at     = now,
                    ))

        log.debug("Task cache updated — %d tasks synced", len(pages))

    except Exception as exc:
        # Cache update failure should never crash the main fetch
        log.warning("Task cache update failed (non-critical): %s", exc)
