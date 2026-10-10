import asyncio
import time

import pytest
from support import DemoFactory, assessment, microphone_audio, next_event, simulation_settings

from scam_the_scamers.agent import TranscriptBuffer
from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.providers import ProviderError, TranscriptEvent
from scam_the_scamers.risk import CRITERIA, WARNINGS, Assessment


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


@pytest.mark.parametrize(
    "warning,threshold,spoken", [(False, 50, False), (True, 60, True), (True, 61, False)]
)
async def test_partial_scenario_computed_total_drives_ui_email_and_warning(
    warning, threshold, spoken
):
    result = Assessment.model_validate(
        {
            "criteria": dict(zip(CRITERIA, [5, 10, 20, 0, 25, 0], strict=True)),
            "warning_text": WARNINGS[0] if warning else None,
        }
    )
    settings = simulation_settings(
        warning_threshold=threshold,
        resend_api_key="fake",
        alert_from_email="sender@example.com",
        fallback_email="caregiver@example.com",
    )
    factory = DemoFactory(settings, result)
    session = await factory.open(CallerLookup("not_provided"), "computed")
    try:
        await session.send_audio(microphone_audio())
        event = await next_event(session, "assessment")
        assert event.payload["overall"] == 60
        assert event.payload["criteria"] == result.criteria.model_dump()
        await asyncio.wait_for(session.alerts.pending.join(), 1)
        assert session.alerts.highest_accepted == 50
        assert len(factory.email.calls) == 1
        if spoken:
            assert (await next_event(session, "warning")).payload["text"] == WARNINGS[0]
        else:
            await asyncio.wait_for(session.warning_queue.join(), 1)
            assert not factory.speech.calls
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


@pytest.mark.parametrize("warning", [False, True])
async def test_gemini_scam_flag_bypasses_score_threshold_without_inventing_speech(warning):
    factory = DemoFactory(
        simulation_settings(
            warning_threshold=90,
            resend_api_key="fake",
            alert_from_email="sender@example.com",
            fallback_email="caregiver@example.com",
        ),
        assessment(10, warning, is_scam=True),
    )
    session = await factory.open(CallerLookup("not_provided"), "scam-override")
    try:
        await session.send_audio(microphone_audio())
        event = await next_event(session, "assessment")
        assert event.payload["overall"] == 10
        assert event.payload["is_scam"] is True and event.payload["alert_level"] == 75
        await asyncio.wait_for(session.alerts.pending.join(), 1)
        assert session.alerts.highest_accepted == 75
        await asyncio.wait_for(session.warning_queue.join(), 3)
        emitted = []
        while not session.events.empty():
            emitted.append(session.events.get_nowait())
        notices = [e.payload for e in emitted if e.payload.get("event") == "guardian_alert"]
        assert len(notices) == 1 and notices[0]["level"] == 75
        assert notices[0]["status"] == "accepted"
        spoken = [e for e in emitted if e.payload.get("event") == "warning"]
        assert len(spoken) == int(warning)
        if spoken:
            assert spoken[0].payload["text"] == WARNINGS[0] and spoken[0].audio
        assert factory.speech.calls == ([WARNINGS[0]] if warning else [])
    finally:
        await session.close()


@pytest.mark.parametrize("configured", [False, True])
async def test_failed_or_unconfigured_email_never_emits_guardian_notification(configured):
    settings = simulation_settings(
        resend_api_key="fake" if configured else "",
        alert_from_email="sender@example.com",
        fallback_email="caregiver@example.com",
    )
    factory = DemoFactory(settings, assessment(90))
    factory.email.failures = [ProviderError("resend", "credentials_rejected")]
    session = await factory.open(CallerLookup("not_provided"), "guardian-unavailable")
    try:
        await session.send_audio(microphone_audio())
        await asyncio.wait_for(session.finish(), 2)
        emitted = []
        while (event := await session.receive()) is not None:
            emitted.append(event.payload)
        assert not any(e.get("event") == "guardian_alert" for e in emitted)
        assert any(e.get("event") == "assessment" for e in emitted)
        assert session.alerts.highest_accepted == 0
        assert len(factory.email.calls) == int(configured)
    finally:
        await session.close()


async def test_transcript_snapshots_are_bounded_and_context_matches_assessment_revision():
    factory = DemoFactory(auto_text=False)
    factory.classifier.delay = 0.05
    session = await factory.open(CallerLookup("not_provided"), "transcript")
    session.transcript = TranscriptBuffer(1000)
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Opening context."))
        first = await next_event(session, "transcript")
        assert first.payload == {
            "event": "transcript",
            "revision": 1,
            "text": "Opening context.",
            "context_truncated": False,
            "settings_revision": 0,
            "turns": [{"id": 1, "text": "Opening context.", "role": "unknown", "start_s": None}],
        }
        started = await next_event(session, "tool")
        assert (started.payload["tool"], started.payload["status"]) == ("gemini", "assessing")
        await factory.stts[0].events.put(TranscriptEvent("text", "Later speech. " * 100))
        second = await next_event(session, "transcript")
        assert second.payload["revision"] == 2
        assert second.payload["context_truncated"] is True
        assert len(second.payload["text"]) <= 1000
        assert second.payload["text"].startswith("Opening context.")
        first_score = await next_event(session, "assessment")
        assert first_score.payload["revision"] == 1
        assert first_score.payload["context_truncated"] is False
        second_score = await next_event(session, "assessment")
        assert second_score.payload["revision"] == 2
        assert second_score.payload["context_truncated"] is True
        assert factory.classifier.calls[-1][0] == second.payload["text"]
    finally:
        await session.close()


