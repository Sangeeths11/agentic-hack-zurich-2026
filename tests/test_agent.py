import asyncio
import time

import pytest
from support import DemoFactory, assessment, microphone_audio, next_event, simulation_settings

from scam_the_scamers.agent import TranscriptBuffer
from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.providers import ProviderError, TranscriptEvent
from scam_the_scamers.risk import CRITERIA, WARNINGS


@pytest.mark.parametrize(
    "score,warning,threshold,expected",
    [
        (20, False, 50, False),
        (90, False, 50, False),
        (49, True, 50, False),
        (50, True, 50, True),
        (75, True, 90, False),
        (90, True, 90, True),
    ],
)
async def test_score_only_and_eligible_warning(score, warning, threshold, expected):
    settings = simulation_settings(warning_threshold=threshold)
    factory = DemoFactory(settings, assessment(score, warning))
    session = await factory.open(CallerLookup("not_provided"), "test")
    try:
        await session.send_audio(microphone_audio())
        event = await next_event(session, "assessment")
        assert event.payload["overall"] == score
        assert set(event.payload["criteria"]) == set(CRITERIA)
        if expected:
            event = await next_event(session, "warning")
            assert event.audio and event.payload["text"] == WARNINGS[0]
            assert factory.speech.calls == [WARNINGS[0]]
            await session.send_audio(microphone_audio())
            assert not any(factory.stts[0].received[-1])
            await session.cancel_warning()
            session.suppressed_until = time.monotonic() - 1
            await session.send_audio(microphone_audio())
            assert any(factory.stts[0].received[-1])
        else:
            await asyncio.sleep(0.03)
            assert not factory.speech.calls
        assert not factory.email.calls
    finally:
        await session.close()
    assert factory.stts[0].closed and not session.transcript.text
    assert all(task.done() for task in session.tasks)


async def test_accumulated_context_coalesced_and_single_flight_revisions():
    factory = DemoFactory(auto_text=False)
    factory.classifier.delay = 0.05
    session = await factory.open(CallerLookup("matched", "+12013759200", "fixture"), "test")
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "I was arrested."))
        first = await next_event(session, "assessment")
        for text in ["Do not tell Mum.", "Bring CHF 5000.", "A courier will collect it."]:
            await factory.stts[0].events.put(TranscriptEvent("text", text))
        second = await next_event(session, "assessment")
        assert second.payload["revision"] > first.payload["revision"]
        assert len(factory.classifier.calls) == 2
        assert "arrested" in factory.classifier.calls[-1][0]
        assert "Mum" in factory.classifier.calls[-1][0]
        assert "courier" in factory.classifier.calls[-1][0]
        assert factory.classifier.calls[-1][1].status == "matched"
    finally:
        await session.close()


def test_context_bounded_keeps_opening_and_recent_speech():
    buffer = TranscriptBuffer(1000)
    buffer.append("Earlier distress and secrecy.")
    for _ in range(300):
        buffer.append("More conversation.")
    buffer.append("Courier now.")
    assert len(buffer.text) <= 1000 and buffer.truncated
    assert buffer.text.startswith("Earlier distress and secrecy.")
    assert buffer.text.endswith("Courier now.")
    assert not buffer.append(" ")


@pytest.mark.parametrize(
    "error", [ProviderError("gemini"), ValueError("bad output"), TimeoutError()]
)
async def test_errors_report_unavailable_no_fake_scores_or_speech(error):
    factory = DemoFactory()
    factory.classifier.error = error
    session = await factory.open(CallerLookup("not_provided"), "test")
    try:
        await session.send_audio(microphone_audio())
        event = await next_event(session, "assessment_unavailable")
        assert "overall" not in event.payload
        assert session.latest is None
        assert not factory.speech.calls and not factory.email.calls
    finally:
        await session.close()


async def test_warning_repeats_and_jitter_do_not_repeat_tts():
    factory = DemoFactory(result=assessment(50, True), auto_text=False)
    session = await factory.open(CallerLookup("not_provided"), "test")
    try:
        for score in [50, 49, 50, 74, 75, 74, 75, 90, 89, 90]:
            factory.classifier.result = assessment(score, True)
            await factory.stts[0].events.put(TranscriptEvent("text", "Synthetic addition."))
            await next_event(session, "assessment")
            # Wait for warning worker to complete each tier, without real audio playback.
            await asyncio.wait_for(session.warning_queue.join(), 3)
        assert len(factory.speech.calls) == 3
    finally:
        await session.close()


async def test_finish_preserves_short_final_transcript_and_pause_cancels_inflight():
    factory = DemoFactory(auto_text=False)
    session = await factory.open(CallerLookup("not_provided"), "test")
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Cash now."))
        await asyncio.wait_for(session.finish(), 2)
        assert factory.classifier.calls[-1][0] == "Cash now."
    finally:
        await session.close()
    factory = DemoFactory()
    factory.classifier.delay = 10
    session = await factory.open(CallerLookup("not_provided"), "test")
    await session.send_audio(microphone_audio())
    await asyncio.sleep(0.01)
    await asyncio.wait_for(session.close(), 0.5)
    assert factory.stts[0].closed and all(t.done() for t in session.tasks)


async def test_tts_failure_leaves_scores_available_and_cancel_discards_pending_synthesis():
    factory = DemoFactory(result=assessment(90, True))
    factory.speech.delay = 0.1
    session = await factory.open(CallerLookup("not_provided"), "test")
    try:
        await session.send_audio(microphone_audio())
        await next_event(session, "assessment")
        await asyncio.sleep(0.01)
        await session.cancel_warning()
        await asyncio.wait_for(session.warning_queue.join(), 1)
        assert session.latest.overall == 90
        events = []
        while not session.events.empty():
            events.append(session.events.get_nowait())
        assert not any(getattr(e, "audio", None) for e in events)
    finally:
        await session.close()
