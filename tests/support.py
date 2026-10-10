"""Offline browser protocol exercise with diagnostic tones, no speech or AI."""

import asyncio
import audioop
import math
import struct
import time
import wave
from pathlib import Path
from typing import Any

from scam_the_scamers.agent import AgentEvent
from scam_the_scamers.config import Settings
from scam_the_scamers.main import create_app

LOCAL_URL = "http://localhost:8082"
LOCAL_HEADERS = {"origin": LOCAL_URL}


def simulation_settings(**overrides) -> Settings:
    values: dict[str, Any] = dict(gradium_api_key="offline", llm_api_key="offline")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def tone(rate: int, frequency: float = 440, seconds: float = 0.1) -> bytes:
    pcm = b"".join(
        struct.pack("<h", int(5000 * math.sin(2 * math.pi * frequency * i / rate)))
        for i in range(round(rate * seconds))
    )
    return audioop.lin2ulaw(pcm, 2)


def microphone_audio(seconds: float = 0.02) -> bytes:
    return audioop.ulaw2lin(tone(24000, seconds=seconds), 2)


class DemoSession:
    def __init__(self):
        self.events: asyncio.Queue[AgentEvent | None] = asyncio.Queue(maxsize=8)
        self.events.put_nowait(AgentEvent("audio", tone(48000)))
        self.received: list[bytes] = []
        self.closed = False
        self.responded = False

    async def send_audio(self, audio: bytes) -> None:
        self.received.append(audio)
        if not self.responded and audioop.rms(audioop.ulaw2lin(audio, 2), 2) > 400:
            self.responded = True
            await self.events.put(AgentEvent("interrupt"))
            await self.events.put(AgentEvent("turn_end"))
            await self.events.put(AgentEvent("audio", tone(48000, 660)))

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


def start_message() -> dict:
    return {"event": "start", "encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1}


def simulate(output: Path) -> dict:
    from fastapi.testclient import TestClient

    factory = DemoFactory()
    app = create_app(simulation_settings(), factory)
    recorded = bytearray()
    with TestClient(app, base_url=LOCAL_URL) as client:
        assert client.get("/").status_code == 200
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            started = time.perf_counter()
            ws.send_json(start_message())
            assert ws.receive_json()["event"] == "ready"
            for _ in range(5):
                recorded.extend(ws.receive_bytes())
            greeting_ms = (time.perf_counter() - started) * 1000
            ws.send_bytes(microphone_audio())
            assert ws.receive_json()["event"] == "clear"
            assert ws.receive_json()["event"] == "thinking"
            for _ in range(5):
                recorded.extend(ws.receive_bytes())
            ws.send_json({"event": "stop"})
            assert ws.receive()["code"] == 1000
        assert factory.sessions[0].closed
        assert not app.state.registry.sockets
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(recorded)
    return {
        "result": "passed",
        "mode": "offline diagnostic tones (no AI speech)",
        "greeting_transport_ms": round(greeting_ms, 2),
        "interruption": "passed",
        "cleanup": "passed",
        "wav": str(output),
    }
