"""Offline Twilio contract exercise. Diagnostic tones, explicitly not speech or AI."""

import asyncio
import audioop
import base64
import math
import struct
import time
import wave
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from twilio.request_validator import RequestValidator

from .agent import AgentEvent
from .config import Settings
from .main import create_app

ACCOUNT_SID = "AC" + "1" * 32
CALL_SID = "CA" + "2" * 32
STREAM_SID = "MZ" + "3" * 32


def simulation_settings(**overrides) -> Settings:
    values: dict[str, Any] = dict(
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token="offline-simulation-only",
        twilio_phone_number="+15005550006",
        gradium_api_key="offline",
        llm_api_key="offline",
        public_http_url="https://simulation.example",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def tone(rate: int, frequency: float = 440, seconds: float = 0.1) -> bytes:
    pcm = b"".join(
        struct.pack("<h", int(5000 * math.sin(2 * math.pi * frequency * i / rate)))
        for i in range(round(rate * seconds))
    )
    return audioop.lin2ulaw(pcm, 2)


class DemoSession:
    def __init__(self):
        self.events: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=8)
        self.events.put_nowait(AgentEvent("audio", tone(48_000)))
        self.received: list[bytes] = []
        self.closed = False

    async def send_audio(self, audio: bytes) -> None:
        self.received.append(audio)
        await self.events.put(AgentEvent("interrupt"))
        await self.events.put(AgentEvent("turn_end"))
        await self.events.put(AgentEvent("audio", tone(48_000, 660)))

    async def receive(self) -> AgentEvent | None:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True


class DemoFactory:
    def __init__(self):
        self.sessions: list[DemoSession] = []

    async def open(self) -> DemoSession:
        session = DemoSession()
        self.sessions.append(session)
        return session


def signature(settings: Settings, path: str, params=None, *, websocket=False) -> dict[str, str]:
    url = settings.public_http_url + path
    if websocket:
        url = url.replace("https://", "wss://", 1)
    value = RequestValidator(settings.value("twilio_auth_token")).compute_signature(
        url, params or {}
    )
    return {"X-Twilio-Signature": value}


def start_message(ticket: str, call_sid: str = CALL_SID, stream_sid: str = STREAM_SID) -> dict:
    return {
        "event": "start",
        "streamSid": stream_sid,
        "start": {
            "accountSid": ACCOUNT_SID,
            "callSid": call_sid,
            "streamSid": stream_sid,
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "customParameters": {"ticket": ticket},
        },
    }


def simulate(output: Path) -> dict:
    from fastapi.testclient import TestClient

    settings = simulation_settings()
    factory = DemoFactory()
    app = create_app(settings, factory)
    recorded = bytearray()
    with TestClient(app) as client:
        params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
        response = client.post(
            "/twilio/voice", data=params, headers=signature(settings, "/twilio/voice", params)
        )
        response.raise_for_status()
        parameter = ElementTree.fromstring(response.text).find("./Connect/Stream/Parameter")
        assert parameter is not None
        with client.websocket_connect(
            "/twilio/stream", headers=signature(settings, "/twilio/stream", websocket=True)
        ) as ws:
            started = time.perf_counter()
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(start_message(parameter.attrib["value"]))
            while len(recorded) < 800:
                event = ws.receive_json()
                assert event["event"] == "media"
                recorded.extend(base64.b64decode(event["media"]["payload"]))
            greeting_ms = (time.perf_counter() - started) * 1000
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": STREAM_SID,
                    "media": {
                        "track": "inbound",
                        "payload": base64.b64encode(tone(8000)).decode(),
                    },
                }
            )
            interrupted = ws.receive_json()
            assert interrupted["event"] == "clear"
            while len(recorded) < 1600:
                event = ws.receive_json()
                recorded.extend(base64.b64decode(event["media"]["payload"]))
            ws.send_json({"event": "stop", "streamSid": STREAM_SID})
            assert ws.receive()["type"] == "websocket.close"
        assert factory.sessions[0].closed
        assert not app.state.registry.sockets
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(audioop.ulaw2lin(bytes(recorded), 2))
    return {
        "result": "passed",
        "mode": "offline diagnostic tones (no AI speech)",
        "greeting_transport_ms": round(greeting_ms, 2),
        "interruption": "passed",
        "cleanup": "passed",
        "wav": str(output),
    }
