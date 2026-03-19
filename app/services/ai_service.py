"""
AI service.

Routes all AI calls to the configured provider (Groq or Gemini).
Change AI_PROVIDER env var to switch with no code change.

Updated in this version
───────────────────────
Intent prompt now includes update_task and add_task with parameter
extraction. The AI returns a parameters object alongside the action
so the webhook router knows exactly which task to update and to what.
"""

import json
import re
from typing import Optional

from app.config import settings
from app.models.intent import Intent, VALID_ACTIONS
from app.logger import get_logger

log = get_logger(__name__)


# ── Provider routing ──────────────────────────────────────────────────────────

def _chat(system_prompt: str, user_message: str,
          temperature: float = 0.2, max_tokens: int = 512) -> str:
    """
    Route to Gemini or Groq based on AI_PROVIDER setting.
    Reads the setting at call time — switching providers needs no restart.
    """
    if settings.AI_PROVIDER == "gemini":
        from app.clients.gemini_client import chat_completion
    else:
        from app.clients.groq_client import chat_completion

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
Classify the user's message and extract any parameters needed to act on it.

Respond ONLY with a valid JSON object — no explanation, no markdown fences.

JSON schema:
{
  "action": "<one of the valid actions below>",
  "confidence": <float 0.0–1.0>,
  "ai_reply": "<short reply for free_response or unknown, else empty string>",
  "parameters": {<structured data for update_task or add_task, else empty object>}
}

Valid actions:
- force_reminder  : Check for urgent tasks / run reminder engine
- morning_brief   : Send morning task summary
- evening_brief   : Send evening wrap-up summary
- send_update     : Send full current task list
- clear_state     : Reset reminder alerts
- test_telegram   : Test Telegram connection
- status          : Report system health and scheduler status
- update_task     : Change status of an existing task
- add_task        : Create a new task
- free_response   : General question — answer in ai_reply
- unknown         : Cannot classify — ask for clarification in ai_reply

Parameters for update_task:
{
  "task_ref": "1",               // task number (e.g. "1", "2") OR task name
  "new_status": "Completed"      // one of: Pending, In Progress, Stopped, Completed
}

Parameters for add_task:
{
  "task_name": "review investor deck",
  "due_date": "2026-03-22"       // ISO date string, or null if no date mentioned
}

Confidence guide:
- 0.9–1.0: Very clear command
- 0.7–0.9: Likely correct
- Below 0.7: Use free_response or unknown

Examples:
User: "mark task 1 as complete"
→ {"action":"update_task","confidence":0.96,"ai_reply":"","parameters":{"task_ref":"1","new_status":"Completed"}}

User: "change task 2 to in progress"
→ {"action":"update_task","confidence":0.94,"ai_reply":"","parameters":{"task_ref":"2","new_status":"In Progress"}}

User: "add task: review investor deck due Friday"
→ {"action":"add_task","confidence":0.93,"ai_reply":"","parameters":{"task_name":"review investor deck","due_date":null}}

User: "mark plan full crypto bot as stopped"
→ {"action":"update_task","confidence":0.91,"ai_reply":"","parameters":{"task_ref":"plan full crypto bot","new_status":"Stopped"}}

User: "what tasks are overdue?"
→ {"action":"force_reminder","confidence":0.92,"ai_reply":"","parameters":{}}

User: "how are you?"
→ {"action":"free_response","confidence":0.99,"ai_reply":"I'm running well and ready to help manage your tasks.","parameters":{}}
""".strip()


def parse_intent(message: str) -> Intent:
    """
    Use the AI to classify a user's message into a structured Intent.

    Returns Intent(action="unknown") on any AI failure — never raises.
    """
    log.info("Parsing intent for message: %r", message[:100])

    try:
        raw = _chat(
            system_prompt=_INTENT_SYSTEM_PROMPT,
            user_message=message,
            temperature=0.1,
            max_tokens=300,
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
        log.warning("AI returned invalid action %r", action)
        action = "unknown"

    intent = Intent(
        action     = action,
        confidence = float(parsed.get("confidence", 0.5)),
        raw_message= message,
        ai_reply   = parsed.get("ai_reply", ""),
        parameters = parsed.get("parameters", {}),
    )

    log.info(
        "Intent parsed  action=%s  confidence=%.2f  provider=%s  params=%s",
        intent.action, intent.confidence, settings.AI_PROVIDER,
        str(intent.parameters)[:80],
    )
    return intent


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

_ENRICH_SYSTEM_PROMPT = """
You are the notification writer for Ops Agent, an AI executive assistant.
Rewrite raw task data as a clear, concise, professional Telegram message.

Rules:
- Preserve all task names and due dates exactly
- Use HTML bold (<b>text</b>) for emphasis, not markdown
- Add one short priority recommendation if relevant
- Maximum 300 words, no greetings or sign-offs
""".strip()


def enrich_notification(raw_text: str, context: str = "") -> str:
    """Rewrite raw task data through AI. Returns raw_text if AI fails."""
    if not raw_text.strip():
        return raw_text
    user_msg = f"Context: {context}\n\n{raw_text}" if context else raw_text
    try:
        return _chat(_ENRICH_SYSTEM_PROMPT, user_msg, temperature=0.4, max_tokens=400)
    except Exception as exc:
        log.warning("Notification enrichment failed — using raw text: %s", exc)
        return raw_text


# ─────────────────────────────────────────────────────────────────────────────
# FREE RESPONSE
# ─────────────────────────────────────────────────────────────────────────────

_FREE_RESPONSE_SYSTEM_PROMPT = """
You are Ops Agent, an AI executive assistant for task management and scheduling.
Keep responses brief and professional. Use HTML bold (<b>text</b>) sparingly.
Maximum 200 words.
""".strip()


def free_response(message: str) -> str:
    """Generate a conversational reply. Returns fallback string if AI fails."""
    try:
        return _chat(_FREE_RESPONSE_SYSTEM_PROMPT, message, temperature=0.6, max_tokens=300)
    except Exception as exc:
        log.warning("Free response failed: %s", exc)
        return "I'm having trouble connecting right now. Please try again shortly."


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> Optional[dict]:
    """Extract JSON from AI output, stripping any markdown fences."""
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None
