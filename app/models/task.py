"""
Task model — canonical representation of a Notion task record.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Task:
    id: str
    name: str
    due: Optional[datetime]      # always UTC-aware, or None
    status: Optional[str]
    priority: Optional[str]

    def due_iso(self) -> Optional[str]:
        """Human-readable due date string, or None."""
        return self.due.strftime("%Y-%m-%d %H:%M UTC") if self.due else None

    def __str__(self) -> str:
        parts = [f"📌 {self.name}"]
        if self.due_iso():
            parts.append(f"Due: {self.due_iso()}")
        if self.priority:
            parts.append(f"Priority: {self.priority}")
        if self.status:
            parts.append(f"Status: {self.status}")
        return "  ".join(parts)
