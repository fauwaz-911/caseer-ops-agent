"""
AI service.

Routes all AI calls through either Gemini or Groq depending on the
AI_PROVIDER setting. Change AI_PROVIDER env var to switch providers
with no code change and no redeploy.

Three responsibilities
──────────────────────
1. parse_intent(message)    — classify user's Telegram message into a
                              structured Intent object
2. enrich_notification(raw) — rewrite plain task data into a polished
                              Telegram-ready message
3. free_response(message)   — generate a conversational reply for
                              general questions

Fallback policy
───────────────
If the AI call fails for any reason (timeout, quota, network), every
function returns a safe fallback — parse_intent returns Intent(unknown),
enrich_notification returns the original raw text, free_response returns
a fixed apology string. The system never crashes because the AI failed.
"""

import json
import re
from typing import Optional

from app.config import settings
from app.models.intent import Intent, VALID_ACTIONS
from app.core.exceptions import OpsAgentError
from app.logger import get_logger

log = get_logger(__name__)


# ── Provider routing ──────────────────────────────────────────────────────────

def _chat(system_prompt: str, user_message: str,
          temperature: float = 0.2, max_tokens: int = 512) -> str:
    """
    Route a chat completion call to the configured AI provider.

    Reads AI_PROVIDER at call time so switching providers only requires
    an env var change — no restart, no code change.
    """
    if settings.AI_PROVIDER == "gemini":
        from app.clients.gemini_client import chat_completion, GeminiError as _Err
    else:
        from app.clients.groq_client import chat_completion, GroqError as _Err

    return chat_completion(
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

_INTENT_SYSTEM_PROMPT = """
You are the intent parser for an AI executive assistant called Ops Agent.
Your job is to read the user's message and classify what they want.

You MUST respond with ONLY a valid JSON object — no explanation, no markdown,
no code fences. Just the raw JSON.

JSON schema:
{
  "action": "<one of the valid actions listed below>",
  "confidence": <float between 0.0 and 1.0>,
  "ai_reply": "<optional: a short natural language response for free_response or unknown>"
}

Valid actions:
- force_reminder   : Run the reminder engine / check for urgent tasks
- morning_brief    : Send the morning task summary
- evening_brief    : Send the evening wrap-up / end of day summary
- send_update      : Send the current full task list
- clear_state      : Reset / clear the reminder state or alerts
- test_telegram    : Test the Telegram connection / send a ping
- status           : Report system status, scheduler info, health
- free_response    : General question or conversation — respond in ai_reply
- unknown          : Cannot confidently classify — ask for clarification in ai_reply

Confidence guide:
- 0.9–1.0 : Very clear command
- 0.7–0.9 : Likely correct
- 0.5–0.7 : Uncertain — lean toward free_response or unknown
- Below 0.5: Use unknown

Examples:
User: "what tasks are overdue?" → {"action": "force_reminder", "confidence": 0.92, "ai_reply": ""}
User: "send me the morning brief" → {"action": "morning_brief", "confidence": 0.97, "ai_reply": ""}
User: "are you working?" → {"action": "status", "confidence": 0.85, "ai_reply": ""}
User: "what's the capital of France?" → {"action": "free_response", "confidence": 0.99, "ai_reply": "The capital of France is Paris."}
User: "blah blah xyz" → {"action": "unknown", "confidence": 0.95, "ai_reply": "I didn't understand that. You can ask me to check tasks, send a brief, or check system status."}
""".strip()


def parse_intent(message: str) -> Intent:
    """
    Use the AI to classify a user's message into a structured Intent.

    Returns Intent(action="unknown") if the AI is unavailable or returns
    an unparseable response — never raises.
    """
    log.info("Parsing intent for message: %r", message[:100])

    try:
        raw = _chat(
            system_prompt=_INTENT_SYSTEM_PROMPT,
            user_message=message,
            temperature=0.1,
            max_tokens=200,
        )
    except Exception as exc:
        log.warning("AI unavailable for intent parsing: %s", exc)
        return Intent.unknown(
            raw_message=message,
            ai_reply="I'm having trouble thinking right now. Please try again in a moment.",
        )

    parsed = _parse_json(raw)
    if not parsed:
        log.warning("AI returned unparseable JSON: %r", raw[:200])
        return Intent.unknown(
            raw_message=message,
            ai_reply="I had trouble understanding that. Could you rephrase?",
        )

    action = parsed.get("action", "unknown")
    if action not in VALID_ACTIONS:
        log.warning("AI returned invalid action %r — falling back to unknown", action)
        action = "unknown"

    intent = Intent(
        action=action,
        confidence=float(parsed.get("confidence", 0.5)),
        raw_message=message,
        ai_reply=parsed.get("ai_reply", ""),
    )

    log.info(
        "Intent parsed  action=%s  confidence=%.2f  provider=%s",
        intent.action, intent.confidence, settings.AI_PROVIDER,
    )
    return intent


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

_ENRICH_SYSTEM_PROMPT = """
You are the notification writer for an AI executive assistant called Ops Agent.

You receive raw task data and rewrite it as a clear, concise, professional
message for a busy executive.

Rules:
- Keep it concise — no fluff
- Preserve all task names and due dates exactly as given
- Use HTML bold (<b>text</b>) for emphasis, not markdown
- You may add one short priority recommendation if relevant
- Maximum 300 words
- Do NOT add greetings or sign-offs
""".strip()


def enrich_notification(raw_text: str, context: str = "") -> str:
    """
    Rewrite raw task data through the AI before sending to Telegram.

    Returns raw_text unchanged if the AI is unavailable.
    """
    if not raw_text.strip():
        return raw_text

    user_message = f"Context: {context}\n\n{raw_text}" if context else raw_text

    try:
        enriched = _chat(
            system_prompt=_ENRICH_SYSTEM_PROMPT,
            user_message=user_message,
            temperature=0.4,
            max_tokens=400,
        )
        return enriched
    except Exception as exc:
        log.warning("Notification enrichment failed — using raw text: %s", exc)
        return raw_text


# ─────────────────────────────────────────────────────────────────────────────
# FREE RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

_FREE_RESPONSE_SYSTEM_PROMPT = """
You are Ops Agent, an AI executive assistant. You help with task management,
scheduling, reminders, and general operational questions.

Keep responses brief and professional. Use HTML bold (<b>text</b>) sparingly.
Maximum 200 words.
""".strip()


def free_response(message: str) -> str:
    """
    Generate a conversational reply for general questions.

    Returns a fallback string if the AI is unavailable.
    """
    try:
        return _chat(
            system_prompt=_FREE_RESPONSE_SYSTEM_PROMPT,
            user_message=message,
            temperature=0.6,
            max_tokens=300,
        )
    except Exception as exc:
        log.warning("Free response failed: %s", exc)
        return "I'm having trouble connecting right now. Please try again shortly."


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> Optional[dict]:
    """
    Safely extract a JSON object from AI output.

    Handles markdown fences and leading/trailing text that some models add.
    Returns None if no valid JSON can be extracted.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

    # Direct parse (happy path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract first {...} block if there's surrounding text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
