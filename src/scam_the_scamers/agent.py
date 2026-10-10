"""Per-session screening orchestration with injectable providers and bounded memory."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from .alerts import AlertManager, WhatsAppAlertManager
from .caller import CallerLookup
from .config import Settings
from .profiles import RuntimePreferences
from .providers import (
    CallMeBotWhatsApp,
    Classifier,
    Email,
    GeminiClassifier,
    GradiumSTT,
    GradiumTTS,
    ProviderError,
    ResendEmail,
    Speech,
    STTSession,
    WhatsApp,
)
from .risk import Assessment, personalized_warning


@dataclass(frozen=True)
class AgentEvent:
    payload: dict = field(default_factory=dict)
    audio: bytes = b""
    warning_generation: int | None = None


class AgentSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...
    async def receive(self) -> AgentEvent | None: ...
    async def finish(self) -> None: ...
    async def cancel_warning(self) -> None: ...
    async def update_preferences(self, preferences: RuntimePreferences) -> None: ...
    async def close(self) -> None: ...
    async def notify(self, payload: dict) -> None: ...


class AgentFactory(Protocol):
    async def open(
        self,
        caller: CallerLookup,
        session_id: str,
        *,
        guardian_phone: str | None = None,
        preferences: RuntimePreferences | None = None,
        preferences_provider: Callable[[], RuntimePreferences] | None = None,
    ) -> AgentSession: ...


class TranscriptBuffer:
    def __init__(self, limit: int):
        self.limit, self.text, self.truncated = limit, "", False
        self.turns: list[dict] = []
        self.next_id = 1
        self.new_turn = True

    def boundary(self) -> None:
        self.new_turn = True

    def append(self, text: str, start_s: float | None = None) -> bool:
        text = text.strip()
        if not text:
            return False
        joined = f"{self.text} {text}".strip()
        if len(joined) > self.limit:
            self.truncated = True
            prefix = self.limit // 3
            joined = joined[:prefix] + " [context clipped] " + joined[-(self.limit - prefix - 19) :]
        self.text = joined
        if self.new_turn or not self.turns:
            self.turns.append(
                {"id": self.next_id, "text": text, "role": "unknown", "start_s": start_s}
            )
            self.next_id += 1
            self.new_turn = False
        else:
            self.turns[-1]["text"] += " " + text
            self.turns[-1]["role"] = "unknown"
        # Bound the display and speaker-label context independently of audio. Keep
        # opening and latest evidence, never alternate speakers merely on a pause.
        if len(self.turns) > 200:
            self.turns = self.turns[:60] + self.turns[-140:]
            self.truncated = True
        while sum(len(turn["text"]) + 1 for turn in self.turns) > self.limit:
            self.truncated = True
            if len(self.turns) == 1:
                self.turns[0]["text"] = self.text
                break
            self.turns[0]["text"] = self.turns[0]["text"][: self.limit // 3]
            if len(self.turns) > 2:
                del self.turns[1]
            else:
                available = self.limit - len(self.turns[0]["text"]) - 2
                self.turns[-1]["text"] = self.turns[-1]["text"][-available:]
                break
        return True

    def snapshot(self) -> list[dict]:
        return [dict(turn) for turn in self.turns]


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
        whatsapp: WhatsApp,
        guardian_phone: str | None = None,
        preferences: RuntimePreferences | None = None,
    ):
        self.settings, self.stt, self.classifier, self.speech = settings, stt, classifier, speech
        self.caller = caller
        self.preferences = preferences or RuntimePreferences(
            warning_threshold=max(1, int(settings.warning_threshold)),
            whatsapp_threshold=settings.whatsapp_threshold,
            whatsapp_cooldown_seconds=settings.whatsapp_cooldown_seconds,
        )
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
        self.warning_queue: asyncio.Queue[tuple[Assessment, int]] = asyncio.Queue(maxsize=1)
        self.alerts = AlertManager(settings, email, session_id, self.activity, notify=self.notify)
        self.whatsapp_alerts = WhatsAppAlertManager(
            settings,
            whatsapp,
            settings.value("guardian_phone") if guardian_phone is None else guardian_phone,
            session_id,
            self.activity,
            notify=self.notify,
            preferences=self.preferences,
        )
        self.closed = False
        self.tasks = [
            asyncio.create_task(self._guard(coro))
            for coro in (
                self._transcribe(),
                self._classify(),
                self._warn(),
                self.alerts.run(),
                self.whatsapp_alerts.run(),
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

    async def notify(self, payload: dict) -> None:
        await self.events.put(AgentEvent(payload))

    async def publish_transcript(self) -> None:
        await self.notify(
            {
                "event": "transcript",
                "revision": self.revision,
                "text": self.transcript.text,
                "turns": self.transcript.snapshot(),
                "context_truncated": self.transcript.truncated,
                "settings_revision": self.preferences.revision,
            }
        )

    async def update_preferences(self, preferences: RuntimePreferences) -> None:
        if preferences.revision <= self.preferences.revision:
            await self.notify({"event": "settings_applied", "revision": self.preferences.revision})
            return
        self.preferences = preferences
        self.latest = None
        await self.whatsapp_alerts.update_preferences(preferences)
        for turn in self.transcript.turns:
            turn["role"] = "unknown"
        await self.cancel_warning()
        while not self.warning_queue.empty():
            self.warning_queue.get_nowait()
            self.warning_queue.task_done()
        await self.whatsapp_alerts.consider_caller(self.caller)
        await self.notify({"event": "settings_applied", "revision": preferences.revision})
        if self.transcript.text:
            self.revision += 1
            self.assessed.clear()
            await self.publish_transcript()
            self.changed.set()

    async def send_audio(self, audio: bytes) -> None:
        # Keep the clock moving with silence during warnings; never replay buffered echo.
        if time.monotonic() < self.suppressed_until:
            audio = bytes(len(audio))
        await self.stt.send_audio(audio)

    async def receive(self) -> AgentEvent | None:
        while True:
            event = await self.events.get()
            if isinstance(event, Exception):
                raise event
            if (
                event is not None
                and event.warning_generation is not None
                and event.warning_generation != self.warning_generation
            ):
                # A settings edit or cancellation can overtake queued warning audio.
                continue
            return event

    async def _transcribe(self) -> None:
        await self.activity(
            "stt", "streaming", "Gradium STT connected; streaming microphone audio."
        )
        await self.alerts.consider_caller(self.caller)
        await self.whatsapp_alerts.consider_caller(self.caller)
        while True:
            event = await asyncio.wait_for(
                self.stt.receive(), self.settings.provider_idle_timeout_seconds
            )
            if event is None:
                await self.activity("stt", "complete", "Gradium STT finished transcribing.")
                self.stt_done.set()
                return
            if event.kind == "boundary":
                self.transcript.boundary()
            if event.kind == "text" and self.transcript.append(event.text, event.start_s):
                self.revision += 1
                await self.publish_transcript()
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
            preferences = self.preferences
            segments = self.transcript.snapshot()
            context_truncated = self.transcript.truncated
            last_started = time.monotonic()
            await self.activity("gemini", "assessing", "Assessing accumulated call context.")
            try:
                async with asyncio.timeout(self.settings.request_timeout_seconds):
                    assessment = await self.classifier.classify(
                        text,
                        self.caller,
                        profile=preferences.profile,
                        warning_threshold=preferences.warning_threshold,
                        segments=segments,
                    )
            except (ProviderError, ValueError, TimeoutError):
                if preferences.revision != self.preferences.revision:
                    self.changed.set()
                    continue
                self.latest = None
                await self.events.put(
                    AgentEvent(
                        {
                            "event": "assessment_unavailable",
                            "revision": revision,
                            "settings_revision": preferences.revision,
                        }
                    )
                )
                await self.activity(
                    "gemini", "error", "Assessment unavailable. No score or warning fabricated."
                )
            else:
                if preferences.revision != self.preferences.revision:
                    self.changed.set()
                    continue
                # One classifier request at a time; revisions can never arrive out of order.
                self.latest = assessment
                original = {turn["id"]: turn["text"] for turn in segments}
                assignments = {
                    label.turn_id: label.role for label in assessment.speaker_assignments
                }
                relabeled = False
                for turn in self.transcript.turns:
                    if turn["id"] in assignments and original.get(turn["id"]) == turn["text"]:
                        turn["role"] = assignments[turn["id"]]
                        relabeled = True
                if relabeled:
                    await self.publish_transcript()
                await self.activity(
                    "gemini", "complete", "Six criteria and scam judgment received."
                )
                await self.activity("scoring", "calculating", "Adding the six validated criteria.")
                if preferences.revision != self.preferences.revision:
                    self.changed.set()
                    continue
                result = assessment.model_dump()
                await self.events.put(
                    AgentEvent(
                        {
                            "event": "assessment",
                            "revision": revision,
                            **result,
                            "context_truncated": context_truncated,
                            "settings_revision": preferences.revision,
                        }
                    )
                )
                await self.activity(
                    "scoring", "complete", "Additive total and alert level calculated locally."
                )
                if preferences.revision != self.preferences.revision:
                    self.changed.set()
                    continue
                await self.alerts.consider(assessment, self.caller)
                if preferences.revision != self.preferences.revision:
                    self.changed.set()
                    continue
                await self.whatsapp_alerts.consider(assessment, self.caller)
                if self.warning_queue.full():
                    self.warning_queue.get_nowait()
                    self.warning_queue.task_done()
                self.warning_queue.put_nowait((assessment, preferences.revision))
            self.last_revision = revision
            if revision == self.revision:
                self.assessed.set()

    async def _warn(self) -> None:
        while True:
            assessment, preferences_revision = await self.warning_queue.get()
            try:
                level = max(1, assessment.alert_level)
                if (
                    preferences_revision != self.preferences.revision
                    or not assessment.warning_eligible(self.preferences.warning_threshold)
                    or level <= self.warning_attempted
                ):
                    continue
                generation = self.warning_generation
                preferences = self.preferences
                await self.activity(
                    "tts", "synthesizing", "Gradium TTS is synthesizing an eligible caution."
                )
                if (
                    generation != self.warning_generation
                    or preferences_revision != self.preferences.revision
                ):
                    continue
                self.warning_attempted = level
                try:
                    async with asyncio.timeout(self.settings.request_timeout_seconds):
                        # warning_eligible guarantees a nonempty allowlisted phrase.
                        warning_text = personalized_warning(assessment, preferences.profile)
                        assert warning_text is not None
                        audio = await self.speech.synthesize(warning_text)
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
                    or not self.latest.warning_eligible(self.preferences.warning_threshold)
                ):
                    await self.activity("tts", "cancelled", "Outdated warning discarded.")
                    continue
                duration = len(audio) / 96000
                self.suppressed_until = time.monotonic() + duration + 1
                await self.events.put(
                    AgentEvent(
                        {
                            "event": "warning",
                            "text": warning_text,
                            "duration_seconds": duration,
                        },
                        audio,
                        warning_generation=generation,
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
        await self.whatsapp_alerts.finish()
        await self.whatsapp_alerts.pending.join()
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
        self.transcript.turns.clear()
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
        whatsapp: WhatsApp | None = None,
    ):
        self.settings = settings
        self.stt_open = stt_open or (lambda: GradiumSTT.open(settings))
        self.classifier = classifier or GeminiClassifier(settings)
        self.speech = speech or GradiumTTS(settings)
        self.email = email or ResendEmail(settings)
        self.whatsapp = whatsapp or CallMeBotWhatsApp(settings)

    async def open(
        self,
        caller: CallerLookup,
        session_id: str,
        *,
        guardian_phone: str | None = None,
        preferences: RuntimePreferences | None = None,
        preferences_provider: Callable[[], RuntimePreferences] | None = None,
    ) -> AgentSession:
        stt = await self.stt_open()
        if preferences_provider is not None:
            preferences = preferences_provider()
        return ScreeningSession(
            self.settings,
            stt,
            self.classifier,
            self.speech,
            self.email,
            caller,
            session_id,
            self.whatsapp,
            guardian_phone,
            preferences,
        )
