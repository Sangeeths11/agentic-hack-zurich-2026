"""One bounded, cancellable full-duplex bridge per call."""

import asyncio
import base64
import binascii
import json
import logging
import re
import time
from contextlib import suppress

from fastapi import WebSocket, WebSocketDisconnect

from .agent import AgentFactory, AgentSession
from .audio import GRADBOT_INPUT_RATE, GRADBOT_OUTPUT_RATE, TWILIO_RATE, UlawResampler
from .config import Settings
from .contacts import call_is_known_contact, update_call_risk
from .risk import UNKNOWN_NUMBER_POINTS, CallRiskScore
from .security import valid_ticket

log = logging.getLogger(__name__)


class ProtocolError(ValueError):
    pass


async def read_event(ws: WebSocket, idle_seconds: float) -> dict:
    raw = await asyncio.wait_for(ws.receive_text(), idle_seconds)
    if len(raw) > 32_768:
        raise ProtocolError("Message too large")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ProtocolError("Expected object")
    return event


async def read_start(ws: WebSocket, settings: Settings) -> tuple[str, str]:
    async with asyncio.timeout(5):
        for _ in range(3):
            event = await read_event(ws, 5)
            if event.get("event") == "connected":
                continue
            if event.get("event") != "start":
                raise ProtocolError("Expected start")
            start = event.get("start", {})
            call_sid = start.get("callSid", "")
            stream_sid = event.get("streamSid", "")
            if not (
                re.fullmatch(r"CA[0-9a-fA-F]{32}", call_sid)
                and re.fullmatch(r"MZ[0-9a-fA-F]{32}", stream_sid)
            ):
                raise ProtocolError("Invalid identifiers")
            if start.get("accountSid") != settings.twilio_account_sid:
                raise ProtocolError("Unexpected account")
            if start.get("streamSid") != stream_sid:
                raise ProtocolError("Stream mismatch")
            if start.get("mediaFormat") != {
                "encoding": "audio/x-mulaw",
                "sampleRate": 8000,
                "channels": 1,
            }:
                raise ProtocolError("Expected mono 8kHz mu-law")
            ticket = start.get("customParameters", {}).get("ticket", "")
            if not valid_ticket(settings, call_sid, ticket):
                raise ProtocolError("Invalid stream ticket")
            return call_sid, stream_sid
    raise ProtocolError("Missing start")


