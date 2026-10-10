"""Cumulative, weighted risk score over a call's caller-side transcript.

Mirrors the pitch deck's scoring model (slides 10-11): named signals each add
points once per call (never stacking on repetition), and the running total
maps to Amber/Red/Critical thresholds. This is a backend rule, not an LLM
decision — per the brief, model output alone never authorises an action.

Not modelled (not available in the current single-leg bridge or without
infrastructure we don't have): voiceprint matching, STIR/SHAKEN gateway
attestation, and the protected person's own "compliance marker" (there is no
separate protected-person leg yet — the agent speaks directly with whoever
called in).
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
            r"\b(it'?s me\b|grandma\b|grandpa\b|granddad\b"
            r"|(?:i'?m|this is) your (?:son|daughter|grandson|granddaughter))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "distress",
        8,
        re.compile(
            r"\b(arrested|in (?:jail|a cell)|accident|hospital|emergency"
            r"|please help me|i'?m in trouble)\b",
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

# Highest first — the first threshold the score clears wins.
_THRESHOLDS: list[tuple[int, RiskTier]] = [(90, "critical"), (75, "red"), (50, "amber")]

UNKNOWN_NUMBER_POINTS = 10


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
    """Mutable, cumulative score for one call. One instance per call_sid,
    owned by the bridge for the lifetime of that call — not shared or
    thread-safe. Each signal category only ever contributes once.
    """

    score: int = 0
    categories_hit: set[str] = field(default_factory=set)

    def apply(self, text: str) -> list[Signal]:
        """Score a new transcript segment; returns only the signals that are
        new for this call (already-hit categories don't score again).
        """
        new_signals = [s for s in detect_signals(text) if s.category not in self.categories_hit]
        for signal in new_signals:
            self.score += signal.points
            self.categories_hit.add(signal.category)
        return new_signals

    @property
    def tier(self) -> RiskTier:
        return tier_for_score(self.score)
