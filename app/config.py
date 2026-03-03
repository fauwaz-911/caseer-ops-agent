"""
Environment-driven configuration.

Design decisions
────────────────
• Uses a factory function (load_settings) so os.getenv is called at
  runtime, not at class-definition time — avoids the frozen-at-import
  bug common in dataclass-based configs.
• Returns a frozen (immutable) Settings object — no accidental mutation.
• validate_settings() is called inside load_settings() so the application
  always fails fast with a clear message on missing env vars.
• Module-level `settings` singleton is the only import other modules need.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # ── Identity ───────────────────────────────────────────────────────────────
    WORKSPACE_ID: str

    # ── Notion ─────────────────────────────────────────────────────────────────
    NOTION_API_KEY: str
    NOTION_TASKS_DB_ID: str

    # ── Telegram ───────────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str

    # ── Scheduler ──────────────────────────────────────────────────────────────
    MORNING_HOUR: int
    MORNING_MINUTE: int
    EVENING_HOUR: int
    EVENING_MINUTE: int
    REMINDER_INTERVAL_MINUTES: int
    SCHEDULER_TIMEZONE: str

    # ── Telegram retry ─────────────────────────────────────────────────────────
    TELEGRAM_MAX_RETRIES: int
    TELEGRAM_RETRY_BACKOFF: float        # base seconds; doubles each attempt

    # ── Logging ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str
    LOG_DIR: str

    # ── State persistence ──────────────────────────────────────────────────────
    STATE_FILE: str                      # JSON file for idempotency cache


_REQUIRED_KEYS = ["NOTION_API_KEY", "NOTION_TASKS_DB_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]


def load_settings() -> Settings:
    """Read env, validate, return immutable Settings. Raises on missing vars."""
    missing = [k for k in _REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in a .env file or your deployment environment."
        )

    return Settings(
        WORKSPACE_ID               = os.getenv("WORKSPACE_ID", "default"),
        NOTION_TOKEN               = os.environ["NOTION_API_KEY"],
        NOTION_DB_ID               = os.environ["NOTION_TASKS_DB_ID"],
        TELEGRAM_TOKEN             = os.environ["TELEGRAM_BOT_TOKEN"],
        TELEGRAM_CHAT_ID           = os.environ["TELEGRAM_CHAT_ID"],
        MORNING_HOUR               = int(os.getenv("MORNING_HOUR", "10")),
        MORNING_MINUTE             = int(os.getenv("MORNING_MINUTE", "0")),
        EVENING_HOUR               = int(os.getenv("EVENING_HOUR", "18")),
        EVENING_MINUTE             = int(os.getenv("EVENING_MINUTE", "0")),
        REMINDER_INTERVAL_MINUTES  = int(os.getenv("REMINDER_INTERVAL_MINUTES", "30")),
        SCHEDULER_TIMEZONE         = os.getenv("SCHEDULER_TIMEZONE", "UTC"),
        TELEGRAM_MAX_RETRIES       = int(os.getenv("TELEGRAM_MAX_RETRIES", "3")),
        TELEGRAM_RETRY_BACKOFF     = float(os.getenv("TELEGRAM_RETRY_BACKOFF", "2.0")),
        LOG_LEVEL                  = os.getenv("LOG_LEVEL", "INFO"),
        LOG_DIR                    = os.getenv("LOG_DIR", "logs"),
        STATE_FILE                 = os.getenv("STATE_FILE", "logs/reminder_state.json"),
    )


# ── Module-level singleton ─────────────────────────────────────────────────────
settings: Settings = load_settings()
