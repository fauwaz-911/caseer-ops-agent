"""
Database layer — SQLAlchemy engine and session management.

Design decisions
────────────────
• Synchronous SQLAlchemy (not async). FastAPI runs sync DB operations in
  a threadpool via run_in_threadpool — no async complexity needed here.
• Single engine created at import time from DATABASE_URL env var.
• Session factory used as a context manager everywhere — sessions are
  always committed or rolled back and closed, never leaked.
• create_tables() called once at startup — idempotent, safe to call
  repeatedly (uses CREATE TABLE IF NOT EXISTS via checkfirst=True).

Connection pooling
──────────────────
SQLAlchemy's default pool (QueuePool) handles Render PostgreSQL fine.
pool_pre_ping=True checks the connection is alive before using it —
prevents "connection closed" errors after Render's free DB sleeps.
"""

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.logger import get_logger

log = get_logger(__name__)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


def _make_engine():
    """
    Build the SQLAlchemy engine from the DATABASE_URL env var.
    Called once at module import — config is read at that point.
    """
    from app.config import settings
    if not settings.DATABASE_URL:
        raise EnvironmentError(
            "DATABASE_URL is not set. "
            "Add your Render PostgreSQL Internal Database URL to env vars."
        )
    return create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,       # verify connection before use
        pool_size=5,              # max persistent connections
        max_overflow=10,          # extra connections under load
    )


# Module-level singletons — created once when the module is first imported
engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_db():
    """
    Yield a database session as a context manager.

    Usage:
        with get_db() as db:
            db.add(some_row)
            db.commit()

    Always commits on success, rolls back on exception, closes on exit.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_tables() -> None:
    """
    Create all tables defined in app/db/models.py if they don't exist.

    Called once at app startup. Safe to call multiple times — SQLAlchemy
    checks if each table exists before trying to create it.
    """
    # Import models so SQLAlchemy knows about them before create_all
    import app.db.models  # noqa: F401
    Base.metadata.create_all(bind=engine, checkfirst=True)
    log.info("Database tables verified / created.")
