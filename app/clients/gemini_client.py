"""
Google Gemini client via the OpenAI-compatible API.

Why Gemini instead of OpenRouter?
──────────────────────────────────
• Stable model ID — google maintains it directly, no endpoint churn
• 1,500 requests/day free, no credit card required
• No routing layer — one fewer failure point
• OpenAI-compatible format — minimal code change from the OpenRouter version

API details
───────────
Google exposes an OpenAI-compatible endpoint at:
  https://generativelanguage.googleapis.com/v1beta/openai/

Auth is a standard Bearer token using your GEMINI_API_KEY.
Get a free key at: https://aistudio.google.com/apikey

The request/response shape is identical to OpenAI — same messages array,
same choices[0].message.content response path.
"""

import requests
from typing import Optional

from app.config import settings
from app.core.exceptions import OpsAgentError
from app.logger import get_logger

log = get_logger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiError(OpsAgentError):
    """Raised when the Gemini API returns an error or times out."""


def chat_completion(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> str:
    """
    Send a chat completion request to Gemini and return the response text.

    Parameters
    ----------
    system_prompt : Instructions that set the AI's behaviour and output format.
    user_message  : The actual input to process.
    model         : Model ID override. Defaults to settings.GEMINI_MODEL.
    temperature   : 0.0 = deterministic, 1.0 = creative. Keep low for parsing.
    max_tokens    : Hard cap on response length.

    Returns
    -------
    The assistant's reply as a plain string.

    Raises
    ------
    GeminiError on any HTTP, network, or response parsing failure.
    """
    selected_model = model or settings.GEMINI_MODEL

    payload = {
        "model":       selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {settings.GEMINI_API_KEY}",
        "Content-Type":  "application/json",
    }

    log.debug(
        "Gemini request — model=%s  system_chars=%d  user_chars=%d",
        selected_model, len(system_prompt), len(user_message),
    )

    try:
        response = requests.post(
            f"{GEMINI_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.GEMINI_TIMEOUT,
        )
    except requests.Timeout:
        raise GeminiError(
            f"Gemini timed out after {settings.GEMINI_TIMEOUT}s"
        )
    except requests.RequestException as exc:
        raise GeminiError(f"Gemini network error: {exc}") from exc

    if response.status_code != 200:
        raise GeminiError(
            f"Gemini returned HTTP {response.status_code}: {response.text[:300]}"
        )

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        if content is None:
            raise GeminiError("Gemini returned null content")
        return content.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(
            f"Unexpected Gemini response shape: {exc} — {str(data)[:300]}"
        ) from exc
