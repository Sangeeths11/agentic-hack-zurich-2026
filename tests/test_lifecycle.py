import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support import (
    LOCAL_HEADERS,
    LOCAL_URL,
    DemoFactory,
    microphone_audio,
    simulation_settings,
    start_message,
)

from scam_the_scamers.main import create_app


def connect(client):
    return client.websocket_connect("ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS)


def start(ws):
    ws.send_json(start_message())
    assert ws.receive_json()["tool"] == "backend"
    assert ws.receive_json()["status"] == "checking"
    assert ws.receive_json()["event"] == "caller_lookup"
    assert ws.receive_json()["status"] == "not_provided"
    assert ws.receive_json()["status"] == "connecting"
    assert ws.receive_json()["event"] == "ready"
    assert ws.receive_json()["tool"] == "stt"


def receive_close(ws):
    while "code" not in (event := ws.receive()):
        assert event.get("bytes") is None
    return event


def test_low_risk_session_no_first_audio_timeout_and_cleanup():
    settings = simulation_settings()
    factory = DemoFactory(settings)
    app = create_app(settings, factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            ws.send_bytes(microphone_audio())
            while ws.receive_json()["event"] != "assessment":
                pass
            # Healthy STT heartbeats do not require warning/TTS audio.
            time.sleep(1.1)
            assert not factory.sessions[0].closed and not factory.speech.calls
            ws.send_json({"event": "stop"})
            while "code" not in (event := ws.receive()):
                assert event.get("bytes") is None
            assert event["code"] == 1000
    assert factory.sessions[0].closed and factory.stts[0].closed
    assert not app.state.registry.sockets


def test_parallel_sessions_isolated_and_capacity_recovers():
    settings = simulation_settings(max_concurrent_sessions=2)
    factory = DemoFactory(settings)
    app = create_app(settings, factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as first:
            start(first)
            with connect(client) as second:
                start(second)
                with pytest.raises(WebSocketDisconnect):
                    with connect(client):
                        pass
                first.send_bytes(microphone_audio())
                while first.receive_json()["event"] != "assessment":
                    pass
                assert not factory.stts[1].received
            assert factory.stts[1].closed
            with connect(client) as third:
                start(third)
    assert all(s.closed for s in factory.sessions)
    assert not app.state.registry.sockets


@pytest.mark.parametrize("audio", [b"", b"odd", b"\0" * 9602])
def test_malformed_audio_closes_provider(audio):
    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            ws.send_bytes(audio)
            assert ws.receive()["code"] == 1008
    assert factory.stts[0].closed and not app.state.registry.sockets


@pytest.mark.parametrize("setting", ["max_session_seconds", "media_idle_timeout_seconds"])
def test_timeouts_close_and_release_capacity(setting):
    settings = simulation_settings().model_copy(update={setting: 0.1})
    factory = DemoFactory(settings)
    app = create_app(settings, factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            expected = 1000 if setting == "max_session_seconds" else 1011
            assert receive_close(ws)["code"] == expected
    assert factory.stts[0].closed and not app.state.registry.sockets


def test_stop_during_provider_start_cancels_open():
    class SlowFactory:
        cancelled = False

        async def open(self, caller, session_id):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    factory = SlowFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            ws.send_json(start_message())
            time.sleep(0.02)
            ws.send_json({"event": "stop"})
            assert receive_close(ws)["code"] == 1000
    assert factory.cancelled and not app.state.registry.sockets


def test_provider_start_failure_releases_capacity():
    class BrokenFactory:
        async def open(self, caller, session_id):
            raise RuntimeError("Synthetic failure")

    app = create_app(simulation_settings(), BrokenFactory())
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            ws.send_json(start_message())
            assert receive_close(ws)["code"] == 1011
    assert not app.state.registry.sockets


def test_listed_number_is_reported_before_provider_failure():
    class BrokenFactory:
        async def open(self, caller, session_id):
            assert caller.status == "matched"
            raise RuntimeError("Synthetic failure")

    app = create_app(simulation_settings(), BrokenFactory())
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            ws.send_json(start_message("+1 (201) 375-9200"))
            assert ws.receive_json()["tool"] == "backend"
            assert ws.receive_json()["status"] == "checking"
            result = ws.receive_json()
            assert result["event"] == "caller_lookup"
            assert result["caller"]["status"] == "matched"
            assert result["caller"]["number"] == "+12013759200"
            activity = ws.receive_json()
            assert activity["tool"] == "lookup" and activity["status"] == "matched"
            assert "Warning" in activity["detail"]
            assert ws.receive_json()["status"] == "connecting"
            assert ws.receive()["code"] == 1011
    assert not app.state.registry.sockets


def test_disconnect_closes_provider():
    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
    assert factory.stts[0].closed and not app.state.registry.sockets


def test_inbound_audio_queue_is_bounded_during_slow_start():
    class SlowFactory:
        async def open(self, caller, session_id):
            await asyncio.sleep(20)

    app = create_app(simulation_settings(), SlowFactory())
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            ws.send_json(start_message())
            for _ in range(130):
                ws.send_bytes(microphone_audio())
            assert receive_close(ws)["code"] == 1008
    assert not app.state.registry.sockets


def test_stt_heartbeat_timeout_is_independent_of_tts():
    from support import FakeSTT

    class SilentSTT(FakeSTT):
        async def receive(self):
            await asyncio.sleep(30)

    settings = simulation_settings().model_copy(update={"provider_idle_timeout_seconds": 0.05})
    factory = DemoFactory(settings)
    stt = SilentSTT()

    async def open_stt():
        return stt

    factory.stt_open = open_stt
    app = create_app(settings, factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            assert ws.receive()["code"] == 1011
    assert stt.closed


def test_final_short_utterance_is_delivered_before_clean_close():
    from support import FakeSTT

    from scam_the_scamers.providers import TranscriptEvent

    class FinalSTT(FakeSTT):
        async def finish(self):
            await self.events.put(TranscriptEvent("text", "Last short utterance."))
            await super().finish()

    factory = DemoFactory(auto_text=False)

    async def open_stt():
        return FinalSTT(False)

    factory.stt_open = open_stt
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            ws.send_json({"event": "finish"})
            received = []
            while "code" not in (message := ws.receive()):
                received.append(message)
            assert message["code"] == 1000
            assert any('"event":"assessment"' in m.get("text", "") for m in received)
    assert factory.classifier.calls[-1][0] == "Last short utterance."


def test_disconnect_during_final_assessment_cancels_immediately():
    from support import FakeSTT

    from scam_the_scamers.providers import TranscriptEvent

    class FinalSTT(FakeSTT):
        async def finish(self):
            await self.events.put(TranscriptEvent("text", "Final fragment."))
            await super().finish()

    factory = DemoFactory(auto_text=False)
    stt = FinalSTT(False)
    factory.classifier.delay = 30

    async def open_stt():
        return stt

    factory.stt_open = open_stt
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with connect(client) as ws:
            start(ws)
            ws.send_json({"event": "finish"})
            while ws.receive_json().get("tool") != "gemini":
                pass
            started = time.monotonic()
        assert time.monotonic() - started < 1
        assert stt.closed
    assert not app.state.registry.sockets
