"""
Intent model.

Structured result of parsing a user's Telegram message via AI.

New in this version
───────────────────
  update_task  → change the status of an existing Notion task
  add_task     → create a new task in Notion

Both new intents carry structured parameters the AI extracts:
  update_task: { "task_ref": "1" or "task name", "new_status": "Completed" }
  add_task:    { "task_name": "review deck", "due_date": "2026-03-22" or null }

Valid Notion status values
──────────────────────────
  Pending, In Progress, Stopped, Completed
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


VALID_ACTIONS = {
    "force_reminder",
    "morning_brief",
    "evening_brief",
    "send_update",
    "clear_state",
    "test_telegram",
    "status",
    "update_task",      # change task status in Notion
    "add_task",         # create new task in Notion
    "free_response",
    "unknown",
}

# Actions that require user confirmation before executing
ACTIONS_REQUIRING_CONFIRMATION = {
    "force_reminder",
    "morning_brief",
    "evening_brief",
    "send_update",
    "clear_state",
    "test_telegram",
    "update_task",      # always confirm before writing to Notion
    "add_task",         # always confirm before writing to Notion
}

# Valid Notion status values — used for validation before API calls
VALID_NOTION_STATUSES = {"Pending", "In Progress", "Stopped", "Completed"}


@dataclass
class Intent:
    action: str                          # one of VALID_ACTIONS
    confidence: float                    # 0.0 → 1.0
    raw_message: str                     # original user text
    ai_reply: Optional[str] = None       # for free_response / unknown
    parameters: dict = field(default_factory=dict)  # structured params for Notion actions

    @property
    def requires_confirmation(self) -> bool:
        return self.action in ACTIONS_REQUIRING_CONFIRMATION

    @property
    def is_actionable(self) -> bool:
        return self.action not in {"free_response", "unknown"}

    @classmethod
    def unknown(cls, raw_message: str, ai_reply: str = "") -> "Intent":
        return cls(
            action="unknown",
            confidence=0.0,
            raw_message=raw_message,
            ai_reply=ai_reply,
        )