async def test_tool_events_follow_actual_pipeline_work_and_stt_completion():
    factory = DemoFactory()
    session = await factory.open(CallerLookup("not_provided"), "tools")
    try:
        await session.send_audio(microphone_audio())
        await asyncio.wait_for(session.finish(), 2)
        tools = []
        while (event := await session.receive()) is not None:
            if event.payload.get("event") == "tool":
                tools.append((event.payload["tool"], event.payload["status"]))
        for activity in [
            ("stt", "streaming"),
            ("stt", "complete"),
            ("gemini", "assessing"),
            ("gemini", "complete"),
            ("scoring", "calculating"),
            ("scoring", "complete"),
        ]:
            assert activity in tools
        assert tools.index(("gemini", "complete")) < tools.index(("scoring", "calculating"))
        assert tools.index(("scoring", "calculating")) < tools.index(("scoring", "complete"))
        assert not any(tool in {"tts", "resend"} for tool, _ in tools)
    finally:
        await session.close()


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


async def test_whatsapp_and_email_alerts_complete_independently_without_tts_or_recipient_context():
    settings = simulation_settings(
        callmebot_api_key="fake",
        guardian_phone="+41791234567",
        resend_api_key="fake",
        alert_from_email="sender@example.com",
        fallback_email="caregiver@example.com",
    )
    factory = DemoFactory(settings, assessment(75))
    factory.whatsapp.delay = 0.05
    session = await factory.open(CallerLookup("not_provided"), "both-channels")
    try:
        await session.send_audio(microphone_audio())
        await asyncio.wait_for(session.finish(), 2)
        notices = []
        while (event := await session.receive()) is not None:
            if event.payload.get("event") == "guardian_alert":
                notices.append(event.payload)
        assert len(factory.email.calls) == len(factory.whatsapp.calls) == 1
        assert {event["channel"] for event in notices} == {"email", "whatsapp"}
        assert session.whatsapp_alerts.highest_accepted == 75
        assert factory.whatsapp.calls[0][0] == "+41791234567"
        assert not factory.speech.calls
        assert "+41791234567" not in str(factory.classifier.calls)
    finally:
        await session.close()


async def test_whatsapp_failure_keeps_screening_email_and_final_results_available():
    settings = simulation_settings(
        callmebot_api_key="fake",
        guardian_phone="+41791234567",
        resend_api_key="fake",
        alert_from_email="sender@example.com",
        fallback_email="caregiver@example.com",
    )
    factory = DemoFactory(settings, assessment(90))
    factory.whatsapp.failures = [ProviderError("callmebot", "credentials_rejected")]
    session = await factory.open(CallerLookup("not_provided"), "failure")
    try:
        await session.send_audio(microphone_audio())
        await asyncio.wait_for(session.finish(), 2)
        emitted = []
        while (event := await session.receive()) is not None:
            emitted.append(event.payload)
        assert any(event.get("event") == "assessment" for event in emitted)
        notices = [event for event in emitted if event.get("event") == "guardian_alert"]
        assert len(notices) == 1 and notices[0]["channel"] == "email"
        assert session.whatsapp_alerts.highest_accepted == 0
        assert len(factory.whatsapp.calls) == 1
    finally:
        await session.close()


async def test_whatsapp_recipient_snapshot_and_session_isolation():
    settings = simulation_settings(
        callmebot_api_key="fake",
        guardian_phone="+41791234567",
        callmebot_recipient_keys='{"+41781234567":"fake-other"}',
    )
    factory = DemoFactory(settings, assessment(50))
    first = await factory.open(CallerLookup("not_provided"), "first", guardian_phone="+41781234567")
    second = await factory.open(CallerLookup("not_provided"), "second", guardian_phone="")
    try:
        await first.send_audio(microphone_audio())
        await second.send_audio(microphone_audio())
        await asyncio.wait_for(asyncio.gather(first.finish(), second.finish()), 2)
        assert [phone for phone, _ in factory.whatsapp.calls] == ["+41781234567"]
        assert first.whatsapp_alerts.highest_accepted == 50
        assert second.whatsapp_alerts.highest_accepted == 0
        assert settings.value("guardian_phone") == "+41791234567"
    finally:
        await first.close()
        await second.close()


async def test_pause_cancels_pending_whatsapp_and_closes_microphone_provider():
    settings = simulation_settings(callmebot_api_key="fake", guardian_phone="+41791234567")
    factory = DemoFactory(settings, assessment(90))
    factory.whatsapp.delay = 30
    session = await factory.open(CallerLookup("not_provided"), "cancel-alert")
    await session.send_audio(microphone_audio())
    await next_event(session, "assessment")
    await asyncio.wait_for(factory.whatsapp.started.wait(), 1)
    await asyncio.wait_for(session.close(), 0.5)
    assert factory.stts[0].closed and all(task.done() for task in session.tasks)
    assert session.whatsapp_alerts.highest_accepted == 0
