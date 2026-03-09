"""
Environment-driven configuration.

AI provider strategy
─────────────────────
The system uses a three-layer fallback for all AI calls:

  Layer 1 — Groq (primary)
    Free tier: 14,400 req/day on llama-3.1-8b-instant
    Get key: console.groq.com → API Keys

  Layer 2 — Gemini (secondary)
    Free tier: 1,500 req/day on gemini-2.0-flash
    Get key: aistudio.google.com/apikey

  Layer 3 — Rule-based classifier (final fallback)
    Zero API calls. Keyword pattern matching.
    Handles common commands even when all APIs are down.

The system never goes fully silent. If Groq and Gemini are both
exhausted, the rule classifier handles known commands and unknown
messages get a clear "I can't think right now" response with a
list of commands the user can try.
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

    # ── State ──────────────────────────────────────────────────────────────────
    STATE_FILE: str

    # ── AI Layer 1: Groq (primary) ─────────────────────────────────────────────
    GROQ_API_KEY: str           # console.groq.com — free, 14,400 req/day
    GROQ_MODEL: str             # llama-3.1-8b-instant = high volume free tier
    GROQ_TIMEOUT: int

    # ── AI Layer 2: Gemini (secondary) ────────────────────────────────────────
    GEMINI_API_KEY: str         # aistudio.google.com/apikey — 1,500 req/day free
    GEMINI_MODEL: str
    GEMINI_TIMEOUT: int

    # ── Telegram Webhook ───────────────────────────────────────────────────────
    WEBHOOK_BASE_URL: str
    WEBHOOK_SECRET: str


# App refuses to start without these
_REQUIRED_KEYS = [
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "WEBHOOK_BASE_URL",
]


def load_settings() -> Settings:
    """Read env, validate required keys, return immutable Settings."""
    missing = [k for k in _REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set them in your .env file or deployment environment."
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

        # Groq — primary AI (high free quota)
        GROQ_API_KEY               = os.environ["GROQ_API_KEY"],
        GROQ_MODEL                 = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
        GROQ_TIMEOUT               = int(os.getenv("GROQ_TIMEOUT", "20")),

        # Gemini — secondary AI (fallback)
        GEMINI_API_KEY             = os.environ["GEMINI_API_KEY"],
        GEMINI_MODEL               = os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        GEMINI_TIMEOUT             = int(os.getenv("GEMINI_TIMEOUT", "20")),

        WEBHOOK_BASE_URL           = os.environ["WEBHOOK_BASE_URL"],
        WEBHOOK_SECRET             = os.getenv("WEBHOOK_SECRET", "ops-agent-webhook-secret"),
    )


settings: Settings = load_settings()
