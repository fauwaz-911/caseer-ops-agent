"""
Conversation state manager.

What this does
──────────────
When the system asks "Are you sure you want to run the reminder engine?"
it needs to remember what it was about to do — so when you reply "yes",
it knows which action to execute. This module stores those pending
confirmations in memory, keyed by chat_id.

Why keyed by chat_id?
─────────────────────
This is multi-user ready. Each Telegram chat (each user) has its own
independent pending action. When multi-tenancy is added later, this
same pattern extends to per-workspace state stored in the database.

Structure of a pending confirmation
────────────────────────────────────
{
    "chat_id": "123456789",
    "intent":  <Intent object>,     ← what the user originally asked for
    "asked_at": datetime,           ← when we asked — for TTL expiry
}

TTL (time-to-live)
──────────────────
Confirmations expire after CONFIRMATION_TTL_SECONDS (default: 5 minutes).
If you don't reply in time, the pending action is dropped and you'd need
to ask again. This prevents stale confirmations from executing accidentally
hours later.

Thread safety
─────────────
Background scheduler and webhook handler run on different threads.
All reads/writes use a threading.Lock.
"""

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.models.intent import Intent
from app.logger import get_logger

log = get_logger(__name__)

# How long a pending confirmation waits for a yes/no before expiring
CONFIRMATION_TTL_SECONDS = 300      # 5 minutes

_lock = threading.Lock()

# In-memory store: chat_id (str) → pending confirmation dict
# Format: { "intent": Intent, "asked_at": datetime }
_pending: dict[str, dict] = {}


# ── Confirmation words — what counts as "yes" and "no" ───────────────────────
# Kept deliberately broad so natural language works
_YES_WORDS = {"yes", "y", "confirm", "do it", "proceed", "go", "ok", "okay", "sure", "yep", "yeah"}
_NO_WORDS  = {"no", "n", "cancel", "stop", "abort", "nope", "nah", "don't", "dont"}


def store_pending(chat_id: str, intent: Intent) -> None:
    """
    Store an intent that is waiting for user confirmation.

    Called after the system sends the confirmation prompt to the user.
    """
    with _lock:
        _pending[chat_id] = {
            "intent":   intent,
            "asked_at": datetime.now(timezone.utc),
        }
    log.debug(
        "Pending confirmation stored  chat_id=%s  action=%s",
        chat_id, intent.action,
    )


def get_pending(chat_id: str) -> Optional[Intent]:
    """
    Return the pending intent for a chat_id if it exists and hasn't expired.

    Returns None if there is no pending confirmation or if it has expired.
    """
    with _lock:
        entry = _pending.get(chat_id)
        if not entry:
            return None

        # Check if the confirmation window has expired
        age = (datetime.now(timezone.utc) - entry["asked_at"]).total_seconds()
        if age > CONFIRMATION_TTL_SECONDS:
            # Quietly drop the expired entry
            del _pending[chat_id]
            log.debug(
                "Pending confirmation expired  chat_id=%s  age=%.0fs",
                chat_id, age,
            )
            return None

        return entry["intent"]


def clear_pending(chat_id: str) -> None:
    """Remove the pending confirmation for a chat — called after yes or no."""
    with _lock:
        _pending.pop(chat_id, None)
    log.debug("Pending confirmation cleared  chat_id=%s", chat_id)


def is_confirmation(text: str) -> bool:
    """
    Return True if the text looks like a 'yes' response.

    Checks against a set of known confirmation words (case-insensitive).
    """
    return text.strip().lower() in _YES_WORDS


def is_cancellation(text: str) -> bool:
    """
    Return True if the text looks like a 'no' response.

    Checks against a set of known cancellation words (case-insensitive).
    """
    return text.strip().lower() in _NO_WORDS
