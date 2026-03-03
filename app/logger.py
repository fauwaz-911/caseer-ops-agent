"""
Centralised logging configuration.

Two handlers
────────────
• Console  : INFO+, human-readable (timestamp | level | logger | msg)
• File     : DEBUG+, one JSON object per line, rotating (10 MB × 5 backups)

Usage
─────
    from app.logger import get_logger
    log = get_logger(__name__)
    log.info("message")

Call setup_logging() once at application startup (main.py lifespan).
Subsequent get_logger() calls are idempotent — handlers are never doubled.
"""

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone


_CONFIGURED = False


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a compact single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":       datetime.now(timezone.utc).isoformat(),
            "level":    record.levelname,
            "logger":   record.name,
            "msg":      record.getMessage(),
            "module":   record.module,
            "func":     record.funcName,
            "line":     record.lineno,
        }
        # Attach execution_id if injected into the record
        if hasattr(record, "execution_id"):
            payload["execution_id"] = record.execution_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure root logger. Safe to call multiple times — runs once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    os.makedirs(log_dir, exist_ok=True)
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)           # handlers filter individually

    # ── Console ───────────────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # ── Rotating JSON file ────────────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "ops_agent.log"),
        maxBytes=10 * 1024 * 1024,         # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_JSONFormatter())

    root.addHandler(console)
    root.addHandler(file_handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. setup_logging() must have been called first."""
    return logging.getLogger(name)
