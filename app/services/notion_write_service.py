"""
Notion write service.

Handles all write operations to the Notion API:
  - update_task_status(notion_id, new_status) — change a task's status
  - add_task(name, due_date)                  — create a new task page

Notion API notes
────────────────
• Update a page property: PATCH https://api.notion.com/v1/pages/{page_id}
• Create a new page:       POST https://api.notion.com/v1/pages
• Auth header:             Authorization: Bearer {NOTION_API_KEY}
• Notion version header:   Notion-Version: 2022-06-28

Status property
───────────────
Your Notion database uses a "Status" property of type Select.
Valid values: Pending, In Progress, Stopped, Completed.
The API requires the exact string — case sensitive.

Task lookup
───────────
When the user says "task 1" or "task 2", we look up the notion_id
from the task_cache table using display_order. When they say a task
name, we fuzzy-match against the cache.
"""

import requests
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.core.exceptions import NotionError
from app.logger import get_logger

log = get_logger(__name__)

_NOTION_BASE   = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    return {
        "Authorization":  f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }


# ── Task lookup from cache ────────────────────────────────────────────────────

def lookup_task(task_ref: str) -> Optional[dict]:
    """
    Look up a task from the task_cache by number or name.

    task_ref can be:
      "1"         → first task in the last fetched list (display_order=1)
      "2"         → second task
      "plan crypto bot" → matched by name (case-insensitive substring)

    Returns a dict with notion_id, name, status or None if not found.
    """
    from app.db.database import get_db
    from app.db.models import TaskCache

    with get_db() as db:
        # Try numeric lookup first
        if task_ref.strip().isdigit():
            order = int(task_ref.strip())
            row = (
                db.query(TaskCache)
                .filter(TaskCache.display_order == order)
                .first()
            )
        else:
            # Name-based lookup — case-insensitive contains match
            rows = db.query(TaskCache).all()
            row = next(
                (r for r in rows
                 if task_ref.strip().lower() in r.name.lower()),
                None,
            )

    if not row:
        return None

    return {
        "notion_id": row.notion_id,
        "name":      row.name,
        "status":    row.status,
        "due":       row.due,
    }


# ── Write operations ──────────────────────────────────────────────────────────

def update_task_status(notion_id: str, new_status: str) -> None:
    """
    Update the Status property of a Notion page.

    Parameters
    ----------
    notion_id  : The Notion page ID (from task_cache.notion_id)
    new_status : One of: Pending, In Progress, Stopped, Completed

    Raises NotionError on API failure.
    """
    url = f"{_NOTION_BASE}/pages/{notion_id}"

    payload = {
        "properties": {
            "Status": {
                "select": {
                    "name": new_status
                }
            }
        }
    }

    log.info("Updating Notion task status  notion_id=%s  status=%s",
             notion_id, new_status)

    try:
        response = requests.patch(url, json=payload, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Notion update request failed: {exc}") from exc

    if response.status_code not in (200, 201):
        raise NotionError(
            f"Notion update returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    log.info("Notion task status updated successfully  notion_id=%s", notion_id)


def add_task(name: str, due_date: Optional[str] = None) -> str:
    """
    Create a new task page in the Notion database.

    Parameters
    ----------
    name     : Task title
    due_date : ISO date string e.g. "2026-03-22" (optional)

    Returns
    -------
    The new page's Notion ID.

    Raises NotionError on API failure.
    """
    url = f"{_NOTION_BASE}/pages"

    # Build properties — always include Name, Status defaults to Pending
    properties = {
        "Name": {
            "title": [
                {"text": {"content": name}}
            ]
        },
        "Status": {
            "select": {"name": "Pending"}
        },
    }

    # Add due date if provided
    if due_date:
        properties["Due Date"] = {
            "date": {"start": due_date}
        }

    payload = {
        "parent": {
            "database_id": settings.NOTION_TASKS_DB_ID
        },
        "properties": properties,
    }

    log.info("Adding new Notion task  name=%r  due=%s", name, due_date)

    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Notion add task request failed: {exc}") from exc

    if response.status_code not in (200, 201):
        raise NotionError(
            f"Notion add task returned HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    page_id = response.json().get("id", "unknown")
    log.info("New Notion task created  notion_id=%s", page_id)
    return page_id
