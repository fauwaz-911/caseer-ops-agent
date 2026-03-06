"""
Environment-driven configuration.

Design decisions
────────────────
• Factory function (load_settings) reads env at runtime, not import time.
• Returns a frozen (immutable) Settings object — no accidental mutation.
• Module-level `settings` singleton is the only import other modules need.

AI provider
───────────
Uses Google Gemini via the OpenAI-compatible API (Google AI Studio).
Free tier: 1,500 requests/day, 1M tokens/minute — no credit card needed.
Get your key at: https://aistudio.google.com/apikey
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
    TELEGRAM_RETRY_BACKOFF: float

    # ── Logging ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str
    LOG_DIR: str

    # ── State persistence ──────────────────────────────────────────────────────
    STATE_FILE: str

    # ── AI / Gemini ────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str             # from https://aistudio.google.com/apikey
    GEMINI_MODEL: str               # stable model ID
    GEMINI_TIMEOUT: int             # seconds before giving up on AI call

    # ── Telegram Webhook ───────────────────────────────────────────────────────
    WEBHOOK_BASE_URL: str           # your Render URL e.g. https://ops-agent.onrender.com
    WEBHOOK_SECRET: str             # random token included in every Telegram update header


_REQUIRED_KEYS = [
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GEMINI_API_KEY",
    "WEBHOOK_BASE_URL",
]


def load_settings() -> Settings:
    """Read env, validate, return immutable Settings. Raises clearly on missing vars."""
    missing = [k for k in _REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in a .env file or your deployment environment."
        )

    return Settings(
        WORKSPACE_ID               = os.getenv("WORKSPACE_ID", "default"),
        NOTION_API_KEY             = os.environ["NOTION_API_KEY"],
        NOTION_TASKS_DB_ID         = os.environ["NOTION_TASKS_DB_ID"],
        TELEGRAM_BOT_TOKEN         = os.environ["TELEGRAM_BOT_TOKEN"],
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

        # Gemini — stable free model
        GEMINI_API_KEY             = os.environ["GEMINI_API_KEY"],
        GEMINI_MODEL               = os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        GEMINI_TIMEOUT             = int(os.getenv("GEMINI_TIMEOUT", "20")),

        # Webhook
        WEBHOOK_BASE_URL           = os.environ["WEBHOOK_BASE_URL"],
        WEBHOOK_SECRET             = os.getenv("WEBHOOK_SECRET", "ops-agent-webhook-secret"),
    )


settings: Settings = load_settings()
