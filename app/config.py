"""
Environment-driven configuration with eager validation at startup.

All required variables are asserted at import time so the application
fails fast with a clear error instead of silently misbehaving at runtime.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

from .logger import get_logger

load_dotenv()

log = get_logger(__name__)

_REQUIRED = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
]


@dataclass(frozen=True)
class Settings:
    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Notion
    notion_api_key: str
    notion_tasks_db_id: str

    # Scheduler cadence (overridable via env)
    morning_hour: int = 10
    morning_minute: int = 0
    evening_hour: int = 18
    evening_minute: int = 0
    reminder_interval_minutes: int = 30

    # Telegram retry
    telegram_max_retries: int = 3
    telegram_retry_backoff: float = 2.0       # seconds

    # Timezone (for APScheduler)
    scheduler_timezone: str = "UTC"


def load_settings() -> Settings:
    """Validate env vars and return an immutable Settings object."""
    missing = [k for k in _REQUIRED if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in a .env file or your deployment environment."
        )

    settings = Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        notion_api_key=os.environ["NOTION_API_KEY"],
        notion_tasks_db_id=os.environ["NOTION_TASKS_DB_ID"],
        morning_hour=int(os.getenv("MORNING_HOUR", "10")),
        morning_minute=int(os.getenv("MORNING_MINUTE", "0")),
        evening_hour=int(os.getenv("EVENING_HOUR", "18")),
        evening_minute=int(os.getenv("EVENING_MINUTE", "0")),
        reminder_interval_minutes=int(os.getenv("REMINDER_INTERVAL_MINUTES", "30")),
        telegram_max_retries=int(os.getenv("TELEGRAM_MAX_RETRIES", "3")),
        telegram_retry_backoff=float(os.getenv("TELEGRAM_RETRY_BACKOFF", "2.0")),
        scheduler_timezone=os.getenv("SCHEDULER_TIMEZONE", "UTC"),
    )

    log.info(
        "Configuration loaded — morning=%02d:%02d  evening=%02d:%02d  "
        "reminder_interval=%dm  tz=%s",
        settings.morning_hour, settings.morning_minute,
        settings.evening_hour, settings.evening_minute,
        settings.reminder_interval_minutes,
        settings.scheduler_timezone,
    )
    return settings


# Module-level singleton — imported by all other modules
settings: Settings = load_settings()
