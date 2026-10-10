"""LLM-based alternative to risk.py's regex detector.

Same categories, same points, same CallRiskScore/thresholds downstream —
only *how a segment gets classified into categories* changes. The
conversational agent's own LLM (Gemini, via settings.llm_base_url /
llm_api_key / llm_model — the same OpenAI-compatible provider gradbot
already talks to) extracts which categories are present in paraphrased or
novel wording a fixed word list would miss; it never assigns a score or
decides anything itself. Per the brief: "Model output alone cannot
authorise a connection" — the points and Amber/Red/Critical thresholds are
still risk.py's plain backend rule, applied to whatever categories the LLM
reports. No separate API key: this reuses the same provider configuration
gradbot is already running on.
"""

import json
import logging
from dataclasses import dataclass

import httpx

from .config import Settings
from .risk import POINTS_BY_CATEGORY, Signal

log = logging.getLogger(__name__)

_CATEGORIES = sorted(POINTS_BY_CATEGORY)

_SYSTEM_PROMPT = (
    "You detect scam-call risk signals in one segment of a live phone call "
    "transcript, spoken by the caller (a possible scammer) to an older adult. "
    "Categories:\n"
    "- family_claim: caller claims to be a relative (e.g. grandchild, child)\n"
    "- distress: caller claims to be in danger, arrested, injured, or in trouble\n"
    "- urgency: caller pressures for immediate action, no time to think\n"
    "- secrecy: caller asks to keep this call or the request secret\n"
    "- financial_request: caller asks for money, gift cards, crypto, bank details, or cash\n"
    "- extraction: caller asks for remote computer access, a verification code, "
    "or arranges physical pickup/delivery (e.g. a courier)\n"
    "Reply with ONLY a JSON object of the exact shape "
    '{"categories": ["..."]} listing categories clearly present in THIS segment '
    "(category names must come from the list above). Use an empty array if none "
    "clearly apply. Never guess from vague or ambiguous text — a bare mention of "
    "money or a relative's name alone is not enough; the caller must actually be "
    "making that kind of claim or request. Reply with nothing but that JSON object."
)


_WARNING_SYSTEM_PROMPT = (
    "You are a safety assistant, speaking out loud to an older adult during a live "
    "phone call that shows signs of being a scam. Write ONE short, calm sentence "
    "(max about 20 words), in the same language as the transcript segment you are "
    "given, appropriate to the current risk level.\n"
    "Rules, always:\n"
    "- Speak directly and calmly to the protected person, never to the caller.\n"
    '- NEVER say "this is a scam", "this call is fraudulent" or anything asserting '
    "fraud as settled fact — only suggest caution and offer to verify.\n"
    "- NEVER claim a family member asked you to monitor this call.\n"
    "- NEVER flatly instruct them to hang up — offer help verifying instead.\n"
    "- Never invent facts about the caller or claim certainty you don't have.\n"
    "Tune the tone to the risk level given to you:\n"
    "- amber: a gentle, barely-noticeable check-in.\n"
    "- red: a clearer, more direct caution, offering to help verify.\n"
    "- critical: pause and calmly offer to connect them with someone who can help "
    "verify right now.\n"
    "Reply with ONLY the sentence to speak — no quotes, no explanation, nothing else."
)


@dataclass(frozen=True)
class GeminiExtractionResult:
    signals: list[Signal]
    error: str = ""


@dataclass(frozen=True)
class GeminiWarningResult:
    text: str = ""
    error: str = ""


async def _chat_completion(
    settings: Settings, system_prompt: str, user_content: str, *, json_mode: bool
) -> tuple[str, str]:
    """POST one chat-completions call; returns (content, error). Shared by
    extract_signals (JSON output) and generate_warning (plain sentence) so
    both degrade the same way.
    """
    api_key = settings.value("llm_api_key")
    if not api_key:
        return "", "llm api key not configured"

    payload: dict = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{settings.llm_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        return "", f"llm request failed: {type(exc).__name__}"

    if response.status_code != 200:
        return "", f"llm returned {response.status_code}"

    try:
        body = response.json()
        return body["choices"][0]["message"]["content"], ""
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        return "", f"unexpected llm response: {type(exc).__name__}"


async def extract_signals(settings: Settings, text: str) -> GeminiExtractionResult:
    """Classify one transcript segment into risk categories via the
    configured LLM (Gemini, through its OpenAI-compatible chat completions
    endpoint — the same one gradbot's conversational agent already uses).

    Degrades to an empty, error-flagged result on any failure (missing key,
    network error, unexpected response shape) rather than raising — a
    flaky LLM call must never crash the live call relay.
    """
    if not text or not text.strip():
        return GeminiExtractionResult([])

    raw_content, error = await _chat_completion(settings, _SYSTEM_PROMPT, text, json_mode=True)
    if error:
        return GeminiExtractionResult([], error=error)

    try:
        categories = json.loads(raw_content)["categories"]
        if not isinstance(categories, list):
            raise TypeError("expected categories to be a list")
    except (KeyError, ValueError, TypeError) as exc:
        return GeminiExtractionResult([], error=f"unexpected llm response: {type(exc).__name__}")

    signals = [
        Signal(category, POINTS_BY_CATEGORY[category])
        for category in categories
        if category in POINTS_BY_CATEGORY
    ]
    return GeminiExtractionResult(signals)


async def generate_warning(
    settings: Settings, tier: str, categories: list[str]
) -> GeminiWarningResult:
    """Generate one short, guardrailed sentence for the agent to speak when
    the backend (not this function) has already decided the call just
    escalated to a new risk tier. This function only decides *what to say*;
    *whether* to say anything at all is risk.py's deterministic threshold,
    decided by the caller of this function — never by the LLM itself.
    """
    user_content = json.dumps({"risk_level": tier, "detected_signals": categories})
    raw_content, error = await _chat_completion(
        settings, _WARNING_SYSTEM_PROMPT, user_content, json_mode=False
    )
    if error:
        return GeminiWarningResult(error=error)
    text = raw_content.strip().strip('"')
    if not text:
        return GeminiWarningResult(error="empty warning text")
    return GeminiWarningResult(text=text)
