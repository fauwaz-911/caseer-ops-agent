"""
Notion read service.

Fetches tasks from the Notion database and caches them locally.

Change from v3.2.0
──────────────────
Removed the server-side filter and sort from the Notion query.
Server-side filters require exact property name matches — if your
database uses "Due Date" vs "Due" vs "Date", the filter silently
returns 0 results. Filtering and sorting now happens in Python after
the raw results arrive, which is always reliable.
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
    Fetch all tasks from Notion, filter and sort in Python, update task cache.

    Returns tasks that are NOT Completed, ordered by due date.
    """
    log_ctx = ctx.logger(__name__)
    log_ctx.info("Fetching tasks from Notion  db=%s", workspace.notion_db_id[:8])

    url = f"https://api.notion.com/v1/databases/{workspace.notion_db_id}/query"

    headers = {
        "Authorization":  f"Bearer {workspace.notion_token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }

    # No server-side filter — fetch everything and filter in Python
    # This avoids silent failures from property name mismatches
    all_pages = []
    payload   = {"page_size": 100}

    while True:
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise NotionError(f"Notion request failed: {exc}") from exc

        if response.status_code != 200:
            raise NotionError(
                f"Notion returned HTTP {response.status_code}: {response.text[:300]}"
            )

        data    = response.json()
        results = data.get("results", [])
        all_pages.extend(results)

        # Handle Notion pagination
        if data.get("has_more") and data.get("next_cursor"):
            payload["start_cursor"] = data["next_cursor"]
        else:
            break

    # Parse all pages into Task objects
    tasks = []
    for page in all_pages:
        task = _parse_page(page)
        if task:
            tasks.append(task)

    # Filter out Completed tasks in Python — reliable regardless of property name
    active_tasks = [t for t in tasks if t.status != "Completed"]

    # Sort by due date — tasks without due dates go to the end
    active_tasks.sort(key=lambda t: (t.due is None, t.due or datetime.max.replace(tzinfo=timezone.utc)))

    log_ctx.info(
        "Fetched %d total tasks, %d active", len(tasks), len(active_tasks)
    )

    # Update task cache with active tasks (preserves display_order for "task 1" lookup)
    _update_task_cache(active_tasks)

    return active_tasks


def _parse_page(page: dict) -> Optional[Task]:
    """Extract a Task from a raw Notion page object."""
    props     = page.get("properties", {})
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
    """Extract task title — tries common property names."""
    for key in ("Name", "Task", "Title", "task", "name"):
        if key in props:
            title_arr = props[key].get("title", [])
            if title_arr:
                return title_arr[0].get("plain_text", "").strip()
    return ""


def _safe_date(props: dict) -> Optional[datetime]:
    """Extract due date — tries common property names."""
    for key in ("Due Date", "Due", "Date", "due_date", "due"):
        if key in props:
            date_obj = props[key].get("date")
            if date_obj:
                start = date_obj.get("start", "")
                if start:
                    try:
                        dt = datetime.fromisoformat(start) if "T" in start \
                             else datetime.fromisoformat(f"{start}T00:00:00")
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except ValueError:
                        pass
    return None


def _safe_select(props: dict, key: str) -> Optional[str]:
    """Extract a select property value."""
    if key in props:
        select = props[key].get("select")
        if select:
            return select.get("name")
    return None


def _update_task_cache(tasks: list[Task]) -> None:
    """
    Sync the active task list into the task_cache table.

    Uses the Task objects (already parsed) rather than raw pages.
    display_order is the 1-indexed position in the active task list
    — this is what "task 1", "task 2" refers to.
    """
    try:
        from app.db.database import get_db
        from app.db.models import TaskCache
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        with get_db() as db:
            for position, task in enumerate(tasks, start=1):
                row = (
                    db.query(TaskCache)
                    .filter(TaskCache.notion_id == task.id)
                    .first()
                )
                if row:
                    row.name          = task.name
                    row.due           = task.due
                    row.status        = task.status
                    row.priority      = task.priority
                    row.display_order = position
                    row.synced_at     = now
                else:
                    db.add(TaskCache(
                        notion_id     = task.id,
                        name          = task.name,
                        due           = task.due,
                        status        = task.status,
                        priority      = task.priority,
                        display_order = position,
                        synced_at     = now,
                    ))

        log.debug("Task cache updated — %d tasks", len(tasks))

    except Exception as exc:
        log.warning("Task cache update failed (non-critical): %s", exc)
