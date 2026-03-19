"""
Notion write service — update task status, add new tasks.
"""

import requests
from typing import Optional

from app.config import settings
from app.core.exceptions import NotionError
from app.logger import get_logger

log = get_logger(__name__)

_NOTION_BASE    = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization":  f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def lookup_task(task_ref: str) -> Optional[dict]:
    """
    Look up a task from task_cache by number or name.

    Reads all row values INSIDE the session block to avoid
    SQLAlchemy DetachedInstanceError after session close.
    """
    from app.db.database import get_db
    from app.db.models import TaskCache

    with get_db() as db:
        if task_ref.strip().isdigit():
            row = db.query(TaskCache).filter(
                TaskCache.display_order == int(task_ref.strip())
            ).first()
        else:
            rows = db.query(TaskCache).all()
            row  = next(
                (r for r in rows if task_ref.strip().lower() in r.name.lower()),
                None,
            )

        if not row:
            return None

        # Extract all values while session is still open
        # Accessing row attributes after session closes raises DetachedInstanceError
        return {
            "notion_id": row.notion_id,
            "name":      row.name,
            "status":    row.status,
            "due":       row.due,
        }


def update_task_status(notion_id: str, new_status: str) -> None:
    """
    PATCH the Status property of a Notion page.
    Valid values: Pending, In Progress, Stopped, Completed
    """
    url     = f"{_NOTION_BASE}/pages/{notion_id}"
    payload = {"properties": {"Status": {"select": {"name": new_status}}}}

    log.info("Updating Notion task  notion_id=%s  status=%s", notion_id, new_status)

    try:
        response = requests.patch(url, json=payload, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Notion update failed: {exc}") from exc

    if response.status_code not in (200, 201):
        raise NotionError(
            f"Notion update HTTP {response.status_code}: {response.text[:300]}"
        )
    log.info("Task updated in Notion  notion_id=%s", notion_id)


def add_task(name: str, due_date: Optional[str] = None) -> str:
    """
    Create a new task page in the Notion database.
    Uses 'Task Name' as the title property (confirmed from your database).
    Returns the new page's Notion ID.
    """
    url = f"{_NOTION_BASE}/pages"

    properties = {
        "Task Name": {
            "title": [{"text": {"content": name}}]
        },
        "Status": {
            "select": {"name": "Pending"}
        },
    }

    if due_date:
        properties["Due Date"] = {"date": {"start": due_date}}

    payload = {
        "parent":     {"database_id": settings.NOTION_TASKS_DB_ID},
        "properties": properties,
    }

    log.info("Adding Notion task  name=%r  due=%s", name, due_date)

    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Notion add task failed: {exc}") from exc

    if response.status_code not in (200, 201):
        raise NotionError(
            f"Notion add task HTTP {response.status_code}: {response.text[:300]}"
        )

    page_id = response.json().get("id", "unknown")
    log.info("New task created in Notion  notion_id=%s", page_id)
    return page_id
