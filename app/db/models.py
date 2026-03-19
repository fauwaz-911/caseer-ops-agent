"""
ORM table definitions.

Four tables — each replaces or extends a previous in-memory/file system:

  reminder_state    ← replaces logs/reminder_state.json
  conversation_state ← replaces in-memory dict in conversation_state.py
  task_cache        ← new: caches Notion tasks, enables task lookup by number
  message_history   ← new: logs every Telegram exchange (in + out)

Why SQLAlchemy ORM over raw SQL?
────────────────────────────────
Type safety, no string-templated queries, automatic schema from Python
classes. When we add alembic migrations later, these model definitions
are already in the right format.
"""

from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer, Text, Boolean
from app.db.database import Base


def _now() -> datetime:
    """Return current UTC time — used as column default."""
    return datetime.now(timezone.utc)


class ReminderState(Base):
    """
    Tracks which reminder alerts have already been sent.

    Replaces logs/reminder_state.json. Each row represents one
    (task_name, label) pair that has been alerted. The row expires
    after TTL hours so the same task re-alerts after expiry.

    Columns
    ───────
    task_name   Notion task title (the task being tracked)
    label       Urgency tier: OVERDUE, CRITICAL, DUE_TODAY, UPCOMING
    expires_at  When this entry expires and the task is eligible to re-alert
    created_at  When the alert was first sent
    """
    __tablename__ = "reminder_state"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    task_name  = Column(String(500), nullable=False, index=True)
    label      = Column(String(50),  nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class ConversationState(Base):
    """
    Tracks pending confirmations waiting for user yes/no.

    Replaces the in-memory dict in conversation_state.py. Persisting
    this to the database means a pending confirmation survives a server
    restart — the user can still reply "yes" after a redeploy.

    Columns
    ───────
    chat_id     Telegram chat ID (one row per active conversation)
    intent_json The serialised Intent object (JSON string)
    asked_at    When the confirmation was requested (for TTL checks)
    """
    __tablename__ = "conversation_state"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    chat_id     = Column(String(50), nullable=False, unique=True, index=True)
    intent_json = Column(Text, nullable=False)
    asked_at    = Column(DateTime(timezone=True), default=_now, nullable=False)


class TaskCache(Base):
    """
    Local cache of tasks fetched from Notion.

    Avoids hitting the Notion API on every reminder cycle. Tasks are
    synced whenever fetch_tasks() runs. The display_order column records
    the position in the list the last time tasks were fetched — this is
    what "task 1", "task 2" refers to in user commands.

    Columns
    ───────
    notion_id     Notion page ID (stable unique identifier)
    name          Task title
    due           Due date/time (nullable)
    status        Current status: Pending, In Progress, Stopped, Completed
    priority      Priority value from Notion (nullable)
    display_order Position in the last fetched list (1-indexed)
    synced_at     When this row was last updated from Notion
    """
    __tablename__ = "task_cache"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    notion_id     = Column(String(100), nullable=False, unique=True, index=True)
    name          = Column(String(500), nullable=False)
    due           = Column(DateTime(timezone=True), nullable=True)
    status        = Column(String(100), nullable=True)
    priority      = Column(String(100), nullable=True)
    display_order = Column(Integer, nullable=False, default=0)
    synced_at     = Column(DateTime(timezone=True), default=_now, nullable=False)


class MessageHistory(Base):
    """
    Logs every Telegram exchange — inbound messages and outbound replies.

    Useful for debugging, auditing, and eventually for giving the AI
    conversation context across sessions.

    Columns
    ───────
    chat_id     Telegram chat ID
    direction   'in' (user → bot) or 'out' (bot → user)
    text        The message text
    intent      The classified intent action (nullable — only set for 'in')
    timestamp   When the message was sent/received
    """
    __tablename__ = "message_history"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    chat_id   = Column(String(50),  nullable=False, index=True)
    direction = Column(String(3),   nullable=False)   # 'in' or 'out'
    text      = Column(Text,        nullable=False)
    intent    = Column(String(100), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=_now, nullable=False)
