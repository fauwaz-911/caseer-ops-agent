"""
Workspace model.

Currently the system runs as a single workspace loaded from environment
variables. This model is the contract for what a workspace looks like —
when multi-tenancy is added (database-backed), each row maps to one of these.

The factory method `from_settings()` bridges the current env-var world
and the future database world without touching any other module.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    notion_token: str
    notion_db_id: str
    telegram_token: str
    telegram_chat_id: str

    @classmethod
    def from_settings(cls) -> "Workspace":
        """Build the default workspace from the current Settings singleton."""
        from app.config import settings
        return cls(
            workspace_id     = settings.WORKSPACE_ID,
            notion_token     = settings.NOTION_API_KEY,        # correct field name
            notion_db_id     = settings.NOTION_TASKS_DB_ID,    # correct field name
            telegram_token   = settings.TELEGRAM_BOT_TOKEN,    # correct field name
            telegram_chat_id = settings.TELEGRAM_CHAT_ID,
        )
