"""
OpenRouter API client.

What is OpenRouter?
───────────────────
OpenRouter is a unified API gateway that lets you call many different
LLMs (Mistral, Llama, Gemma, Claude, GPT-4, etc.) through a single
endpoint using the OpenAI message format. This means we can swap models
by changing one config value — no code changes needed.

API format
──────────
OpenRouter uses the exact same request/response shape as the OpenAI
chat completions API. The only differences are:
  • Base URL: https://openrouter.ai/api/v1
  • Auth header: Authorization: Bearer <OPENROUTER_API_KEY>
  • Optional header: HTTP-Referer (identifies your app in their dashboard)

Free models
───────────
Models ending in :free are rate-limited but cost $0. Good for development
and low-volume production use. Current good free options:
  • mistralai/mistral-7b-instruct:free    — fast, good instruction following
  • meta-llama/llama-3.1-8b-instruct:free — strong reasoning
  • google/gemma-3-4b-it:free             — lightweight and fast

This module is intentionally low-level — it just makes the HTTP call
and returns the raw text. The ai_service layer sits on top of this and
handles prompting, parsing, and business logic.
"""

import requests
from typing import Optional

from app.config import settings
from app.core.exceptions import OpsAgentError
from app.logger import get_logger

log = get_logger(__name__)


class OpenRouterError(OpsAgentError):
    """Raised when the OpenRouter API returns an error or times out."""


def chat_completion(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.2,      # low temperature = more deterministic/consistent
    max_tokens: int = 512,
) -> str:
    """
    Send a chat completion request to OpenRouter and return the response text.

    Parameters
    ----------
    system_prompt : Instructions that set the AI's behaviour and output format.
    user_message  : The actual input to process (user's message or task data).
    model         : OpenRouter model slug. Defaults to settings.OPENROUTER_MODEL.
    temperature   : 0.0 = deterministic, 1.0 = creative. Keep low for parsing.
    max_tokens    : Hard cap on response length.

    Returns
    -------
    The assistant's reply as a plain string.

    Raises
    ------
    OpenRouterError on any HTTP or network failure.
    """
    # Use the configured model if none explicitly requested
    selected_model = model or settings.OPENROUTER_MODEL

    # Build the standard OpenAI-compatible messages array
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ]

    # Request payload — identical to OpenAI format
    payload = {
        "model":       selected_model,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        # HTTP-Referer identifies your app in the OpenRouter dashboard
        # (optional but good practice — helps with debugging usage)
        "HTTP-Referer":  settings.WEBHOOK_BASE_URL,
        "X-Title":       "Ops Agent",
    }

    log.debug(
        "OpenRouter request — model=%s  system_chars=%d  user_chars=%d",
        selected_model, len(system_prompt), len(user_message),
    )

    try:
        response = requests.post(
            f"{settings.OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.OPENROUTER_TIMEOUT,
        )
    except requests.Timeout:
        raise OpenRouterError(
            f"OpenRouter timed out after {settings.OPENROUTER_TIMEOUT}s "
            f"(model={selected_model})"
        )
    except requests.RequestException as exc:
        raise OpenRouterError(f"OpenRouter network error: {exc}") from exc

    # Check HTTP status before trying to parse the body
    if response.status_code != 200:
        raise OpenRouterError(
            f"OpenRouter returned HTTP {response.status_code}: {response.text[:300]}"
        )

    data = response.json()

    # Defensive extraction — the response shape can vary slightly between models
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(
            f"Unexpected OpenRouter response shape: {exc}  body={str(data)[:300]}"
        ) from exc

    log.debug(
        "OpenRouter response — model=%s  response_chars=%d",
        selected_model, len(text),
    )
    return text
