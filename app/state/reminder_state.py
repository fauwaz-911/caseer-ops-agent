"""
Idempotency cache for the reminder engine.

Problem it solves
─────────────────
Without state, every reminder cycle would re-send alerts for the same
task. The cache records which (task, urgency_label) pairs have already
been dispatched so they are not sent again.

Storage
───────
JSON file on disk (path from settings.STATE_FILE).
Survives server restarts, deploys, and cold starts on Render.

TTL
───
Each cache entry carries an expiry timestamp. Entries older than
ENTRY_TTL_HOURS are evicted on every load. This ensures:
  • A task that stays overdue for days continues to alert (after TTL).
  • The cache file doesn't grow forever.

Thread safety
─────────────
The scheduler runs jobs on a background thread. All reads/writes are
protected by a threading.Lock so concurrent access is safe.

Schema (STATE_FILE)
───────────────────
{
  "task_name:label": "2025-03-01T10:00:00+00:00",   ← expiry ISO timestamp
  ...
}
"""

import json
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict

from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)

# How long before a sent entry is eligible to re-alert
ENTRY_TTL_HOURS = 24

_lock = threading.Lock()
_cache: Dict[str, str] = {}     # key → expiry ISO string
_loaded = False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _state_path() -> str:
    os.makedirs(os.path.dirname(settings.STATE_FILE), exist_ok=True)
    return settings.STATE_FILE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> None:
    """Load cache from disk, evicting expired entries in the process."""
    global _cache, _loaded
    path = _state_path()
    raw: Dict[str, str] = {}

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load reminder state file (%s) — starting fresh.", exc)

    now = _now()
    _cache = {
        k: v for k, v in raw.items()
        if datetime.fromisoformat(v) > now           # keep only non-expired
    }
    evicted = len(raw) - len(_cache)
    if evicted:
        log.debug("Evicted %d expired reminder state entries.", evicted)
    _loaded = True


def _save() -> None:
    path = _state_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2)
    except OSError as exc:
        log.error("Could not persist reminder state: %s", exc)


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _load()


# ── Public API ────────────────────────────────────────────────────────────────

def already_sent(task_name: str, label: str) -> bool:
    """Return True if this (task, label) pair is still within its TTL window."""
    with _lock:
        _ensure_loaded()
        key = f"{task_name}:{label}"
        expiry_str = _cache.get(key)
        if not expiry_str:
            return False
        return datetime.fromisoformat(expiry_str) > _now()


def mark_sent(task_name: str, label: str) -> None:
    """Record that this alert was sent; set TTL expiry and persist."""
    with _lock:
        _ensure_loaded()
        key = f"{task_name}:{label}"
        expiry = (_now() + timedelta(hours=ENTRY_TTL_HOURS)).isoformat()
        _cache[key] = expiry
        _save()
        log.debug("Reminder state marked: key=%s  expires=%s", key, expiry)


def clear_state() -> None:
    """Wipe all state. Used by admin endpoint and tests."""
    global _cache, _loaded
    with _lock:
        _cache = {}
        _loaded = True
        _save()
    log.info("Reminder state cleared.")


def get_state_summary() -> dict:
    """Return a snapshot of current state for the /health endpoint."""
    with _lock:
        _ensure_loaded()
        now = _now()
        active = {k: v for k, v in _cache.items() if datetime.fromisoformat(v) > now}
        return {"active_entries": len(active), "entries": active}
