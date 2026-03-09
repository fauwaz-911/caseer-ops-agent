"""
Groq API client.

Why Groq?
─────────
Groq offers free inference at 14,400 requests/day — nearly 10x Gemini's
free tier. It uses the same OpenAI-compatible format so the change from
Gemini is minimal. The only differences are the base URL and API key.

Free tier limits (as of 2026):
  • 14,400 requests/day
  • 30 requests/minute
  • 6,000 tokens/minute

Model: llama-3.3-70b-versatile
  • Excellent instruction following
  • Strong JSON output (needed for intent parsing)
  • Fast inference via Groq's LPU hardware

Get a free API key at: console.groq.com
"""

import requests
from typing import Optional

from app.config import settings
from app.core.exceptions import OpsAgentError
from app.logger import get_logger

log = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqError(OpsAgentError):
    """Raised when the Groq API returns an error or times out."""


def chat_completion(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> str:
    """
    Send a chat completion request to Groq and return the response text.

    Parameters
    ----------
    system_prompt : Instructions that set the AI's behaviour and output format.
    user_message  : The actual input to process.
    model         : Model override. Defaults to settings.GROQ_MODEL.
    temperature   : 0.0 = deterministic, 1.0 = creative.
    max_tokens    : Hard cap on response length.

    Returns
    -------
    The assistant's reply as a plain string.

    Raises
    ------
    GroqError on any HTTP, network, or response parsing failure.
    """
    selected_model = model or settings.GROQ_MODEL

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
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }

    log.debug(
        "Groq request — model=%s  system_chars=%d  user_chars=%d",
        selected_model, len(system_prompt), len(user_message),
    )

    try:
        response = requests.post(
            f"{GROQ_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=settings.GROQ_TIMEOUT,
        )
    except requests.Timeout:
        raise GroqError(f"Groq timed out after {settings.GROQ_TIMEOUT}s")
    except requests.RequestException as exc:
        raise GroqError(f"Groq network error: {exc}") from exc

    if response.status_code == 429:
        raise GroqError(f"Groq rate limit hit: {response.text[:200]}")

    if response.status_code != 200:
        raise GroqError(
            f"Groq returned HTTP {response.status_code}: {response.text[:300]}"
        )

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
        if content is None:
            raise GroqError("Groq returned null content")
        return content.strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError(
            f"Unexpected Groq response shape: {exc} — {str(data)[:300]}"
        ) from exc
