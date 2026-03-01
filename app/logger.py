"""
Centralized logging configuration.
- Console: INFO-level, human-readable
- File:    DEBUG-level, JSON-structured, rotating (10 MB × 5 backups)
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime, timezone


LOG_DIR = os.getenv("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger pre-configured with console + rotating file handlers."""
    logger = logging.getLogger(name)

    if logger.handlers:          # avoid duplicate handlers on re-import
        return logger

    logger.setLevel(logging.DEBUG)

    # ── Console handler ────────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    # ── Rotating file handler ──────────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(LOG_DIR, "ops_agent.log"),
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JSONFormatter())

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger
