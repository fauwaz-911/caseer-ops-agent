"""
AI service.

This is the intelligence layer. It sits between raw user messages and
system actions, and between raw task data and formatted notifications.

Two responsibilities
────────────────────

1. parse_intent(message)
   Takes a user's raw Telegram message and asks the AI to classify what
   they want. Returns a structured Intent object that the webhook router
   uses to decide what action to take (or confirm first).

   Example:
     "what's due today?"    → Intent(action="force_reminder", confidence=0.9)
     "how are you?"         → Intent(action="free_response",  confidence=0.95)
     "send the morning msg" → Intent(action="morning_brief",  confidence=0.85)

2. enrich_notification(raw_text, context)
   Takes plain task data (e.g. a list of overdue items) and asks the AI
   to reformat it into a more intelligent, human-readable message before
   it's sent to Telegram. This makes automated notifications feel like
   an assistant wrote them, not a cron job.

Prompting strategy
──────────────────
For intent parsing we use a strict JSON-output prompt so the response
is always machine-parseable. The system prompt tells the AI exactly
what JSON keys to return, keeping temperature low (0.1) for consistency.

For enrichment we allow more creative output (temperature 0.4) because
we want it to read naturally, not robotically.

Fallback policy
───────────────
If the AI returns invalid JSON, times out, or is unavailable, we fall
back gracefully — intent parsing returns Intent(action="unknown") and
enrichment returns the original raw text unchanged. The system never
crashes because the AI misbehaved.
"""

import json
import re
from typing import Optional

from app.models.intent import Intent, VALID_ACTIONS
from app.clients.openrouter_client import chat_completion, OpenRouterError
from app.core.exceptions import OpsAgentError
from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# INTENT PARSING
# ─────────────────────────────────────────────────────────────────────────────

# The system prompt for intent classification.
# Key design choices:
#   • We tell the AI the exact JSON schema we expect
#   • We list every valid action so it can't invent new ones
#   • We explain the confidence field so it uses it meaningfully
#   • We give examples to reduce ambiguity
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
- 0.9–1.0 : Very clear command, no ambiguity
- 0.7–0.9 : Likely correct, reasonable interpretation
- 0.5–0.7 : Uncertain — lean toward free_response or unknown
- Below 0.5: Use unknown

Examples:
User: "what tasks are overdue?" → {"action": "force_reminder", "confidence": 0.92, "ai_reply": ""}
User: "send me the morning brief" → {"action": "morning_brief", "confidence": 0.97, "ai_reply": ""}
User: "are you working?" → {"action": "status", "confidence": 0.85, "ai_reply": ""}
User: "reset alerts" → {"action": "clear_state", "confidence": 0.88, "ai_reply": ""}
User: "what's the capital of France?" → {"action": "free_response", "confidence": 0.99, "ai_reply": "The capital of France is Paris."}
User: "blah blah xyz" → {"action": "unknown", "confidence": 0.95, "ai_reply": "I didn't understand that. You can ask me to check tasks, send a brief, or check system status."}
""".strip()


def parse_intent(message: str) -> Intent:
    """
    Use the AI to classify a user's message into a structured Intent.

    The AI returns JSON which we parse into an Intent object. If anything
    goes wrong (bad JSON, timeout, unexpected response), we return a safe
    fallback Intent(action="unknown") so the webhook handler always has
    something to work with.

    Parameters
    ----------
    message : The raw text the user sent to the Telegram bot.

    Returns
    -------
    Intent object with action, confidence, and optional ai_reply.
    """
    log.info("Parsing intent for message: %r", message[:100])

    try:
        raw_response = chat_completion(
            system_prompt=_INTENT_SYSTEM_PROMPT,
            user_message=message,
            model=settings.OPENROUTER_MODEL,
            temperature=0.1,       # very low — we want consistent, deterministic classification
            max_tokens=200,        # intent JSON is short — no need for more
        )
    except OpenRouterError as exc:
        # AI is unavailable — don't crash, return unknown intent
        log.warning("OpenRouter unavailable for intent parsing: %s", exc)
        return Intent.unknown(
            raw_message=message,
            ai_reply="I'm having trouble thinking right now. Please try again in a moment.",
        )

    # Parse the JSON response from the AI
    parsed = _parse_json_response(raw_response)
    if not parsed:
        log.warning("AI returned unparseable JSON: %r", raw_response[:200])
        return Intent.unknown(
            raw_message=message,
            ai_reply="I had trouble understanding that. Could you rephrase?",
        )

    # Extract and validate fields
    action = parsed.get("action", "unknown")
    if action not in VALID_ACTIONS:
        log.warning("AI returned invalid action %r — falling back to unknown", action)
        action = "unknown"

    confidence = float(parsed.get("confidence", 0.5))
    ai_reply   = parsed.get("ai_reply", "")

    intent = Intent(
        action=action,
        confidence=confidence,
        raw_message=message,
        ai_reply=ai_reply,
    )

    log.info(
        "Intent parsed  action=%s  confidence=%.2f  has_reply=%s",
        intent.action, intent.confidence, bool(intent.ai_reply),
    )
    return intent


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

# System prompt for notification enrichment.
# The AI receives raw task data and rewrites it as a polished message.
_ENRICH_SYSTEM_PROMPT = """
You are the notification writer for an AI executive assistant called Ops Agent.

