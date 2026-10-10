"""Bounded full-duplex PCM transport between a browser and the voice provider."""

import asyncio
import audioop
import json
import logging
import time
import uuid
from contextlib import suppress

from anyio import CancelScope
from fastapi import WebSocket, WebSocketDisconnect

from .agent import AgentFactory, AgentSession
from .config import Settings

GRADBOT_INPUT_RATE = 24_000
GRADBOT_OUTPUT_RATE = 48_000

log = logging.getLogger(__name__)


class ProtocolError(ValueError):
    pass


async def read_start(ws: WebSocket) -> None:
    message = await asyncio.wait_for(ws.receive(), 5)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text", "")
    if len(raw) > 1024:
        raise ProtocolError("Start message too large")
    if json.loads(raw) != {
        "event": "start",
        "encoding": "pcm_s16le",
        "sample_rate": GRADBOT_INPUT_RATE,
        "channels": 1,
    }:
        raise ProtocolError("Expected mono 24kHz PCM16 start")


async def relay(ws: WebSocket, settings: Settings, factory: AgentFactory, session_id: str) -> None:
    started = time.monotonic()
    session: AgentSession | None = None
    tasks: list[asyncio.Task] = []
    first_audio = asyncio.Event()
    incoming: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)

    async def receive_microphone() -> None:
        while True:
            message = await asyncio.wait_for(ws.receive(), settings.media_idle_timeout_seconds)
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") == '{"event":"stop"}':
                return
            if "text" in message:
                raw = message["text"]
                if len(raw) <= 1024 and json.loads(raw) == {"event": "stop"}:
                    return
                raise ProtocolError("Unexpected control message")
            audio = message.get("bytes", b"")
            if not 0 < len(audio) <= 9600 or len(audio) % 2:
                raise ProtocolError("Expected at most 200ms of PCM16")
            try:
                incoming.put_nowait(audio)
            except asyncio.QueueFull as exc:
                raise ProtocolError("Microphone queue overflow") from exc

    async def forward_audio() -> None:
        assert session is not None
        while True:
            audio = await incoming.get()
            await asyncio.wait_for(session.send_audio(audioop.lin2ulaw(audio, 2)), 5)

    async def send_agent() -> None:
        assert session is not None
        turn_ended_at: float | None = None
        while True:
            wait_seconds = settings.provider_idle_timeout_seconds
            if turn_ended_at is not None:
                remaining = settings.first_audio_timeout_seconds - (
                    time.monotonic() - turn_ended_at
                )
                if remaining <= 0:
                    raise TimeoutError("No response audio")
                wait_seconds = min(wait_seconds, remaining)
            event = await asyncio.wait_for(session.receive(), wait_seconds)
            if event is None:
                return
            if event.kind == "interrupt":
                turn_ended_at = None
                await asyncio.wait_for(ws.send_json({"event": "clear"}), 5)
            elif event.kind == "turn_end":
                if turn_ended_at is None:
                    turn_ended_at = time.monotonic()
                await asyncio.wait_for(ws.send_json({"event": "thinking"}), 5)
            elif event.kind == "audio" and event.audio:
                # 20ms PCM frames; the browser schedules them without waiting for a full reply.
                audio = audioop.ulaw2lin(event.audio, 2)
                for offset in range(0, len(audio), 1920):
                    await asyncio.wait_for(ws.send_bytes(audio[offset : offset + 1920]), 5)
                if turn_ended_at is not None:
                    log.info(
                        "listener_response_audio id=%s latency_ms=%d",
                        session_id,
                        (time.monotonic() - turn_ended_at) * 1000,
                    )
                    turn_ended_at = None
                if not first_audio.is_set():
                    first_audio.set()
                    log.info(
                        "listener_first_audio id=%s latency_ms=%d",
                        session_id,
                        (time.monotonic() - started) * 1000,
                    )

    async def watch_first_audio() -> None:
        await asyncio.wait_for(first_audio.wait(), settings.first_audio_timeout_seconds)
        await asyncio.Future()

    async def run_provider() -> None:
        nonlocal session
        session = await asyncio.wait_for(factory.open(), settings.session_start_timeout_seconds)
        await asyncio.wait_for(
            ws.send_json(
                {
                    "event": "ready",
                    "input_rate": GRADBOT_INPUT_RATE,
                    "output_rate": GRADBOT_OUTPUT_RATE,
                }
            ),
            5,
        )
        provider_tasks = [
            asyncio.create_task(coro()) for coro in (forward_audio, send_agent, watch_first_audio)
        ]
        try:
            done, _ = await asyncio.wait(provider_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in provider_tasks:
                task.cancel()
            await asyncio.gather(*provider_tasks, return_exceptions=True)

    try:
        async with asyncio.timeout(settings.max_session_seconds):
            tasks = [asyncio.create_task(receive_microphone()), asyncio.create_task(run_provider())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        # ASGI can cancel the handler during disconnect/shutdown. Keep cleanup alive
        # long enough to release native provider handles, including in that case.
        with CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if session is not None:
                with suppress(Exception):
                    await asyncio.wait_for(session.close(), 3)
            log.info(
                "listener_closed id=%s duration_s=%.2f", session_id, time.monotonic() - started
            )


class SessionRegistry:
    def __init__(self, limit: int):
        self.limit = limit
        self.sockets: set[WebSocket] = set()
        self.draining = False

    def acquire(self, ws: WebSocket) -> bool:
        if self.draining or len(self.sockets) >= self.limit:
            return False
        self.sockets.add(ws)
        return True

    async def shutdown(self) -> None:
        self.draining = True
        for ws in list(self.sockets):
            with suppress(Exception):
                await ws.close(code=1001)


async def handle_stream(
    ws: WebSocket, settings: Settings, factory: AgentFactory, registry: SessionRegistry
) -> None:
    if not registry.acquire(ws):
        await ws.close(code=1013)
        return
    session_id = uuid.uuid4().hex[:12]
    code, reason = 1000, "session-ended"
    try:
        await ws.accept()
        await read_start(ws)
        log.info("listener_started id=%s active=%d", session_id, len(registry.sockets))
        await relay(ws, settings, factory, session_id)
    except WebSocketDisconnect:
        pass
    except (ProtocolError, ValueError, KeyError, TypeError, AttributeError):
        code, reason = 1008, "invalid-audio"
        log.warning("listener_protocol_error id=%s", session_id)
    except TimeoutError:
        code, reason = 1011, "session-timeout"
        log.warning("listener_timeout id=%s", session_id)
    except Exception as exc:
        code, reason = 1011, "provider-unavailable"
        log.error("listener_provider_error id=%s type=%s", session_id, type(exc).__name__)
    finally:
        registry.sockets.discard(ws)
        with suppress(Exception):
            await ws.close(code=code, reason=reason)
