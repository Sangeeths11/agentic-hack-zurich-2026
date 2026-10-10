"""FastAPI application factory, TwiML webhooks and Media Streams endpoint."""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import FormData, UploadFile
from twilio.twiml.voice_response import Connect, VoiceResponse

from .agent import AgentFactory, GradbotFactory
from .bridge import CallRegistry, handle_stream
from .config import Settings
from .security import stream_ticket, valid_signature

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, factory: AgentFactory | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = settings if settings is not None else Settings()
    factory = factory if factory is not None else GradbotFactory(settings)
    registry = CallRegistry(settings.max_concurrent_calls)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await registry.shutdown()

    app = FastAPI(title="Family Shield Voice Agent", version="0.2.0", lifespan=lifespan)
    app.state.registry = registry

    async def verified_form(request: Request):
        if settings.missing():
            raise HTTPException(503, "Voice service is not configured")
        # Reject large bodies before parsing. Twilio's voice forms are small.
        if request.headers.get("content-type", "").split(";")[0] != (
            "application/x-www-form-urlencoded"
        ):
            raise HTTPException(415, "Expected URL-encoded form")
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 16_384:
                        raise HTTPException(413, "Request too large")
        except TimeoutError as exc:
            raise HTTPException(408, "Body timeout") from exc
        try:
            fields: list[tuple[str, str | UploadFile]] = [
                (key, value)
                for key, value in parse_qsl(
                    body.decode("utf-8"), keep_blank_values=True, max_num_fields=100
                )
            ]
            form = FormData(fields)
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(400, "Invalid form") from exc
        path = request.url.path
        if request.url.query:
            path += "?" + request.url.query
        if not valid_signature(settings, path, form, request.headers.get("x-twilio-signature", "")):
            raise HTTPException(403, "Invalid Twilio signature")
        if form.get("AccountSid") != settings.twilio_account_sid:
            raise HTTPException(403, "Unexpected account")
        return form

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        configured = not settings.missing() and not registry.draining
        return JSONResponse(
            {"status": "ready" if configured else "not_ready"},
            status_code=200 if configured else 503,
        )

    @app.post("/twilio/voice")
    async def voice(request: Request):
        form = await verified_form(request)
        call_sid = str(form.get("CallSid", ""))
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call_sid):
            raise HTTPException(400, "Invalid CallSid")
        response = VoiceResponse()
        if registry.draining or len(registry.sockets) >= registry.limit:
            response.reject(reason="busy")
        else:
            connect = Connect()
            stream = connect.stream(url=settings.websocket_url)
            stream.parameter(name="ticket", value=stream_ticket(settings, call_sid))
            response.append(connect)
            # Twilio continues here if the stream ends or cannot connect.
            response.say(
                "Der Assistent ist gerade nicht verfügbar. Auf Wiederhören.", language="de-DE"
            )
            response.hangup()
        return Response(str(response), media_type="application/xml")

    @app.post("/twilio/status")
    async def status(request: Request):
        form = await verified_form(request)
        sid, state = str(form.get("CallSid", "")), str(form.get("CallStatus", ""))
        if re.fullmatch(r"CA[0-9a-fA-F]{32}", sid) and state in {
            "queued",
            "initiated",
            "ringing",
            "in-progress",
            "completed",
            "busy",
            "failed",
            "no-answer",
            "canceled",
        }:
            log.info("call_status sid=%s status=%s", sid, state)
        return Response(status_code=204)

    @app.websocket("/twilio/stream")
    async def stream(ws: WebSocket):
        if (
            settings.missing()
            or ws.url.query
            or not valid_signature(
                settings,
                "/twilio/stream",
                {},
                ws.headers.get("x-twilio-signature", ""),
                websocket=True,
            )
        ):
            await ws.close(code=1008)
            return
        await handle_stream(ws, settings, factory, registry)

    return app
