"""
Environment-driven configuration for Ops Agent v3.1.0

AI_PROVIDER controls which AI backend is used:
  AI_PROVIDER=groq   → Groq (14,400 req/day free, no credit card)
  AI_PROVIDER=gemini → Google Gemini (1,500 req/day free)
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
    STATE_FILE: str                 # kept for migration fallback only

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str               # Render PostgreSQL Internal Database URL

    # ── AI provider switch ─────────────────────────────────────────────────────
    AI_PROVIDER: str                # "groq" or "gemini"

    # ── Groq (console.groq.com — 14,400 req/day free, no credit card) ─────────
    GROQ_API_KEY: str
    GROQ_MODEL: str
    GROQ_TIMEOUT: int

    # ── Gemini (aistudio.google.com — 1,500 req/day free) ─────────────────────
    GEMINI_API_KEY: str
    GEMINI_MODEL: str
    GEMINI_TIMEOUT: int

    # ── Telegram Webhook ───────────────────────────────────────────────────────
    WEBHOOK_BASE_URL: str           # no trailing slash
    WEBHOOK_SECRET: str


_REQUIRED_KEYS = [
    "NOTION_API_KEY",
    "NOTION_TASKS_DB_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATABASE_URL",
    "WEBHOOK_BASE_URL",
]


def load_settings() -> Settings:
    """Read env, validate required keys, return immutable Settings."""
    missing = [k for k in _REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
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

        # Database
        DATABASE_URL               = os.environ["DATABASE_URL"],

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


settings: Settings = load_settings()
