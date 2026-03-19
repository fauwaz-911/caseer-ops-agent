"""
Environment-driven configuration.

Design decisions
────────────────
• Factory function (load_settings) reads env at runtime, not import time.
• Returns a frozen (immutable) Settings object — no accidental mutation.
• Module-level `settings` singleton is the only import other modules need.

AI provider
───────────
Set AI_PROVIDER to either "groq" (default) or "gemini".
Change the env var in Render to switch providers with no code change.

  groq   — console.groq.com — free, 14,400 req/day, no credit card
  gemini — aistudio.google.com — 1,500 req/day free, pay-as-you-go option
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

    # ── AI provider selection ──────────────────────────────────────────────────
    AI_PROVIDER: str            # "groq" or "gemini" — controls which client is used

    # ── Groq (default — free, 14,400 req/day, no credit card) ─────────────────
    GROQ_API_KEY: str           # from console.groq.com
    GROQ_MODEL: str             # e.g. llama-3.3-70b-versatile
    GROQ_TIMEOUT: int

    # ── Gemini (Google AI Studio — 1,500 req/day free, pay-as-you-go option) ──
    GEMINI_API_KEY: str         # from aistudio.google.com/apikey
    GEMINI_MODEL: str           # e.g. gemini-2.0-flash
    GEMINI_TIMEOUT: int

    # ── Telegram Webhook ───────────────────────────────────────────────────────
    WEBHOOK_BASE_URL: str       # your Render URL — no trailing slash
    WEBHOOK_SECRET: str         # random string sent in every Telegram update header


# Required at startup — app refuses to start without these
_REQUIRED_KEYS = [
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "WEBHOOK_BASE_URL",
]


def load_settings() -> Settings:
    """Read env, validate required keys, return immutable Settings."""
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

        # AI provider
        AI_PROVIDER                = os.getenv("AI_PROVIDER", "groq"),

        # Groq
        GROQ_API_KEY               = os.getenv("GROQ_API_KEY", ""),
        GROQ_MODEL                 = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        GROQ_TIMEOUT               = int(os.getenv("GROQ_TIMEOUT", "20")),

        # Gemini
        GEMINI_API_KEY             = os.getenv("GEMINI_API_KEY", ""),
        GEMINI_MODEL               = os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        GEMINI_TIMEOUT             = int(os.getenv("GEMINI_TIMEOUT", "20")),

        # Webhook
        WEBHOOK_BASE_URL           = os.environ["WEBHOOK_BASE_URL"],
        WEBHOOK_SECRET             = os.getenv("WEBHOOK_SECRET", "ops-agent-webhook-secret"),
    )


# Module-level singleton — the only import other modules need
settings: Settings = load_settings()
