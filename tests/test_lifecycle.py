import asyncio
import audioop

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from support import (
    LOCAL_HEADERS,
    LOCAL_URL,
    DemoFactory,
    DemoSession,
    microphone_audio,
    simulate,
    simulation_settings,
    start_message,
)

from scam_the_scamers.agent import AgentEvent
from scam_the_scamers.main import create_app


def test_full_offline_session_and_interrupt(tmp_path):
    result = simulate(tmp_path / "test.wav")
    assert result["result"] == "passed"
    assert result["interruption"] == "passed"
    assert (tmp_path / "test.wav").stat().st_size > 19000


def drain_greeting(ws):
    for _ in range(5):
        assert len(ws.receive_bytes()) == 1920


def start(ws):
    ws.send_json(start_message())
    assert ws.receive_json()["event"] == "ready"
    drain_greeting(ws)


def test_parallel_sessions_are_isolated_and_capacity_recovers():
    factory = DemoFactory()
    app = create_app(simulation_settings(max_concurrent_sessions=2), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as first:
            start(first)
            with client.websocket_connect(
                "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
            ) as second:
                start(second)
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect(
                        "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
                    ):
                        pass
                first.send_bytes(microphone_audio())
                assert first.receive_json()["event"] == "clear"
                assert first.receive_json()["event"] == "thinking"
                drain_greeting(first)
                assert factory.sessions[0].received == [audioop.lin2ulaw(microphone_audio(), 2)]
                assert not factory.sessions[1].received
                for ws in (first, second):
                    ws.send_json({"event": "stop"})
                    assert ws.receive()["code"] == 1000
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as third:
            start(third)
    assert all(session.closed for session in factory.sessions)
    assert not app.state.registry.sockets


class BrokenFactory:
    async def open(self):
        raise RuntimeError("Sensitive upstream response must not appear in logs")


def test_provider_start_failure_releases_capacity(caplog):
    app = create_app(simulation_settings(), BrokenFactory())
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(start_message())
            assert ws.receive()["code"] == 1011
    assert not app.state.registry.sockets
    assert "Sensitive upstream" not in caplog.text


class SilentSession(DemoSession):
    async def receive(self):
        await asyncio.Future()


class ActivityWithoutAudioSession(DemoSession):
    async def receive(self):
        await asyncio.sleep(0.02)
        return AgentEvent("activity")


class SessionFactory:
    def __init__(self, session_type):
        self.session_type = session_type
        self.session = None

    async def open(self):
        self.session = self.session_type()
        return self.session


@pytest.mark.parametrize("session_type", [SilentSession, ActivityWithoutAudioSession])
def test_first_audio_watchdog_even_with_activity(session_type):
    factory = SessionFactory(session_type)
    app = create_app(simulation_settings(first_audio_timeout_seconds=1), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(start_message())
            assert ws.receive_json()["event"] == "ready"
            assert ws.receive()["code"] == 1011
    assert factory.session.closed
    assert not app.state.registry.sockets


@pytest.mark.parametrize("audio", [b"", b"odd", b"\0" * 9602], ids=["empty", "odd", "oversized"])
def test_malformed_audio_closes_provider(audio):
    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            start(ws)
            ws.send_bytes(audio)
            assert ws.receive()["code"] == 1008
    assert factory.sessions[0].closed
    assert not app.state.registry.sockets


class NoReplySession(DemoSession):
    async def send_audio(self, audio):
        await self.events.put(AgentEvent("turn_end"))

    async def receive(self):
        if not self.events.empty():
            return await self.events.get()
        await asyncio.sleep(0.02)
        return AgentEvent("activity")


def test_response_watchdog_after_successful_greeting():
    factory = SessionFactory(NoReplySession)
    app = create_app(simulation_settings(first_audio_timeout_seconds=1), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            start(ws)
            ws.send_bytes(microphone_audio())
            assert ws.receive_json()["event"] == "thinking"
            assert ws.receive()["code"] == 1011
    assert factory.session.closed
    assert not app.state.registry.sockets


def test_session_limit_closes_provider():
    settings = simulation_settings().model_copy(update={"max_session_seconds": 0.1})
    factory = DemoFactory()
    app = create_app(settings, factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            start(ws)
            assert ws.receive()["code"] == 1011
    assert factory.sessions[0].closed


def test_stopping_during_provider_start_cancels_open():
    class SlowFactory:
        cancelled = False

        async def open(self):
            try:
                await asyncio.sleep(30)
            finally:
                self.cancelled = True

    factory = SlowFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(start_message())
            ws.send_json({"event": "stop"})
            assert ws.receive()["code"] == 1000
    assert factory.cancelled
    assert not app.state.registry.sockets


def test_disconnect_closes_provider():
    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    with TestClient(app, base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            start(ws)
    assert factory.sessions[0].closed
    assert not app.state.registry.sockets
