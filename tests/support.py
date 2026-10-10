"""Synthetic in-memory provider doubles. No cloud, real speech, email or persistence."""

import asyncio
import audioop
import math
import struct
from typing import Any

from scam_the_scamers.agent import ScreeningFactory
from scam_the_scamers.config import Settings
from scam_the_scamers.providers import TranscriptEvent
from scam_the_scamers.risk import CRITERIA, WARNINGS, Assessment, Scores

LOCAL_URL = "http://localhost:8082"
LOCAL_HEADERS = {"origin": LOCAL_URL}


def simulation_settings(**overrides) -> Settings:
    values: dict[str, Any] = dict(
        gradium_api_key="offline",
        llm_api_key="offline",
        classification_interval_seconds=0.1,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def assessment(score=20, warning=False) -> Assessment:
    return Assessment(
        criteria=Scores(**dict.fromkeys(CRITERIA, float(score))),
        warning_text=WARNINGS[0] if warning else None,
    )


def microphone_audio(seconds: float = 0.02, rate: int = 24000) -> bytes:
    return b"".join(
        struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(round(rate * seconds))
    )


class FakeSTT:
    def __init__(self, auto_text=True):
        self.events: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue(maxsize=64)
        self.received: list[bytes] = []
        self.closed = False
        self.finished = False
        self.responded = False
        self.auto_text = auto_text

    async def send_audio(self, audio: bytes) -> None:
        self.received.append(audio)
        # Keep test instrumentation bounded even for the 30-second browser scenario.
        self.received[:] = self.received[-2000:]
        if self.auto_text and not self.responded and audioop.rms(audio, 2) > 400:
            self.responded = True
            await self.events.put(TranscriptEvent("text", "Synthetic family emergency scenario."))
            await self.events.put(TranscriptEvent("boundary"))

    async def receive(self):
        try:
            return await asyncio.wait_for(self.events.get(), 0.08)
        except TimeoutError:
            return TranscriptEvent("heartbeat")

    async def finish(self):
        self.finished = True
        await self.events.put(None)

    async def close(self):
        self.closed = True


class FakeClassifier:
    def __init__(self, result=None):
        self.result = result or assessment()
        self.calls = []
        self.delay = 0
        self.error = None

    async def classify(self, transcript, caller):
        self.calls.append((transcript, caller))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


class FakeSpeech:
    def __init__(self):
        self.calls = []
        self.delay = 0
        self.error = None

    async def synthesize(self, text):
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return microphone_audio(seconds=0.6, rate=48000)


class FakeEmail:
    def __init__(self):
        self.calls = []
        self.failures = []

    async def send(self, body, key):
        self.calls.append((body, key))
        if self.failures:
            raise self.failures.pop(0)
        return "fake-acceptance-id"


class DemoFactory(ScreeningFactory):
    def __init__(self, settings=None, result=None, auto_text=True):
        self.stts = []
        self.sessions = []
        self.auto_text = auto_text
        super().__init__(
            settings or simulation_settings(),
            stt_open=self.open_stt,
            classifier=FakeClassifier(result),
            speech=FakeSpeech(),
            email=FakeEmail(),
        )

    async def open_stt(self):
        stt = FakeSTT(self.auto_text)
        self.stts.append(stt)
        return stt

    async def open(self, caller, session_id):
        session = await super().open(caller, session_id)
        self.sessions.append(session)
        return session


def start_message(number="") -> dict:
    return {
        "event": "start",
        "encoding": "pcm_s16le",
        "sample_rate": 24000,
        "channels": 1,
        "caller_number": number,
    }


async def next_event(session, kind, deadline=2):
    async with asyncio.timeout(deadline):
        while True:
            event = await session.receive()
            if event.payload.get("event") == kind:
                return event