async def relay(
    ws: WebSocket, settings: Settings, factory: AgentFactory, call_sid: str, stream_sid: str
) -> None:
    started = time.monotonic()
    session: AgentSession | None = None
    tasks: list[asyncio.Task] = []
    first_audio = asyncio.Event()
    inbound = UlawResampler(TWILIO_RATE, GRADBOT_INPUT_RATE)
    outbound = UlawResampler(GRADBOT_OUTPUT_RATE, TWILIO_RATE)
    # Seed the running risk score from the webhook's caller-ID screening
    # (already logged for this call_sid) — a known enrolled contact starts
    # clean; an unknown number starts with one signal already counted.
    known = await asyncio.to_thread(call_is_known_contact, settings.contacts_db_path, call_sid)
    risk_score = CallRiskScore(
        score=0 if known else UNKNOWN_NUMBER_POINTS,
        categories_hit=set() if known else {"unknown_number"},
    )
    if not known:
        await asyncio.to_thread(
            update_call_risk,
            settings.contacts_db_path,
            call_sid,
            risk_score.score,
            risk_score.tier,
        )

    async def receive_caller() -> None:
        assert session is not None
        while True:
            event = await read_event(ws, settings.media_idle_timeout_seconds)
            if event.get("streamSid") != stream_sid:
                raise ProtocolError("Stream mismatch")
            kind = event.get("event")
            if kind == "stop":
                return
            if kind in {"mark", "dtmf"}:
                continue
            if kind != "media" or event.get("media", {}).get("track") != "inbound":
                raise ProtocolError("Unexpected event")
            try:
                audio = base64.b64decode(event["media"]["payload"], validate=True)
            except (binascii.Error, ValueError, KeyError, TypeError) as exc:
                raise ProtocolError("Invalid audio") from exc
            if not 0 < len(audio) <= 8000:
                raise ProtocolError("Invalid audio size")
            await asyncio.wait_for(session.send_audio(inbound.convert(audio)), 5)

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
                outbound.reset()
                turn_ended_at = None
                await asyncio.wait_for(
                    ws.send_json(
                        {
                            "event": "clear",
                            "streamSid": stream_sid,
                        }
                    ),
                    5,
                )
            elif event.kind == "turn_end":
                if turn_ended_at is None:
                    turn_ended_at = time.monotonic()
            elif event.kind == "transcript":
                # Backend rule, not an LLM decision — model output alone never
                # authorises anything. The matched text itself is never logged,
                # only the resulting score, tier, and which signals fired.
                new_signals = risk_score.apply(event.text)
                if new_signals:
                    log.info(
                        "call_risk sid=%s score=%d tier=%s new_categories=%s",
                        call_sid,
                        risk_score.score,
                        risk_score.tier,
                        ",".join(s.category for s in new_signals),
                    )
                    await asyncio.to_thread(
                        update_call_risk,
                        settings.contacts_db_path,
                        call_sid,
                        risk_score.score,
                        risk_score.tier,
                    )
            elif event.kind == "audio" and event.audio:
                audio = outbound.convert(event.audio)
                # Small frames keep the Twilio playback buffer responsive to clear.
                for offset in range(0, len(audio), 160):
                    await asyncio.wait_for(
                        ws.send_json(
                            {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": base64.b64encode(
                                        audio[offset : offset + 160]
                                    ).decode("ascii")
                                },
                            }
                        ),
                        5,
                    )
                if audio and turn_ended_at is not None:
                    log.info(
                        "call_response_audio sid=%s latency_ms=%d",
                        call_sid,
                        (time.monotonic() - turn_ended_at) * 1000,
                    )
                    turn_ended_at = None
                if audio and not first_audio.is_set():
                    first_audio.set()
                    log.info(
                        "call_first_audio sid=%s latency_ms=%d",
                        call_sid,
                        (time.monotonic() - started) * 1000,
                    )

    async def watch_first_audio() -> None:
        await asyncio.wait_for(first_audio.wait(), settings.first_audio_timeout_seconds)
        await asyncio.Future()  # only a timeout should end the call

    try:
        async with asyncio.timeout(settings.max_call_seconds):
            session = await asyncio.wait_for(factory.open(), settings.session_start_timeout_seconds)
            tasks = [
                asyncio.create_task(receive_caller()),
                asyncio.create_task(send_agent()),
                asyncio.create_task(watch_first_audio()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if session is not None:
            with suppress(Exception):
                await asyncio.wait_for(session.close(), 3)
        log.info("call_closed sid=%s duration_s=%.2f", call_sid, time.monotonic() - started)


class CallRegistry:
    """Worker-local admission control. No pending HTTP state or sticky routing needed."""

    def __init__(self, limit: int):
        self.limit = limit
        self.sockets: set[WebSocket] = set()
        self.calls: set[str] = set()
        self.draining = False

    def acquire(self, ws: WebSocket) -> bool:
        # No await: this check and mutation are atomic on the worker's event loop.
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
    ws: WebSocket, settings: Settings, factory: AgentFactory, registry: CallRegistry
) -> None:
    if not registry.acquire(ws):
        await ws.close(code=1013)
        return
    call_sid = ""
    registered = False
    code = 1000
    try:
        await ws.accept()
        call_sid, stream_sid = await read_start(ws, settings)
        if call_sid in registry.calls:
            raise ProtocolError("Duplicate stream")
        registry.calls.add(call_sid)
        registered = True
        log.info("call_started sid=%s active=%d", call_sid, len(registry.calls))
        await relay(ws, settings, factory, call_sid, stream_sid)
    except WebSocketDisconnect:
        pass
    except (ProtocolError, ValueError, KeyError, TypeError, AttributeError):
        code = 1008
        log.warning("call_protocol_error sid=%s", call_sid)
    except TimeoutError:
        code = 1011
        log.warning("call_timeout sid=%s", call_sid)
    except Exception as exc:
        code = 1011
        # Provider errors may embed credentials or transcript fragments.
        log.error("call_provider_error sid=%s type=%s", call_sid, type(exc).__name__)
    finally:
        if registered:
            registry.calls.discard(call_sid)
        registry.sockets.discard(ws)
        with suppress(Exception):
            await ws.close(code=code)
