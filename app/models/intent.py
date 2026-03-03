"""
Intent model.

When a user sends a message to the bot, the AI parses it and returns
a structured Intent object. The webhook router then uses this object
to decide what to do — act immediately, ask for confirmation, or reply.

Intent types
────────────
  FORCE_REMINDER   → run the reminder engine right now
  MORNING_BRIEF    → send the morning task summary
  EVENING_BRIEF    → send the evening task summary
  SEND_UPDATE      → send current full task list
  CLEAR_STATE      → wipe the idempotency cache
  TEST_TELEGRAM    → send a connectivity ping
  STATUS           → report scheduler and system health
  FREE_RESPONSE    → general question — AI answers directly, no system action
  UNKNOWN          → could not understand the message

Confirmation policy
───────────────────
Scheduled jobs (morning_brief, evening_brief, reminder_engine) run
automatically on their schedule without asking. But if YOU trigger
any action manually through the chat, the system confirms first —
UNLESS the action is explicitly a direct command like "yes", "confirm",
or "do it". This is controlled by the `requires_confirmation` flag
on each intent type.

Why a dataclass and not a dict?
────────────────────────────────
Type safety — the rest of the code can rely on .action, .confidence,
.requires_confirmation existing. No KeyError surprises.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# Every possible action the system can take
# These string values are what the AI is prompted to return
VALID_ACTIONS = {
    "force_reminder",
    "morning_brief",
    "evening_brief",
    "send_update",
    "clear_state",
    "test_telegram",
    "status",
    "free_response",
    "unknown",
}

# Actions that require user confirmation before executing.
# Scheduled automatic runs bypass this — only manual webhook triggers
# go through the confirmation flow.
ACTIONS_REQUIRING_CONFIRMATION = {
    "force_reminder",
    "morning_brief",
    "evening_brief",
    "send_update",
    "clear_state",
    "test_telegram",
}


@dataclass
class Intent:
    action: str                         # one of VALID_ACTIONS
    confidence: float                   # 0.0 → 1.0, how sure the AI is
    raw_message: str                    # the original user text
    ai_reply: Optional[str] = None      # AI's free-text reply (for FREE_RESPONSE / low confidence)
    parameters: dict = field(default_factory=dict)  # reserved for future structured params

    @property
    def requires_confirmation(self) -> bool:
        """Returns True if this action should ask the user before executing."""
        return self.action in ACTIONS_REQUIRING_CONFIRMATION

    @property
    def is_actionable(self) -> bool:
        """Returns True if there is a concrete system action to run."""
        return self.action not in {"free_response", "unknown"}

    @classmethod
    def unknown(cls, raw_message: str, ai_reply: str = "") -> "Intent":
        """Convenience constructor for unrecognised messages."""
        return cls(
            action="unknown",
            confidence=0.0,
            raw_message=raw_message,
            ai_reply=ai_reply,
        )
