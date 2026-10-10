"""Cumulative, weighted risk score over a session's caller-side transcript.

Per-signal points are the pitch deck's own worked example (slides 10-11);
named signals each add points once per session (never stacking on
repetition). This is a backend rule, not an LLM decision — per the brief,
model output alone never authorises an action.

Not modelled (not available in this browser-listener architecture or
without infrastructure we don't have): the caller-ID signal from the deck's
worked example (there's no phone number here, just a microphone), voiceprint
matching, STIR/SHAKEN gateway attestation, and the protected person's own
"compliance marker" (there is no separate protected-person leg — the agent
hears whatever the laptop microphone picks up). Without those, the max score
from text signals alone is 45 — so unlike the deck, the Amber/Red/Critical
thresholds here (18/28/38) are calibrated to this module's own text-only
scale, not copied from the deck's 50/75/90 (which assumed those extra
signals contributing up to 45 more points).
"""

import re
from dataclasses import dataclass, field

RiskTier = str  # "none" | "amber" | "red" | "critical"

# (category, points, pattern) — order doesn't matter, every match is checked.
_SIGNALS: list[tuple[str, int, re.Pattern]] = [
    (
        "family_claim",
        5,
        re.compile(
            r"\b(it'?s me\b"
            r"|grandma\b|grandmother\b|grandpa\b|grandfather\b|granddad\b"
            r"|(?:i'?m|this is) your (?:son|daughter|grandson|granddaughter"
            r"|nephew|niece|cousin|mom|mum|mother|dad|father))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "distress",
        8,
        re.compile(
            r"\b(arrested|in (?:jail|a cell)|detained|kidnapped"
            r"|(?:car )?(?:accident|crash)|injured|bleeding|hospital|emergency"
            r"|please help me|i'?m (?:in trouble|scared|stuck)|can'?t breathe)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "urgency",
        7,
        re.compile(
            r"\b(right now|immediately|urgent(?:ly)?|as soon as possible"
            r"|\basap\b|hurry|before it'?s too late|act now|within the hour)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "secrecy",
        10,
        re.compile(
            r"\b(don'?t tell|keep (?:this|it) (?:a secret|private|quiet|between us)"
            r"|it'?s a secret|confidential|between (?:you and me|us))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "financial_request",
        8,
        re.compile(
            r"\b(transfer|wire|send (?:me|us|him|her)? ?money|gift cards?|bitcoin|crypto"
            r"|bank (?:details|account|transfer)|western union|iban|routing number|in cash)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "extraction",
        7,
        re.compile(
            r"\b(courier|comes? to your door|remote access|teamviewer|anydesk"
            r"|verification code|one[- ]time (?:code|password)|\botp\b"
            r"|read (?:me|us|him|her) (?:the|your) (?:code|pin)|share your screen)\b",
            re.IGNORECASE,
        ),
    ),
]

# Highest first — the first threshold the score clears wins. Calibrated to
# this module's own 0-45 text-only scale (see module docstring), not the
# deck's 0-100 scale: 2-3 strong signals should reach Amber/Red, and most or
# all six signals together should reach Critical.
_THRESHOLDS: list[tuple[int, RiskTier]] = [(38, "critical"), (28, "red"), (18, "amber")]

# category -> points, for any other detector (e.g. an LLM-based extractor)
# that wants to score against this same scale without duplicating it.
POINTS_BY_CATEGORY: dict[str, int] = {name: points for name, points, _ in _SIGNALS}


@dataclass(frozen=True)
class Signal:
    category: str
    points: int


def detect_signals(text: str) -> list[Signal]:
    """Every signal category that matches this one segment of text."""
    if not text or not text.strip():
        return []
    return [Signal(name, points) for name, points, pattern in _SIGNALS if pattern.search(text)]


def tier_for_score(score: int) -> RiskTier:
    for threshold, tier in _THRESHOLDS:
        if score >= threshold:
            return tier
    return "none"


@dataclass
class CallRiskScore:
    """Mutable, cumulative score for one session. One instance per
    session_id, owned by the bridge for the lifetime of that session — not
    shared or thread-safe. Each signal category only ever contributes once.
    """

    score: int = 0
    categories_hit: set[str] = field(default_factory=set)

    def apply(self, text: str) -> list[Signal]:
        """Score a new transcript segment via the regex detector; returns
        only the signals that are new for this call (already-hit categories
        don't score again).
        """
        return self.apply_signals(detect_signals(text))

    def apply_signals(self, signals: list[Signal]) -> list[Signal]:
        """Same accumulation, for signals detected elsewhere (e.g. an LLM
        extractor) instead of this module's regex detector.
        """
        new_signals = [s for s in signals if s.category not in self.categories_hit]
        for signal in new_signals:
            self.score += signal.points
            self.categories_hit.add(signal.category)
        return new_signals

    @property
    def tier(self) -> RiskTier:
        return tier_for_score(self.score)
