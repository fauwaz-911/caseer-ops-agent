"""
Telegram notification client.

Features
────────
• Eager env-var validation (via config.settings)
• Retry with exponential back-off
• Full HTTP response validation + structured logging
• Raises RuntimeError on terminal failure so callers can react
"""

import time
import requests

from .config import settings
from .logger import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/{method}"


def _build_url(method: str) -> str:
    return _BASE_URL.format(token=settings.telegram_bot_token, method=method)


def send_message(
    text: str,
    chat_id: str | None = None,
    parse_mode: str = "HTML",
) -> dict:
    """
    Send a Telegram message with retry + back-off.

    Parameters
    ----------
    text       : Message body (HTML formatting supported by default).
    chat_id    : Override the default chat ID (enables multi-user routing).
    parse_mode : "HTML" | "Markdown" | "MarkdownV2" | None

    Returns
    -------
    Telegram API response dict on success.

    Raises
    ------
    RuntimeError if all retries are exhausted.
    """
    target_chat = chat_id or settings.telegram_chat_id
    url = _build_url("sendMessage")
    payload: dict = {"chat_id": target_chat, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_exc: Exception | None = None

    for attempt in range(1, settings.telegram_max_retries + 1):
        try:
            log.debug(
                "Telegram send attempt %d/%d → chat_id=%s  chars=%d",
                attempt, settings.telegram_max_retries, target_chat, len(text),
            )
            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                result = response.json()
                log.info(
                    "Telegram message delivered → chat_id=%s  message_id=%s",
                    target_chat,
                    result.get("result", {}).get("message_id", "?"),
                )
                return result

            # Non-200: log and maybe retry
            log.warning(
                "Telegram API non-200 on attempt %d: status=%d  body=%s",
                attempt, response.status_code, response.text[:300],
            )
            last_exc = RuntimeError(
                f"Telegram API returned {response.status_code}: {response.text[:300]}"
            )

        except requests.RequestException as exc:
            log.warning("Telegram network error on attempt %d: %s", attempt, exc)
            last_exc = exc

        if attempt < settings.telegram_max_retries:
            sleep_secs = settings.telegram_retry_backoff * (2 ** (attempt - 1))
            log.debug("Retrying Telegram in %.1fs …", sleep_secs)
            time.sleep(sleep_secs)

    log.error("All %d Telegram attempts exhausted.", settings.telegram_max_retries)
    raise RuntimeError(
        f"Telegram delivery failed after {settings.telegram_max_retries} attempts."
    ) from last_exc


def send_test_ping(chat_id: str | None = None) -> dict:
    """Send a lightweight connectivity test message."""
    return send_message("✅ <b>Ops Agent</b> — pipeline check OK", chat_id=chat_id)
