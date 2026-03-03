"""
Telegram notification service.

Features
────────
• Accepts an explicit Workspace — multi-tenant ready.
• Exponential back-off with a hard cap so sleep never exceeds 30s.
• Full HTTP response validation on every attempt.
• Raises TelegramError (typed) on terminal failure.
• ExecutionContext flows through for correlated log lines.
"""

from __future__ import annotations

import time
import requests

from app.config import settings
from app.models.workspace import Workspace
from app.core.exceptions import TelegramError
from app.core.execution_context import ExecutionContext
from app.logger import get_logger

_BACKOFF_CAP_SECONDS = 30
_log = get_logger(__name__)


def send_message(
    text: str,
    workspace: Workspace,
    ctx: ExecutionContext,
    parse_mode: str = "HTML",
) -> dict:
    """
    Send a Telegram message with retry and capped exponential back-off.

    Parameters
    ----------
    text       : Message body (HTML formatting).
    workspace  : Workspace carrying bot token + chat ID.
    ctx        : ExecutionContext for correlated logging.
    parse_mode : Telegram parse mode — HTML by default.

    Returns
    -------
    Telegram API response dict on success.

    Raises
    ------
    TelegramError after all retries are exhausted.
    """
    log = ctx.logger(__name__)
    url = f"https://api.telegram.org/bot{workspace.telegram_token}/sendMessage"
    payload = {
        "chat_id":    workspace.telegram_chat_id,
        "text":       text,
        "parse_mode": parse_mode,
    }
    max_retries = settings.TELEGRAM_MAX_RETRIES
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            log.debug(
                "Telegram send attempt %d/%d  chat_id=%s  chars=%d",
                attempt, max_retries, workspace.telegram_chat_id, len(text),
            )
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                result = response.json()
                log.info(
                    "Telegram message delivered  message_id=%s",
                    result.get("result", {}).get("message_id", "?"),
                )
                return result

            log.warning(
                "Telegram non-200 on attempt %d: status=%d  body=%s",
                attempt, response.status_code, response.text[:200],
            )
            last_exc = TelegramError(
                f"HTTP {response.status_code}: {response.text[:200]}"
            )

        except requests.RequestException as exc:
            log.warning("Telegram network error on attempt %d: %s", attempt, exc)
            last_exc = exc

        if attempt < max_retries:
            sleep = min(
                settings.TELEGRAM_RETRY_BACKOFF * (2 ** (attempt - 1)),
                _BACKOFF_CAP_SECONDS,
            )
            log.debug("Retrying Telegram in %.1fs …", sleep)
            time.sleep(sleep)

    log.error("All %d Telegram attempts exhausted.", max_retries)
    raise TelegramError(
        f"Telegram delivery failed after {max_retries} attempts."
    ) from last_exc