You receive raw task data or system output and rewrite it as a clear,
intelligent, professional message for a busy executive.

Rules:
- Keep it concise — no fluff, no filler words
- Use plain language — no jargon
- Preserve all task names and due dates exactly as given — never invent details
- Use HTML formatting (bold with <b>tags</b>, not markdown)
- You may add one short insight or priority recommendation if relevant
- Maximum 300 words
- Do NOT add greetings or sign-offs — just the content
""".strip()


def enrich_notification(raw_text: str, context: str = "") -> str:
    """
    Rewrite a raw notification through the AI to make it more intelligent
    and human-readable before sending to Telegram.

    Parameters
    ----------
    raw_text : The plain task list or system message to enrich.
    context  : Optional context string (e.g. "this is a morning brief").

    Returns
    -------
    The AI-enriched message string, or raw_text unchanged if AI fails.
    """
    if not raw_text.strip():
        return raw_text

    # Build the user message with optional context
    user_message = raw_text
    if context:
        user_message = f"Context: {context}\n\n{raw_text}"

    log.debug("Enriching notification  chars=%d  context=%r", len(raw_text), context[:50])

    try:
        enriched = chat_completion(
            system_prompt=_ENRICH_SYSTEM_PROMPT,
            user_message=user_message,
            model=settings.OPENROUTER_ENRICH_MODEL,
            temperature=0.4,       # slightly higher — allow natural language variation
            max_tokens=400,
        )
        log.debug("Notification enriched  original=%d  enriched=%d chars", len(raw_text), len(enriched))
        return enriched

    except OpenRouterError as exc:
        # AI unavailable — send the original text rather than nothing
        log.warning("Notification enrichment failed — using raw text: %s", exc)
        return raw_text


# ─────────────────────────────────────────────────────────────────────────────
# FREE RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

_FREE_RESPONSE_SYSTEM_PROMPT = """
You are Ops Agent, an AI executive assistant. You help with task management,
scheduling, reminders, and general operational questions.

You can:
- Answer questions about tasks and deadlines
- Explain what the system does
- Respond to general operational questions
- Politely decline off-topic requests

Keep responses brief and professional. Use HTML bold (<b>text</b>) sparingly
for emphasis. Maximum 200 words.
""".strip()


def free_response(message: str) -> str:
    """
    Generate a conversational AI reply for general questions or chat.

    Used when the intent is FREE_RESPONSE — no system action is taken,
    just a natural language reply.

    Returns the AI's reply, or a fallback string if AI is unavailable.
    """
    log.debug("Generating free response for: %r", message[:80])
    try:
        return chat_completion(
            system_prompt=_FREE_RESPONSE_SYSTEM_PROMPT,
            user_message=message,
            model=settings.OPENROUTER_MODEL,
            temperature=0.6,       # more conversational
            max_tokens=300,
        )
    except OpenRouterError as exc:
        log.warning("Free response failed: %s", exc)
        return "I'm having trouble connecting right now. Please try again shortly."


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json_response(text: str) -> Optional[dict]:
    """
    Safely parse a JSON string from AI output.

    Free models sometimes wrap JSON in markdown code fences or add
    a short explanation before the JSON. This function strips common
    wrappers and attempts to extract the JSON object.

    Returns the parsed dict, or None if parsing fails completely.
    """
    # Strip markdown code fences if present: ```json ... ``` or ``` ... ```
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

    # Try direct parse first (the happy path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find the first { ... } block in the string
    # (handles cases where the AI wrote text before or after the JSON)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None
