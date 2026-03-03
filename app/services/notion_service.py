"""
Notion API service.

Features
────────
• Accepts an explicit Workspace so it's multi-tenant ready.
• All property extraction through _safe_* helpers — never crashes on
  missing or malformed fields.
• Timezone-safe: all datetime objects are returned UTC-aware.
• Raises NotionError (typed) so callers know exactly what went wrong.
• ExecutionContext flows through for correlated log lines.
"""

from __future__ import annotations

import requests
from datetime import datetime, timezone
from typing import Optional

from app.models.task import Task
from app.models.workspace import Workspace
from app.core.exceptions import NotionError
from app.core.execution_context import ExecutionContext
from app.logger import get_logger

_NOTION_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"

_log = get_logger(__name__)


# ── Field extractors ──────────────────────────────────────────────────────────

def _safe_title(props: dict, key: str = "Task Name") -> Optional[str]:
    try:
        return props[key]["title"][0]["plain_text"]
    except (KeyError, IndexError, TypeError):
        return None


def _safe_date(props: dict, key: str = "Due Date") -> Optional[datetime]:
    try:
        raw = props[key]["date"]["start"]
    except (KeyError, TypeError):
        return None
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        _log.warning("Could not parse date %r — skipping.", raw)
        return None


def _safe_select(props: dict, key: str) -> Optional[str]:
    try:
        return props[key]["select"]["name"]
    except (KeyError, TypeError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_tasks(workspace: Workspace, ctx: ExecutionContext) -> list[Task]:
    """
    Query the workspace's Notion database and return typed Task objects.

    Parameters
    ----------
    workspace : Workspace object carrying the credentials and DB ID.
    ctx       : ExecutionContext for correlated logging.

    Returns
    -------
    List of Task objects. Empty list on any non-fatal error.

    Raises
    ------
    NotionError on HTTP/network failure so the caller can decide to abort.
    """
    log = ctx.logger(__name__)
    url = f"{_BASE_URL}/databases/{workspace.notion_db_id}/query"
    headers = {
        "Authorization":  f"Bearer {workspace.notion_token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type":   "application/json",
    }

    log.info("Fetching tasks from Notion DB %s", workspace.notion_db_id)

    try:
        response = requests.post(url, headers=headers, json={}, timeout=15)
    except requests.RequestException as exc:
        raise NotionError(f"Network error querying Notion: {exc}") from exc

    if response.status_code != 200:
        raise NotionError(
            f"Notion API returned {response.status_code}: {response.text[:300]}"
        )

    results = response.json().get("results", [])
    tasks: list[Task] = []

    for record in results:
        props = record.get("properties", {})
        name = _safe_title(props)
        if not name:
            continue
        tasks.append(Task(
            id       = record.get("id", ""),
            name     = name,
            due      = _safe_date(props),
            status   = _safe_select(props, "Status"),
            priority = _safe_select(props, "Priority"),
        ))

    log.info("Fetched %d tasks from Notion.", len(tasks))
    return tasks
