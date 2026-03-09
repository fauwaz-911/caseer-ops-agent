"""
AI service — three-layer fallback architecture.

Every AI call goes through this priority chain:

  Layer 1 — Groq
    Primary provider. 14,400 free requests/day on llama-3.1-8b-instant.
    Fast, reliable, generous quota. Used for all AI calls first.

  Layer 2 — Gemini
    Secondary provider. 1,500 free requests/day on gemini-2.0-flash.
    Catches Groq failures, rate limits, or quota exhaustion.

  Layer 3 — Rule-based classifier (intent parsing only)
    Zero external calls. Keyword pattern matching.
    Handles the 10 most common commands without any API.
    This layer means the system NEVER goes fully silent for known commands.

The chain is tried in order for each call type:
  parse_intent()        → Groq → Gemini → Rule classifier → unknown
  enrich_notification() → Groq → Gemini → original raw text (no crash)
  free_response()       → Groq → Gemini → static fallback message
"""

import json
import re
from typing import Optional

from app.models.intent import Intent, VALID_ACTIONS
from app.clients.groq_client import chat_completion as groq_complete, GroqError
from app.clients.gemini_client import chat_completion as gemini_complete, GeminiError
from app.services.rule_classifier import classify as rule_classify
from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED: LLM call with automatic Groq → Gemini fallback
# ─────────────────────────────────────────────────────────────────────────────

def _llm_call(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> Optional[str]:
    """
    Try Groq first, then Gemini. Returns None if both fail.

    This is the only place in the codebase that knows about both providers.
    All other functions call this and handle the None case themselves.
    """
    # ── Layer 1: Groq ─────────────────────────────────────────────────────────
    try:
        result = groq_complete(
            system_prompt=system_prompt,
            user_message=user_message,
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=settings.GROQ_TIMEOUT,
        )
        log.debug("AI response via Groq (%s)", settings.GROQ_MODEL)
        return result
    except GroqError as exc:
        log.warning("Groq unavailable — trying Gemini: %s", exc)

    # ── Layer 2: Gemini ───────────────────────────────────────────────────────
    try:
        result = gemini_complete(
            system_prompt=system_prompt,
            user_message=user_message,
            model=settings.GEMINI_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        log.warning("AI response via Gemini fallback (%s)", settings.GEMINI_MODEL)
        return result
    except GeminiError as exc:
        log.warning("Gemini unavailable — both AI providers failed: %s", exc)

    return None  # Both providers failed — caller handles this


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
  "ai_reply": "<optional: short natural language response for free_response or unknown>"
}

Valid actions:
- force_reminder   : Run the reminder engine / check for urgent or overdue tasks
- morning_brief    : Send the morning task summary
- evening_brief    : Send the evening wrap-up / end of day summary
- send_update      : Send the current full task list
- clear_state      : Reset / clear the reminder state or alerts
- test_telegram    : Test the Telegram connection / send a ping
- status           : Report system status, scheduler info, health
- free_response    : General question or conversation — answer in ai_reply
- unknown          : Cannot classify — ask for clarification in ai_reply

Confidence guide:
  0.9–1.0 = very clear command
  0.7–0.9 = likely correct
  0.5–0.7 = uncertain, lean toward free_response
  below 0.5 = use unknown

Examples:
User: "what tasks are overdue?" → {"action":"force_reminder","confidence":0.92,"ai_reply":""}
User: "send morning brief"      → {"action":"morning_brief","confidence":0.97,"ai_reply":""}
User: "are you working?"        → {"action":"status","confidence":0.85,"ai_reply":""}
User: "what's the capital of France?" → {"action":"free_response","confidence":0.99,"ai_reply":"The capital of France is Paris."}
User: "blah xyz"                → {"action":"unknown","confidence":0.95,"ai_reply":"I didn't understand that. You can ask me to check tasks, send a brief, or check system status."}
""".strip()


def parse_intent(message: str) -> Intent:
    """
    Classify a user message into a structured Intent.

    Order:
      1. Groq  → parse JSON response
      2. Gemini → parse JSON response
      3. Rule classifier → keyword matching, no API
      4. Unknown intent with helpful fallback message
    """
    log.info("Parsing intent for message: %r", message[:100])

    # ── Layers 1 & 2: AI providers ────────────────────────────────────────────
    raw_response = _llm_call(
        system_prompt=_INTENT_SYSTEM_PROMPT,
        user_message=message,
        temperature=0.1,
        max_tokens=200,
    )

    if raw_response:
        parsed = _parse_json_response(raw_response)
        if parsed:
            action = parsed.get("action", "unknown")
            if action not in VALID_ACTIONS:
                action = "unknown"
            intent = Intent(
                action=action,
                confidence=float(parsed.get("confidence", 0.5)),
                raw_message=message,
                ai_reply=parsed.get("ai_reply", ""),
            )
            log.info("Intent parsed via AI  action=%s  confidence=%.2f", intent.action, intent.confidence)
            return intent
        log.warning("AI returned unparseable JSON: %r", raw_response[:200])

    # ── Layer 3: Rule-based classifier ────────────────────────────────────────
    rule_intent = rule_classify(message)
    if rule_intent:
        log.warning(
            "Both AI providers failed — using rule classifier  action=%s",
            rule_intent.action,
        )
        return rule_intent

    # ── Layer 4: Full fallback — unknown with helpful message ─────────────────
    log.warning("All layers failed — returning unknown intent for: %r", message[:80])
    return Intent.unknown(
        raw_message=message,
        ai_reply=(
            "My AI is temporarily unavailable, but I can still handle direct commands.\n\n"
            "Try one of these:\n"
            "• <b>check my tasks</b>\n"
            "• <b>morning brief</b>\n"
            "• <b>evening brief</b>\n"
            "• <b>show all tasks</b>\n"
            "• <b>status</b>"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

_ENRICH_SYSTEM_PROMPT = """
You are the notification writer for Ops Agent, an AI executive assistant.

Rewrite the raw task data as a clear, intelligent, professional message for a busy executive.

Rules:
- Concise — no fluff or filler
- Preserve all task names and due dates exactly as given — never invent details
- Use HTML bold (<b>tags</b>) sparingly for emphasis
- One short priority insight is welcome if relevant
- Maximum 300 words
- No greetings or sign-offs — just the content
""".strip()


def enrich_notification(raw_text: str, context: str = "") -> str:
    """
    Rewrite raw task data through AI for a more intelligent notification.
    Falls back to raw_text unchanged if both AI providers fail.
    """
    if not raw_text.strip():
        return raw_text

    user_message = f"Context: {context}\n\n{raw_text}" if context else raw_text

    result = _llm_call(
        system_prompt=_ENRICH_SYSTEM_PROMPT,
        user_message=user_message,
        temperature=0.4,
        max_tokens=400,
    )

    if result:
        return result

    # Both providers failed — send the original text rather than nothing
    log.warning("Notification enrichment failed — using raw text")
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
    Generate a conversational AI reply. Falls back to a static message
    if both providers are unavailable.
    """
    result = _llm_call(
        system_prompt=_FREE_RESPONSE_SYSTEM_PROMPT,
        user_message=message,
        temperature=0.6,
        max_tokens=300,
    )

    if result:
        return result

    return (
        "My AI is temporarily unavailable. I can still run your task reminders, "
        "briefs, and updates — just send a direct command like "
        "<b>check my tasks</b> or <b>morning brief</b>."
    )


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json_response(text: str) -> Optional[dict]:
    """
    Safely parse JSON from AI output. Strips markdown fences and
    attempts to extract the first JSON object if the model added
    surrounding text.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
