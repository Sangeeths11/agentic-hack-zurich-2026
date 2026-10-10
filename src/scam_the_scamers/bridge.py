"""Bounded full-duplex browser PCM transport and screening-session lifecycle."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress

from anyio import CancelScope
from fastapi import WebSocket, WebSocketDisconnect

from .agent import AgentFactory, AgentSession
from .caller import CallerLookup, lookup_caller
from .config import Settings

INPUT_RATE = 24000
OUTPUT_RATE = 48000
log = logging.getLogger(__name__)


class ProtocolError(ValueError):
    pass


async def read_start(ws: WebSocket, settings: Settings) -> CallerLookup:
    message = await asyncio.wait_for(ws.receive(), 5)
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text", "")
    if len(raw) > 1024:
        raise ProtocolError("Start message too large")
    start = json.loads(raw)
    if not isinstance(start, dict):
        raise ProtocolError("Expected start object")
    number = start.pop("caller_number", "")
    if not isinstance(number, str) or len(number) > 64:
        raise ProtocolError("Invalid caller number")
    if start != {
        "event": "start",
        "encoding": "pcm_s16le",
        "sample_rate": INPUT_RATE,
        "channels": 1,
    }:
        raise ProtocolError("Expected mono 24kHz PCM16 start")
    await asyncio.wait_for(
        ws.send_json(
            {
                "event": "tool",
                "tool": "backend",
                "status": "connected",
                "detail": "Local audio connection established.",
            }
        ),
        5,
    )
    await asyncio.wait_for(
        ws.send_json(
            {
                "event": "tool",
                "tool": "lookup",
                "status": "checking",
                "detail": "Checking the supplied number against the local dataset.",
            }
        ),
        5,
    )
    caller = await asyncio.to_thread(
        lookup_caller, number, settings.caller_default_region, settings.scam_db_path
    )
    # Publish local evidence before opening a cloud provider: a listed number
    # remains visible even when STT is slow or unavailable.
    await asyncio.wait_for(ws.send_json({"event": "caller_lookup", "caller": caller.public()}), 5)
    details = {
        "not_provided": "No caller number provided; database lookup skipped.",
        "invalid": "Invalid caller number; database lookup skipped.",
        "unavailable": "Number database unavailable; caller risk is unknown.",
        "no_match": "No match in the local dataset; this does not establish safety.",
        "matched": "Warning: the supplied number is listed in the suspicious-number dataset.",
    }
    await asyncio.wait_for(
        ws.send_json(
            {
                "event": "tool",
                "tool": "lookup",
                "status": caller.status,
                "detail": details[caller.status],
            }
        ),
        5,
    )
    return caller


async def relay(
    ws: WebSocket,
    settings: Settings,
    factory: AgentFactory,
    session_id: str,
    caller: CallerLookup,
) -> None:
    started = time.monotonic()
    session: AgentSession | None = None
    tasks: list[asyncio.Task] = []
    incoming: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
    provider_ready = asyncio.Event()
    finishing = asyncio.Event()
    finish_budget = settings.request_timeout_seconds + 15

    async def receive_microphone() -> None:
        while True:
            wait = finish_budget if finishing.is_set() else settings.media_idle_timeout_seconds
            message = await asyncio.wait_for(ws.receive(), wait)
            if message["type"] == "websocket.disconnect":
                return
            if "text" in message:
                raw = message["text"]
                if len(raw) > 1024:
                    raise ProtocolError("Control message too large")
                control = json.loads(raw)
                if control == {"event": "stop"}:
                    return
                if control == {"event": "finish"}:
                    finishing.set()
                    continue
                if control == {"event": "cancel_warning"} and session is not None:
                    await session.cancel_warning()
                    continue
                raise ProtocolError("Unexpected control message")
            audio = message.get("bytes", b"")
            if not 0 < len(audio) <= 9600 or len(audio) % 2:
                raise ProtocolError("Expected at most 200ms of PCM16")
            if finishing.is_set():
                continue
            try:
                incoming.put_nowait(audio)
            except asyncio.QueueFull as exc:
                raise ProtocolError("Microphone queue overflow") from exc

    async def finalize() -> None:
        # Stop accepting audio at the capture limit; allow a bounded final flush.
        try:
            await asyncio.wait_for(finishing.wait(), settings.max_session_seconds)
        except TimeoutError:
            finishing.set()
        async with asyncio.timeout(finish_budget):
            await provider_ready.wait()
            await incoming.join()
            assert session is not None
            await session.finish()
        # The terminal event follows final results; sender completion closes the relay.
        await asyncio.Future()

    async def forward_audio() -> None:
        assert session is not None
        while True:
            audio = await incoming.get()
            try:
                await asyncio.wait_for(session.send_audio(audio), 5)
            finally:
                incoming.task_done()

    async def send_agent() -> None:
        assert session is not None
        while True:
            event = await session.receive()
            if event is None:
                return
            if event.payload:
                await asyncio.wait_for(ws.send_json(event.payload), 5)
            for offset in range(0, len(event.audio), 1920):
                await asyncio.wait_for(ws.send_bytes(event.audio[offset : offset + 1920]), 5)

    async def run_provider() -> None:
        nonlocal session
        await asyncio.wait_for(
            ws.send_json(
                {
                    "event": "tool",
                    "tool": "stt",
                    "status": "connecting",
                    "detail": "Opening the Gradium transcription connection.",
                }
            ),
            5,
        )
        session = await asyncio.wait_for(
            factory.open(caller, session_id), settings.session_start_timeout_seconds
        )
        await asyncio.wait_for(
            ws.send_json(
                {
                    "event": "ready",
                    "input_rate": INPUT_RATE,
                    "output_rate": OUTPUT_RATE,
                    "caller": caller.public(),
                    "email_status": settings.email_status(),
                }
            ),
            5,
        )
        provider_ready.set()
        provider_tasks = [asyncio.create_task(coro()) for coro in (forward_audio, send_agent)]
        try:
            done, _ = await asyncio.wait(provider_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in provider_tasks:
                task.cancel()
            await asyncio.gather(*provider_tasks, return_exceptions=True)

    try:
        async with asyncio.timeout(settings.max_session_seconds + finish_budget):
            tasks = [
                asyncio.create_task(coro()) for coro in (receive_microphone, run_provider, finalize)
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        with CancelScope(shield=True):
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if session is not None:
                with suppress(Exception):
                    await asyncio.wait_for(session.close(), 3)
            log.info(
                "screening_closed id=%s duration_s=%.2f", session_id, time.monotonic() - started
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
    session_id = uuid.uuid4().hex
    code, reason = 1000, "session-ended"
    try:
        await ws.accept()
        caller = await read_start(ws, settings)
        log.info("screening_started id=%s active=%d", session_id, len(registry.sockets))
        await relay(ws, settings, factory, session_id, caller)
    except WebSocketDisconnect:
        pass
    except (ProtocolError, ValueError, KeyError, TypeError, AttributeError):
        code, reason = 1008, "invalid-audio"
        log.warning("screening_protocol_error id=%s", session_id)
    except TimeoutError:
        code, reason = 1011, "session-timeout"
        log.warning("screening_timeout id=%s", session_id)
    except Exception as exc:
        code, reason = 1011, "provider-unavailable"
        log.error("screening_provider_error id=%s type=%s", session_id, type(exc).__name__)
    finally:
        registry.sockets.discard(ws)
        with suppress(Exception):
            await ws.close(code=code, reason=reason)
