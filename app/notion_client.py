"""
Notion API client.

Features
────────
• Typed Task dataclass — clean interface for downstream consumers
• Graceful handling of missing / malformed property fields
• Structured logging of query results and API errors
• Multi-database ready: pass db_id explicitly to override the default
"""

from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import Optional

from .config import settings
from .logger import get_logger

log = get_logger(__name__)

_NOTION_API_VERSION = "2022-06-28"
_BASE_URL = "https://api.notion.com/v1"


@dataclass
class Task:
    """A normalised Notion task record."""
    id: str
    name: str
    due: Optional[str]       # ISO-8601 string or None
    status: Optional[str]
    priority: Optional[str]

    def __str__(self) -> str:
        parts = [f"📌 {self.name}"]
        if self.due:
            parts.append(f"  Due: {self.due}")
        if self.priority:
            parts.append(f"  Priority: {self.priority}")
        if self.status:
            parts.append(f"  Status: {self.status}")
        return "\n".join(parts)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.notion_api_key}",
        "Notion-Version": _NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


def _safe_title(props: dict, key: str = "Task Name") -> Optional[str]:
    try:
        return props[key]["title"][0]["plain_text"]
    except (KeyError, IndexError, TypeError):
        return None


def _safe_date(props: dict, key: str = "Due Date") -> Optional[str]:
    try:
        return props[key]["date"]["start"]
    except (KeyError, TypeError):
        return None


def _safe_select(props: dict, key: str) -> Optional[str]:
    try:
        return props[key]["select"]["name"]
    except (KeyError, TypeError):
        return None


def fetch_tasks(
    db_id: str | None = None,
    filter_body: dict | None = None,
) -> list[Task]:
    """
    Query a Notion database and return a list of Task objects.

    Parameters
    ----------
    db_id       : Override the default database ID (multi-user support).
    filter_body : Optional Notion filter dict for server-side filtering.

    Returns
    -------
    List of Task objects (empty list on any error, so callers are safe).
    """
    target_db = db_id or settings.notion_tasks_db_id
    url = f"{_BASE_URL}/databases/{target_db}/query"
    body = filter_body or {}

    log.debug("Querying Notion DB: %s", target_db)

    try:
        response = requests.post(url, headers=_headers(), json=body, timeout=15)
        response.raise_for_status()
    except requests.HTTPError as exc:
        log.error(
            "Notion HTTP error: status=%s  body=%s",
            exc.response.status_code if exc.response else "?",
            exc.response.text[:300] if exc.response else str(exc),
        )
        return []
    except requests.RequestException as exc:
        log.error("Notion request failed: %s", exc)
        return []

    data = response.json()
    raw_results = data.get("results", [])
    tasks: list[Task] = []

    for record in raw_results:
        props = record.get("properties", {})
        name = _safe_title(props)
        if not name:
            continue                # skip un-named records

        tasks.append(
            Task(
                id=record.get("id", ""),
                name=name,
                due=_safe_date(props),
                status=_safe_select(props, "Status"),
                priority=_safe_select(props, "Priority"),
            )
        )

    log.info("Notion query returned %d tasks from DB %s", len(tasks), target_db)
    return tasks
