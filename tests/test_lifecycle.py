import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from familyshield.agent import AgentEvent
from familyshield.main import create_app
from familyshield.security import stream_ticket
from familyshield.simulation import (
    CALL_SID,
    STREAM_SID,
    DemoFactory,
    DemoSession,
    signature,
    simulate,
    simulation_settings,
    start_message,
    tone,
)


def test_full_offline_call_and_interrupt(tmp_path):
    result = simulate(tmp_path / "test.wav")
    assert result["result"] == "passed"
    assert result["interruption"] == "passed"
    assert (tmp_path / "test.wav").stat().st_size > 3000


def drain_greeting(ws):
    for _ in range(5):
        assert ws.receive_json()["event"] == "media"


def test_two_parallel_calls_are_isolated_and_third_is_rejected():
    settings = simulation_settings(max_concurrent_calls=2)
    factory = DemoFactory()
    app = create_app(settings, factory)
    headers = signature(settings, "/twilio/stream", websocket=True)
    call2, stream2 = "CA" + "4" * 32, "MZ" + "5" * 32
    with TestClient(app) as client:
        with client.websocket_connect("/twilio/stream", headers=headers) as first:
            first.send_json(start_message(stream_ticket(settings, CALL_SID)))
            drain_greeting(first)
            with client.websocket_connect("/twilio/stream", headers=headers) as second:
                second.send_json(start_message(stream_ticket(settings, call2), call2, stream2))
                drain_greeting(second)
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("/twilio/stream", headers=headers):
                        pass
                first.send_json(
                    {
                        "event": "media",
                        "streamSid": STREAM_SID,
                        "media": {
                            "track": "inbound",
                            "payload": base64.b64encode(tone(8000)).decode(),
                        },
                    }
                )
                assert first.receive_json()["event"] == "clear"
                drain_greeting(first)
                assert len(factory.sessions[0].received) == 1
                assert not factory.sessions[1].received
                first.send_json({"event": "stop", "streamSid": STREAM_SID})
                assert first.receive()["type"] == "websocket.close"
                second.send_json({"event": "stop", "streamSid": stream2})
                assert second.receive()["type"] == "websocket.close"
        assert all(session.closed for session in factory.sessions)
        assert not app.state.registry.sockets
        assert not app.state.registry.calls


class BrokenFactory:
    async def open(self):
        raise RuntimeError("Sensitive upstream response must not appear in logs")


def test_provider_start_failure_releases_capacity(caplog):
    settings = simulation_settings(max_concurrent_calls=1)
    app = create_app(settings, BrokenFactory())
    with TestClient(app) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
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
def test_first_audio_watchdog_even_when_provider_sends_activity(session_type):
    settings = simulation_settings(first_audio_timeout_seconds=1)
    factory = SessionFactory(session_type)
    app = create_app(settings, factory)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            assert ws.receive()["code"] == 1011
        assert factory.session.closed
        assert not app.state.registry.sockets


def test_malformed_audio_ends_only_this_call_and_closes_provider():
    settings = simulation_settings()
    factory = DemoFactory()
    with TestClient(create_app(settings, factory)) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            drain_greeting(ws)
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": STREAM_SID,
                    "media": {"track": "inbound", "payload": "invalid###"},
                }
            )
            assert ws.receive()["code"] == 1008
        assert factory.sessions[0].closed


class NoReplySession(DemoSession):
    async def send_audio(self, audio):
        await self.events.put(AgentEvent("turn_end"))

    async def receive(self):
        if not self.events.empty():
            return await self.events.get()
        await asyncio.sleep(0.02)
        return AgentEvent("activity")


def test_response_watchdog_after_successful_greeting():
    settings = simulation_settings(first_audio_timeout_seconds=1)
    factory = SessionFactory(NoReplySession)
    app = create_app(settings, factory)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            drain_greeting(ws)
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": STREAM_SID,
                    "media": {"track": "inbound", "payload": base64.b64encode(tone(8000)).decode()},
                }
            )
            assert ws.receive()["code"] == 1011
        assert factory.session.closed
        assert not app.state.registry.sockets


def test_call_limit_closes_an_active_provider():
    # Speed up the clock-bound test; normal config enforces a 10s minimum.
    settings = simulation_settings().model_copy(update={"max_call_seconds": 0.1})
    factory = DemoFactory()
    app = create_app(settings, factory)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            drain_greeting(ws)
            assert ws.receive()["code"] == 1011
        assert factory.sessions[0].closed
        assert not app.state.registry.sockets


def test_ticket_works_on_a_different_replica():
    from xml.etree import ElementTree

    from familyshield.simulation import ACCOUNT_SID

    settings = simulation_settings()
    params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
    # HTTP instance has no state shared with the separate WS instance.
    with TestClient(create_app(settings, DemoFactory())) as first:
        reply = first.post(
            "/twilio/voice", data=params, headers=signature(settings, "/twilio/voice", params)
        )
        ticket = (
            ElementTree.fromstring(reply.text).find("./Connect/Stream/Parameter").attrib["value"]
        )
    with TestClient(create_app(settings, DemoFactory())) as second:
        with second.websocket_connect(
            "/twilio/stream",
            headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            ws.send_json(start_message(ticket))
            drain_greeting(ws)
