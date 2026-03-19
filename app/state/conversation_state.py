"""
Conversation state — database-backed pending confirmation tracking.

Replaces the in-memory dict with a PostgreSQL table.

Why this matters
────────────────
Previously a pending confirmation was lost if the server restarted or
Render's free tier spun down between your "check my tasks" message and
your "yes" reply. Now the pending intent is persisted in the database
and survives restarts.

TTL
───
Confirmations still expire after CONFIRMATION_TTL_SECONDS (5 minutes).
Expired rows are ignored and cleaned up on the next read.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.db.database import get_db
from app.db.models import ConversationState
from app.logger import get_logger

log = get_logger(__name__)

CONFIRMATION_TTL_SECONDS = 300  # 5 minutes

# Words that count as yes/no — kept here so webhook_routes imports from one place
_YES_WORDS = {"yes", "y", "confirm", "do it", "proceed", "go", "ok", "okay", "sure", "yep", "yeah"}
_NO_WORDS  = {"no", "n", "cancel", "stop", "abort", "nope", "nah", "don't", "dont"}


def store_pending(chat_id: str, intent) -> None:
    """
    Persist a pending confirmation for the given chat_id.

    Serialises the Intent object to JSON for storage. Upserts —
    replaces any existing pending confirmation for this chat.
    """
    intent_json = json.dumps({
        "action":     intent.action,
        "confidence": intent.confidence,
        "raw_message":intent.raw_message,
        "ai_reply":   intent.ai_reply or "",
        "parameters": intent.parameters or {},
    })

    now = datetime.now(timezone.utc)

    with get_db() as db:
        row = (
            db.query(ConversationState)
            .filter(ConversationState.chat_id == chat_id)
            .first()
        )
        if row:
            row.intent_json = intent_json
            row.asked_at    = now
        else:
            db.add(ConversationState(
                chat_id     = chat_id,
                intent_json = intent_json,
                asked_at    = now,
            ))

    log.debug("Pending confirmation stored  chat_id=%s  action=%s",
              chat_id, intent.action)


def get_pending(chat_id: str) -> Optional[object]:
    """
    Return the pending Intent for a chat_id if it exists and hasn't expired.

    Returns None if no pending confirmation or if it has expired.
    Expired rows are deleted on read (lazy cleanup).
    """
    from app.models.intent import Intent

    now = datetime.now(timezone.utc)

    with get_db() as db:
        row = (
            db.query(ConversationState)
            .filter(ConversationState.chat_id == chat_id)
            .first()
        )
        if not row:
            return None

        # Check TTL
        age = (now - row.asked_at.replace(tzinfo=timezone.utc)
               if row.asked_at.tzinfo is None
               else (now - row.asked_at)).total_seconds()

        if age > CONFIRMATION_TTL_SECONDS:
            db.delete(row)
            log.debug("Expired pending confirmation deleted  chat_id=%s  age=%.0fs",
                      chat_id, age)
            return None

        # Deserialise the Intent
        data = json.loads(row.intent_json)
        return Intent(
            action      = data["action"],
            confidence  = data["confidence"],
            raw_message = data["raw_message"],
            ai_reply    = data.get("ai_reply", ""),
            parameters  = data.get("parameters", {}),
        )


def clear_pending(chat_id: str) -> None:
    """Remove the pending confirmation for a chat_id."""
    with get_db() as db:
        db.query(ConversationState)\
          .filter(ConversationState.chat_id == chat_id)\
          .delete()
    log.debug("Pending confirmation cleared  chat_id=%s", chat_id)


def is_confirmation(text: str) -> bool:
    """Return True if text is a recognised 'yes' response."""
    return text.strip().lower() in _YES_WORDS


def is_cancellation(text: str) -> bool:
    """Return True if text is a recognised 'no' response."""
    return text.strip().lower() in _NO_WORDS
