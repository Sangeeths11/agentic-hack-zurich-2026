"""Concrete server-side transports. Never expose raw provider errors or payloads."""

import asyncio
import audioop
import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from websockets.asyncio.client import ClientConnection, connect

from .caller import CallerLookup
from .config import Settings
from .risk import SYSTEM_PROMPT, Assessment


class ProviderError(RuntimeError):
    def __init__(self, provider: str, code: str = "unavailable", *, retryable: bool = False):
        super().__init__(f"{provider}: {code}")
        self.provider, self.code, self.retryable = provider, code, retryable


@dataclass(frozen=True)
class TranscriptEvent:
    kind: str
    text: str = ""


class STTSession(Protocol):
    async def send_audio(self, audio: bytes) -> None: ...
    async def receive(self) -> TranscriptEvent | None: ...
    async def finish(self) -> None: ...
    async def close(self) -> None: ...


class Classifier(Protocol):
    async def classify(self, transcript: str, caller: CallerLookup) -> Assessment: ...


class Speech(Protocol):
    async def synthesize(self, text: str) -> bytes: ...


class Email(Protocol):
    async def send(self, body: dict, key: str) -> str: ...


async def post_bounded(
    client: httpx.AsyncClient, url: str, headers: dict, body: dict, limit: int, provider: str
) -> bytes:
    try:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 300:
                code = (
                    "credentials_rejected"
                    if response.status_code in {401, 403}
                    else "request_failed"
                )
                raise ProviderError(
                    provider,
                    code,
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > limit:
                    raise ProviderError(provider, "response_too_large")
                data.extend(chunk)
            return bytes(data)
    except httpx.HTTPError as exc:
        raise ProviderError(provider, "transport_error", retryable=True) from exc


class GeminiClassifier:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def classify(self, transcript: str, caller: CallerLookup) -> Assessment:
        s = self.settings
        payload = {
            "model": s.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "untrusted_transcript": transcript,
                            "number_lookup": caller.public(),
                            "spoken_warning_threshold": s.warning_threshold,
                        }
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "call_assessment",
                    "strict": True,
                    "schema": Assessment.model_json_schema(),
                },
            },
            "max_completion_tokens": 2048,
        }
        async with asyncio.timeout(s.request_timeout_seconds):
            async with httpx.AsyncClient(
                timeout=s.request_timeout_seconds, transport=self.transport, follow_redirects=False
            ) as client:
                raw = await post_bounded(
                    client,
                    s.llm_base_url + "/chat/completions",
                    {"Authorization": f"Bearer {s.gemini_key()}"},
                    payload,
                    32768,
                    "gemini",
                )
        try:
            choice = json.loads(raw)["choices"][0]
            message = choice["message"]
            if choice["finish_reason"] != "stop" or message.get("refusal"):
                raise ValueError("Blocked or incomplete")
            content = message["content"]
            if not isinstance(content, str) or len(content) > 8192:
                raise ValueError("Invalid content")
            return Assessment.model_validate_json(content)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("gemini", "invalid_or_blocked_response") from exc


class GradiumSTT:
    def __init__(self, ws: ClientConnection):
        self.ws = ws
        self.buffer = bytearray()
        self.silent_bytes = 0
        self.heard_audio = False
        self.flush_id = 0
        self.finished = False

    @classmethod
    async def open(cls, settings: Settings) -> "GradiumSTT":
        ws = await connect(
            settings.gradium_base_url.replace("https://", "wss://") + "/api/speech/asr",
            additional_headers={"x-api-key": settings.value("gradium_api_key")},
            max_size=32768,
            max_queue=16,
            open_timeout=settings.session_start_timeout_seconds,
            close_timeout=2,
            ping_interval=10,
            ping_timeout=10,
        )
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "setup",
                        "model_name": "default",
                        "input_format": "pcm",
                        "json_config": {"language": settings.agent_language, "delay_in_frames": 10},
                    }
                )
            )
            ready = json.loads(
                await asyncio.wait_for(ws.recv(), settings.session_start_timeout_seconds)
            )
            if ready.get("type") != "ready":
                raise ProviderError("stt", "setup_failed")
            return cls(ws)
        except BaseException:
            await ws.close()
            raise

    async def _audio(self, data: bytes) -> None:
        await self.ws.send(json.dumps({"type": "audio", "audio": base64.b64encode(data).decode()}))

    async def send_audio(self, audio: bytes) -> None:
        if self.finished:
            return
        self.buffer.extend(audio)
        while len(self.buffer) >= 3840:
            frame = bytes(self.buffer[:3840])
            del self.buffer[:3840]
            await self._audio(frame)
            if audioop.rms(frame, 2) > 150:
                self.heard_audio = True
                self.silent_bytes = 0
            else:
                self.silent_bytes += len(frame)
            if self.heard_audio and self.silent_bytes >= 30720:
                self.flush_id += 1
                await self.ws.send(json.dumps({"type": "flush", "flush_id": self.flush_id}))
                self.heard_audio = False

    async def receive(self) -> TranscriptEvent | None:
        message: Any = json.loads(await self.ws.recv())
        kind = message.get("type")
        if kind == "error":
            raise ProviderError("stt", "stream_failed")
        if kind == "end_of_stream":
            return None
        if kind == "text":
            text = message.get("text")
            if not isinstance(text, str) or len(text) > 4096:
                raise ProviderError("stt", "invalid_text")
            return TranscriptEvent("text", text)
        if kind in {"end_text", "flushed"}:
            return TranscriptEvent("boundary")
        return TranscriptEvent("heartbeat")

    async def finish(self) -> None:
        if self.finished:
            return
        self.finished = True
        if self.buffer:
            await self._audio(bytes(self.buffer).ljust(3840, b"\0"))
            self.buffer.clear()
        await self.ws.send(json.dumps({"type": "end_of_stream"}))

    async def close(self) -> None:
        self.buffer.clear()
        await self.ws.close()


class GradiumTTS:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def synthesize(self, text: str) -> bytes:
        if not text.strip() or len(text) > 180:
            raise ProviderError("tts", "invalid_warning")
        s = self.settings
        body = {"text": text, "output_format": "pcm", "only_audio": True}
        if s.gradium_voice_id:
            body["voice_id"] = s.gradium_voice_id
        else:
            body["voice_id"] = "YTpq7expH9539ERJ"
        async with asyncio.timeout(s.request_timeout_seconds):
            async with httpx.AsyncClient(
                timeout=s.request_timeout_seconds, transport=self.transport
            ) as client:
                audio = await post_bounded(
                    client,
                    s.gradium_base_url + "/api/post/speech/tts",
                    {"x-api-key": s.value("gradium_api_key")},
                    body,
                    12 * 48000 * 2,
                    "tts",
                )
        if not audio or len(audio) % 2:
            raise ProviderError("tts", "invalid_audio")
        return audio


class ResendEmail:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def send(self, body: dict, key: str) -> str:
        s = self.settings
        if s.email_status() != "configured":
            raise ProviderError("resend", s.email_status())
        async with asyncio.timeout(s.request_timeout_seconds):
            async with httpx.AsyncClient(
                timeout=s.request_timeout_seconds, transport=self.transport
            ) as client:
                raw = await post_bounded(
                    client,
                    "https://api.resend.com/emails",
                    {
                        "Authorization": f"Bearer {s.value('resend_api_key')}",
                        "Idempotency-Key": key,
                    },
                    body,
                    8192,
                    "resend",
                )
        try:
            identifier = json.loads(raw)["id"]
            if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
                raise ValueError("Missing acceptance ID")
            return identifier
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("resend", "invalid_response") from exc
