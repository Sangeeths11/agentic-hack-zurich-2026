import pytest

from scam_the_scamers.risk import (
    POINTS_BY_CATEGORY,
    CallRiskScore,
    Signal,
    detect_signals,
    tier_for_score,
)


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (0, "none"),
        (17, "none"),
        (18, "amber"),
        (27, "amber"),
        (28, "red"),
        (37, "red"),
        (38, "critical"),
        (100, "critical"),
    ],
)
def test_tier_thresholds(score, tier):
    assert tier_for_score(score) == tier


def test_benign_text_has_no_signals():
    assert detect_signals("Hi, how are you doing today?") == []
    assert detect_signals("") == []


def test_single_signal_detected_with_its_points():
    signals = detect_signals("Please don't tell your daughter about this.")
    assert [s.category for s in signals] == ["secrecy"]
    assert signals[0].points == 10


def test_the_use_case_one_walkthrough_reaches_critical():
    """Reproduces the deck's worked example (slides 10-11): grandson-in-jail
    scam. The deck's own total is 94 off a 0-100 scale (Critical), using
    signals (caller ID, gateway attestation, voiceprint) this module doesn't
    have. All six *text* signals here sum to 45 — the max this module can
    score from text alone — which still clears this module's own Critical
    threshold (38), calibrated to that smaller scale.
    """
    call = CallRiskScore()

    call.apply("Grandma, it's me, Marco.")
    call.apply("I've been arrested. I'm in a cell.")
    call.apply("I need it within the hour.")
    call.apply("Please don't tell Mum and Dad.")
    call.apply("I need five thousand francs, in cash.")
    call.apply("A courier will come to your door.")

    assert call.score == 45
    assert call.tier == "critical"
    assert call.categories_hit == {
        "family_claim",
        "distress",
        "urgency",
        "secrecy",
        "financial_request",
        "extraction",
    }


def test_benign_speech_without_signals_stays_at_none():
    call = CallRiskScore()
    call.apply("Hi Mum, just calling to check in, talk soon.")
    assert call.tier == "none"
    assert call.score == 0


def test_a_category_only_scores_once_per_call():
    call = CallRiskScore()
    first = call.apply("This is urgent, please hurry.")
    second = call.apply("I really need you to act now, it's urgent.")
    assert [s.category for s in first] == ["urgency"]
    assert second == []  # already scored — repetition doesn't stack
    assert call.score == 7


def test_money_mention_alone_does_not_reach_amber():
    call = CallRiskScore()
    call.apply("Could you wire the invoice payment sometime next week?")
    assert call.tier == "none"
    assert call.score == POINTS_BY_CATEGORY["financial_request"]
    assert call.score < 18


@pytest.mark.parametrize(
    "text",
    [
        "Grandmother, it's me, I need your help.",
        "Grandfather, can you hear me?",
        "This is your nephew calling about something urgent.",
        "I'm your niece, please don't hang up.",
        "This is your mum, I need to talk to you.",
        "I'm your father, listen carefully.",
    ],
)
def test_family_claim_recognizes_more_relation_words(text):
    signals = detect_signals(text)
    assert "family_claim" in [s.category for s in signals]


@pytest.mark.parametrize(
    "text",
    [
        "I've been detained at the border, please help.",
        "I was kidnapped, they won't let me go.",
        "I was in a car crash, I'm injured.",
        "I'm bleeding, I'm scared, please come quickly.",
        "I'm stuck here and I can't breathe properly.",
    ],
)
def test_distress_recognizes_more_phrasings(text):
    signals = detect_signals(text)
    assert "distress" in [s.category for s in signals]


def test_bare_mention_of_a_parent_without_a_claim_construction_does_not_fire():
    # "mom"/"dad" alone (not "I'm your mom" / "this is your dad") shouldn't
    # trigger family_claim — otherwise routine family chat would score.
    assert detect_signals("Hi Mum, just calling to check in, talk soon.") == []
    assert detect_signals("I spoke to Dad about dinner plans.") == []


def test_apply_signals_accumulates_signals_from_any_detector():
    # Same accumulation path as .apply(text), for signals detected elsewhere
    # (e.g. gemini_risk.py's LLM extractor instead of the regex detector).
    call = CallRiskScore()
    first = call.apply_signals([Signal("secrecy", 10), Signal("urgency", 7)])
    assert {s.category for s in first} == {"secrecy", "urgency"}
    assert call.score == 17

    second = call.apply_signals([Signal("secrecy", 10), Signal("distress", 8)])
    assert [s.category for s in second] == ["distress"]  # secrecy already counted
    assert call.score == 25
