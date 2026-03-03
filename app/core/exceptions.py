"""
Domain-specific exceptions.

Using typed exceptions instead of bare `Exception` lets callers catch
exactly what they expect and lets logs carry the right class name.
"""


class OpsAgentError(Exception):
    """Base for all application errors."""


class NotionError(OpsAgentError):
    """Raised when the Notion API request fails."""


class TelegramError(OpsAgentError):
    """Raised when Telegram delivery fails after all retries."""


class ConfigError(OpsAgentError):
    """Raised on missing or invalid configuration."""
