"""Per-session screening orchestration with injectable providers and bounded memory."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from .alerts import AlertManager
from .caller import CallerLookup
from .config import Settings
from .providers import (
    Classifier,
    Email,
    GeminiClassifier,
    GradiumSTT,
    GradiumTTS,
    ProviderError,
    ResendEmail,
    Speech,
    STTSession,
)
from .risk import Assessment, tier


@dataclass(frozen=True)
class AgentEvent:
    payload: dict = field(default_factory=dict)
    audio: bytes = b""


class AgentSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...
    async def receive(self) -> AgentEvent | None: ...
    async def finish(self) -> None: ...
    async def cancel_warning(self) -> None: ...
    async def close(self) -> None: ...


class AgentFactory(Protocol):
    async def open(self, caller: CallerLookup, session_id: str) -> AgentSession: ...


class TranscriptBuffer:
    def __init__(self, limit: int):
        self.limit, self.text, self.truncated = limit, "", False

    def append(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        joined = f"{self.text} {text}".strip()
        if len(joined) > self.limit:
            self.truncated = True
            prefix = self.limit // 3
            joined = joined[:prefix] + " [context clipped] " + joined[-(self.limit - prefix - 19) :]
        self.text = joined
        return True


class ScreeningSession:
    def __init__(
        self,
        settings: Settings,
        stt: STTSession,
        classifier: Classifier,
        speech: Speech,
        email: Email,
        caller: CallerLookup,
        session_id: str,
    ):
        self.settings, self.stt, self.classifier, self.speech = settings, stt, classifier, speech
        self.caller = caller
        self.events: asyncio.Queue[AgentEvent | Exception | None] = asyncio.Queue(maxsize=64)
        self.transcript = TranscriptBuffer(settings.transcript_max_chars)
        self.changed = asyncio.Event()
        self.stt_done = asyncio.Event()
        self.assessed = asyncio.Event()
        self.revision = 0
        self.last_revision = 0
        self.latest: Assessment | None = None
        self.warning_attempted = 0
        self.suppressed_until = 0.0
        self.warning_generation = 0
        self.warning_queue: asyncio.Queue[Assessment] = asyncio.Queue(maxsize=1)
        self.alerts = AlertManager(settings, email, session_id, self.activity)
        self.closed = False
        self.tasks = [
            asyncio.create_task(self._guard(coro))
            for coro in (
                self._transcribe(),
                self._classify(),
                self._warn(),
                self.alerts.run(),
            )
        ]

    async def _guard(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except Exception as exc:
            await self.events.put(exc)

    async def activity(self, tool: str, status: str, detail: str) -> None:
        await self.events.put(
            AgentEvent({"event": "tool", "tool": tool, "status": status, "detail": detail})
        )

    async def send_audio(self, audio: bytes) -> None:
        # Keep the clock moving with silence during warnings; never replay buffered echo.
        if time.monotonic() < self.suppressed_until:
            audio = bytes(len(audio))
        await self.stt.send_audio(audio)

    async def receive(self) -> AgentEvent | None:
        event = await self.events.get()
        if isinstance(event, Exception):
            raise event
        return event

    async def _transcribe(self) -> None:
        await self.activity(
            "stt", "streaming", "Gradium STT connected; streaming microphone audio."
        )
        while True:
            event = await asyncio.wait_for(
                self.stt.receive(), self.settings.provider_idle_timeout_seconds
            )
            if event is None:
                self.stt_done.set()
                return
            if event.kind == "text" and self.transcript.append(event.text):
                self.revision += 1
                self.assessed.clear()
                self.changed.set()
            # end_text closes a segment; it is not another text chunk. flushed is an
            # acknowledgement, not permission to discard the accumulated conversation.

    async def _classify(self) -> None:
        last_started = 0.0
        while True:
            await self.changed.wait()
            delay = max(
                0.0,
                self.settings.classification_interval_seconds - (time.monotonic() - last_started),
            )
            if delay:
                await asyncio.sleep(delay)
            self.changed.clear()
            revision, text = self.revision, self.transcript.text
            last_started = time.monotonic()
            await self.activity("gemini", "assessing", "Assessing accumulated call context.")
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    assessment = await self.classifier.classify(text, self.caller)
            except (ProviderError, ValueError, TimeoutError):
                self.latest = None
                await self.events.put(
                    AgentEvent({"event": "assessment_unavailable", "revision": revision})
                )
                await self.activity(
                    "gemini", "error", "Assessment unavailable. No score or warning fabricated."
                )
            else:
                # One classifier request at a time; revisions can never arrive out of order.
                self.latest = assessment
                await self.events.put(
                    AgentEvent(
                        {
                            "event": "assessment",
                            "revision": revision,
                            **assessment.model_dump(),
                            "context_truncated": self.transcript.truncated,
                        }
                    )
                )
                await self.activity(
                    "gemini", "complete", "Six criteria assessed; screening indicator updated."
                )
                await self.alerts.consider(assessment, self.caller)
                if self.warning_queue.full():
                    self.warning_queue.get_nowait()
                    self.warning_queue.task_done()
                self.warning_queue.put_nowait(assessment)
            self.last_revision = revision
            if revision == self.revision:
                self.assessed.set()

    async def _warn(self) -> None:
        while True:
            assessment = await self.warning_queue.get()
            try:
                level = max(1, tier(assessment.overall))
                if (
                    not assessment.warning_text
                    or assessment.overall < self.settings.warning_threshold
                    or level <= self.warning_attempted
                ):
                    continue
                self.warning_attempted = level
                generation = self.warning_generation
                await self.activity(
                    "tts", "synthesizing", "Gradium TTS is synthesizing an eligible caution."
                )
                try:
                    async with asyncio.timeout(self.settings.request_timeout_seconds):
                        audio = await self.speech.synthesize(assessment.warning_text)
                    if not audio or len(audio) % 2 or len(audio) > 12 * 96000:
                        raise ProviderError("tts", "invalid_audio")
                except (ProviderError, TimeoutError):
                    await self.activity(
                        "tts", "error", "Warning audio unavailable; scores remain visible."
                    )
                    continue
                if (
                    generation != self.warning_generation
                    or self.latest is None
                    or self.latest.overall < self.settings.warning_threshold
                ):
                    await self.activity("tts", "cancelled", "Outdated warning discarded.")
                    continue
                duration = len(audio) / 96000
                self.suppressed_until = time.monotonic() + duration + 1
                await self.events.put(
                    AgentEvent(
                        {
                            "event": "warning",
                            "text": assessment.warning_text,
                            "duration_seconds": duration,
                        },
                        audio,
                    )
                )
                await self.activity(
                    "tts",
                    "ready",
                    "Warning audio sent to browser; playback depends on browser audio.",
                )
                # Bound warning cadence and prevent a second warning overlapping playback.
                await asyncio.sleep(duration + 1)
            finally:
                self.warning_queue.task_done()

    async def cancel_warning(self) -> None:
        self.warning_generation += 1
        # A short tail protects against sound already emitted by laptop speakers.
        self.suppressed_until = time.monotonic() + 0.5
        await self.events.put(AgentEvent({"event": "clear"}))
        await self.activity("tts", "cancelled", "Warning cancelled by listener.")

    async def finish(self) -> None:
        # End-session drains STT; pause/disconnect cancels immediately via close().
        await self.stt.finish()
        await self.stt_done.wait()
        if self.revision > self.last_revision:
            await self.assessed.wait()
        await self.alerts.pending.join()
        await self.warning_queue.join()
        await self.events.put(None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.stt.close()
        self.transcript.text = ""
        self.latest = None
        while not self.events.empty():
            self.events.get_nowait()


class ScreeningFactory:
    def __init__(
        self,
        settings: Settings,
        *,
        stt_open: Callable[[], Awaitable[STTSession]] | None = None,
        classifier: Classifier | None = None,
        speech: Speech | None = None,
        email: Email | None = None,
    ):
        self.settings = settings
        self.stt_open = stt_open or (lambda: GradiumSTT.open(settings))
        self.classifier = classifier or GeminiClassifier(settings)
        self.speech = speech or GradiumTTS(settings)
        self.email = email or ResendEmail(settings)

    async def open(self, caller: CallerLookup, session_id: str) -> AgentSession:
        stt = await self.stt_open()
        return ScreeningSession(
            self.settings, stt, self.classifier, self.speech, self.email, caller, session_id
        )
