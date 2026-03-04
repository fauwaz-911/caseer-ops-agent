"""
OpenRouter API client with automatic model fallback.

If the configured model returns a 404 (endpoint gone), this client
automatically tries the next model in the fallback list rather than
failing immediately. This makes the system resilient to OpenRouter's
free tier endpoint churn.

Fallback order
──────────────
1. settings.OPENROUTER_MODEL (your configured model)
2. openrouter/free           (OpenRouter's auto-router — picks any available free model)
3. deepseek/deepseek-chat:free
4. google/gemma-3-4b-it:free

As soon as one model succeeds, we stop and return the result.
"""

import requests
from typing import Optional

from app.config import settings
from app.core.exceptions import OpsAgentError
from app.logger import get_logger

log = get_logger(__name__)


class OpenRouterError(OpsAgentError):
    """Raised when all model fallbacks are exhausted."""


# Fallback chain — tried in order when a model returns 404
# openrouter/free is the auto-router: picks whatever free model is alive right now
_FALLBACK_MODELS = [
    "openrouter/free",
    "deepseek/deepseek-chat:free",
    "google/gemma-3-4b-it:free",
]


def chat_completion(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> str:
    """
    Send a chat completion request to OpenRouter, with automatic fallback.

    Tries the configured model first, then works through _FALLBACK_MODELS
    until one succeeds. Only raises OpenRouterError if every option fails.

    Parameters
    ----------
    system_prompt : Instructions that set the AI's behaviour and output format.
    user_message  : The actual input to process.
    model         : Override model slug. Defaults to settings.OPENROUTER_MODEL.
    temperature   : 0.0 = deterministic, 1.0 = creative.
    max_tokens    : Hard cap on response length.
    """
    # Build the full list to try: configured model first, then fallbacks
    # (deduplicate in case configured model is already in fallback list)
    primary = model or settings.OPENROUTER_MODEL
    candidates = [primary] + [m for m in _FALLBACK_MODELS if m != primary]

    last_error = None

    for candidate in candidates:
        try:
            result = _single_request(
                system_prompt=system_prompt,
                user_message=user_message,
                model=candidate,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            # Log if we fell back from the primary model
            if candidate != primary:
                log.warning(
                    "Used fallback model %s (primary %s was unavailable)",
                    candidate, primary,
                )
            return result

        except _ModelUnavailable as exc:
            # 404 — this model is gone, try the next one
            log.warning("Model %s unavailable, trying next fallback: %s", candidate, exc)
            last_error = exc
            continue

        except OpenRouterError:
            # Non-404 error (auth, timeout, malformed response) — don't retry
            raise

    # Every candidate failed
    raise OpenRouterError(
        f"All OpenRouter models failed. Last error: {last_error}\n"
        f"Tried: {', '.join(candidates)}"
    )


# ── Internal ──────────────────────────────────────────────────────────────────

class _ModelUnavailable(Exception):
    """Internal signal: this model returned 404, try the next one."""


def _single_request(
    system_prompt: str,
    user_message: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Make one HTTP request to OpenRouter. Raises _ModelUnavailable on 404."""
    payload = {
        "model":       model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  settings.WEBHOOK_BASE_URL,
        "X-Title":       "Ops Agent",
    }

    log.debug("OpenRouter request — model=%s", model)

    try:
        response = requests.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.OPENROUTER_TIMEOUT,
        )
    except requests.Timeout:
        raise OpenRouterError(
            f"OpenRouter timed out after {settings.OPENROUTER_TIMEOUT}s (model={model})"
        )
    except requests.RequestException as exc:
        raise OpenRouterError(f"OpenRouter network error: {exc}") from exc

    # 404 = model endpoint gone — signal to try next fallback
    if response.status_code == 404:
        raise _ModelUnavailable(response.text[:200])

    if response.status_code != 200:
        raise OpenRouterError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:300]}"
        )

    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(
            f"Unexpected response shape: {exc} — {str(data)[:300]}"
        ) from exc

    log.debug("OpenRouter response — model=%s  chars=%d", model, len(text))
    return text
