import asyncio

from fastapi.testclient import TestClient
from support import (
    LOCAL_HEADERS,
    LOCAL_URL,
    DemoFactory,
    assessment,
    microphone_audio,
    next_event,
    start_message,
)

from scam_the_scamers.agent import TranscriptBuffer
from scam_the_scamers.caller import CallerLookup
from scam_the_scamers.main import create_app
from scam_the_scamers.profiles import EmergencyContact, PersonalProfile, RuntimePreferences
from scam_the_scamers.providers import TranscriptEvent
from scam_the_scamers.risk import SpeakerAssignment


def preferences(revision=1, threshold=50):
    return RuntimePreferences(
        profile=PersonalProfile(
            listener_name="Margaret",
            personal_context="My daughter Emily lives in Bern. I have no son.",
            emergency_contacts=(
                EmergencyContact(name="Emily", relationship="daughter", phone="+41791234567"),
            ),
        ),
        warning_threshold=threshold,
        whatsapp_threshold=90,
        whatsapp_cooldown_seconds=60,
        revision=revision,
    )


async def test_personal_context_and_literal_speaker_turns_reach_classifier_and_voice():
    result = assessment(75, warning=True).model_copy(
        update={
            "conversation_summary": "The caller claims to be a son and asks for emergency cash.",
            "profile_mismatches": ["The listener says she has no son."],
            "speaker_assignments": [
                SpeakerAssignment(turn_id=1, role="caller"),
                SpeakerAssignment(turn_id=2, role="listener"),
                SpeakerAssignment(turn_id=999, role="caller"),
            ],
        }
    )
    factory = DemoFactory(result=result, auto_text=False)
    session = await factory.open(
        CallerLookup("not_provided"), "personal", preferences=preferences()
    )
    try:
        stt = factory.stts[0]
        await stt.events.put(TranscriptEvent("text", "Mum, it is your son.", start_s=0.5))
        await stt.events.put(TranscriptEvent("boundary"))
        await stt.events.put(TranscriptEvent("text", "I have no son. <img src=x>", start_s=2.0))
        await stt.events.put(TranscriptEvent("boundary"))
        await next_event(session, "assessment")
        warning = await next_event(session, "warning")
        assert "Margaret" in warning.payload["text"]
        assert "Emily" in warning.payload["text"]
        assert "family details" in warning.payload["text"]
        assert "<img" not in warning.payload["text"]
        assert "4179" not in warning.payload["text"]
        assert len(warning.payload["text"]) <= 180
        context = factory.classifier.contexts[-1]
        assert context["profile"].listener_name == "Margaret"
        assert context["warning_threshold"] == 50
        assert context["segments"][0]["id"] == 1
        assert session.transcript.turns == [
            {"id": 1, "text": "Mum, it is your son.", "role": "caller", "start_s": 0.5},
            {"id": 2, "text": "I have no son. <img src=x>", "role": "listener", "start_s": 2.0},
        ]
        assert not factory.whatsapp.calls
    finally:
        await session.close()
    assert not session.transcript.turns


async def test_live_preferences_discard_inflight_classification_and_reassess_existing_text():
    factory = DemoFactory(result=assessment(60, warning=True), auto_text=False)
    factory.classifier.delay = 0.12
    session = await factory.open(CallerLookup("not_provided"), "live-edit")
    try:
        await factory.stts[0].events.put(TranscriptEvent("text", "Please transfer some money."))
        await asyncio.wait_for(factory.classifier.started.wait(), 1)
        await session.update_preferences(preferences(threshold=90))
        result = await next_event(session, "assessment")
        assert result.payload["settings_revision"] == 1
        assert result.payload["revision"] == 2
        assert len(factory.classifier.calls) == 2
        assert factory.classifier.calls[0][0] == factory.classifier.calls[1][0]
        assert factory.classifier.contexts[-1]["profile"].listener_name == "Margaret"
        await session.warning_queue.join()
        assert not factory.speech.calls
    finally:
        await session.close()


def test_turns_stay_bounded_and_never_assume_alternating_speakers():
    buffer = TranscriptBuffer(1000)
    for index in range(500):
        buffer.append(f"Literal turn {index}. " * 3)
        buffer.boundary()
    assert buffer.truncated and len(buffer.text) <= 1000
    assert len(buffer.turns) <= 200
    assert sum(len(turn["text"]) for turn in buffer.turns) <= 1000
    assert all(turn["role"] == "unknown" for turn in buffer.turns)
    assert "turn 0" in buffer.turns[0]["text"]
    assert "turn 499" in buffer.turns[-1]["text"]


def test_settings_api_revision_applies_to_running_websocket_without_new_audio():
    class Parser:
        async def parse(self, text):
            return PersonalProfile(listener_name="Margaret", personal_context=text)

    factory = DemoFactory(result=assessment(20))
    app = create_app(factory.settings, factory, profile_parser=Parser())
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(start_message())
            while ws.receive_json()["event"] != "ready":
                pass
            ws.send_bytes(microphone_audio())
            while ws.receive_json()["event"] != "assessment":
                pass
            response = client.post(
                "/api/settings",
                headers=LOCAL_HEADERS,
                json={
                    "text": "I am Margaret. I have no son.",
                    "warning_threshold": 80,
                    "whatsapp_threshold": 85,
                    "whatsapp_cooldown_seconds": 120,
                },
            )
            assert response.status_code == 200
            revision = response.json()["revision"]
            ws.send_json({"event": "settings", "revision": revision})
            while (event := ws.receive_json())["event"] != "assessment":
                pass
            assert event["settings_revision"] == revision
            assert factory.sessions[0].preferences.whatsapp_threshold == 85
            assert factory.classifier.contexts[-1]["profile"].listener_name == "Margaret"
            ws.send_json({"event": "settings", "revision": revision + 20})
            while (event := ws.receive_json())["event"] != "settings_stale":
                pass
            assert event["revision"] == revision
            assert not factory.whatsapp.calls
            ws.send_json({"event": "stop"})
