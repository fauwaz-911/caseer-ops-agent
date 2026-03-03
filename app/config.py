"""
Environment-driven configuration.

Design decisions
────────────────
• Uses a factory function (load_settings) so os.getenv is called at
  runtime, not at class-definition time — avoids the frozen-at-import
  bug common in dataclass-based configs.
• Returns a frozen (immutable) Settings object — no accidental mutation.
• Module-level `settings` singleton is the only import other modules need.

New in this version
───────────────────
• OPENROUTER_API_KEY — required for AI intent parsing and response enrichment
• OPENROUTER_MODEL   — which free model to use (default: mistral-7b)
• WEBHOOK_SECRET     — optional token Telegram sends with every update so
                       we can reject requests that didn't come from Telegram
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

    # ── AI / OpenRouter ────────────────────────────────────────────────────────
    OPENROUTER_API_KEY: str             # required — get from openrouter.ai
    OPENROUTER_BASE_URL: str            # OpenRouter API base (rarely changes)
    OPENROUTER_MODEL: str               # free model slug for intent parsing
    OPENROUTER_ENRICH_MODEL: str        # free model for response enrichment
    OPENROUTER_TIMEOUT: int             # seconds before giving up on AI call

    # ── Telegram Webhook ───────────────────────────────────────────────────────
    WEBHOOK_BASE_URL: str               # your public Render URL e.g. https://ops-agent.onrender.com
    WEBHOOK_SECRET: str                 # random token Telegram includes in every update header


# Required at startup — app refuses to start without these
_REQUIRED_KEYS = [
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "OPENROUTER_API_KEY",
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

        # AI settings — free OpenRouter models by default
        OPENROUTER_API_KEY         = os.environ["OPENROUTER_API_KEY"],
        OPENROUTER_BASE_URL        = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        OPENROUTER_MODEL           = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free"),
        OPENROUTER_ENRICH_MODEL    = os.getenv("OPENROUTER_ENRICH_MODEL", "mistralai/mistral-7b-instruct:free"),
        OPENROUTER_TIMEOUT         = int(os.getenv("OPENROUTER_TIMEOUT", "20")),

        # Webhook settings
        WEBHOOK_BASE_URL           = os.environ["WEBHOOK_BASE_URL"],
        WEBHOOK_SECRET             = os.getenv("WEBHOOK_SECRET", "ops-agent-webhook-secret"),
    )


# ── Module-level singleton — the only import other modules need ───────────────
settings: Settings = load_settings()
