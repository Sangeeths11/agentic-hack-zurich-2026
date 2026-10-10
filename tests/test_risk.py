import pytest

from familyshield.risk import CallRiskScore, detect_signals, tier_for_score


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (0, "none"),
        (49, "none"),
        (50, "amber"),
        (74, "amber"),
        (75, "red"),
        (89, "red"),
        (90, "critical"),
        (150, "critical"),
    ],
)
def test_tier_thresholds_match_the_deck(score, tier):
    assert tier_for_score(score) == tier


def test_benign_text_has_no_signals():
    assert detect_signals("Hi, how are you doing today?") == []
    assert detect_signals("") == []


def test_single_signal_detected_with_its_points():
    signals = detect_signals("Please don't tell your daughter about this.")
    assert [s.category for s in signals] == ["secrecy"]
    assert signals[0].points == 10


def test_the_use_case_one_walkthrough_reaches_at_least_amber():
    """Reproduces the deck's worked example (slides 10-11): grandson-in-jail
    scam. Starting from an unknown number (+10), each line of the call adds
    its signal. The deck's own total is 94 (Critical) but that example also
    counts gateway attestation (+10) and a voiceprint mismatch (+25), which
    this backend doesn't model (no SS7/SIP attestation or voice enrollment
    available yet) — so our total is lower, but the same six text signals
    all fire and the call still clears Amber.
    """
    call = CallRiskScore(score=10, categories_hit={"unknown_number"})  # unknown caller

    call.apply("Grandma, it's me, Marco.")
    call.apply("I've been arrested. I'm in a cell.")
    call.apply("I need it within the hour.")
    call.apply("Please don't tell Mum and Dad.")
    call.apply("I need five thousand francs, in cash.")
    call.apply("A courier will come to your door.")

    assert call.score == 55
    assert call.tier == "amber"
    assert call.categories_hit == {
        "unknown_number",
        "family_claim",
        "distress",
        "urgency",
        "secrecy",
        "financial_request",
        "extraction",
    }


def test_known_contact_without_signals_stays_at_none():
    call = CallRiskScore()  # known contact: no unknown-number seed
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
    call.apply("We talked about the invoice payment last week.")
    assert call.tier in {"none", "amber"}
    assert call.score < 50
