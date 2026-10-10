"""Local listener UI and same-origin browser audio WebSocket."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import AgentFactory, ScreeningFactory
from .bridge import SessionRegistry, handle_stream
from .caller import lookup_caller
from .config import Settings
from .profiles import ProfileParser, ProfileStore, SettingsUpdate

STATIC = Path(__file__).with_name("static")


def create_app(
    settings: Settings | None = None,
    factory: AgentFactory | None = None,
    *,
    profile_parser: ProfileParser | None = None,
) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for name in ("httpx", "httpcore", "websockets"):
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = settings if settings is not None else Settings()
    factory = factory if factory is not None else ScreeningFactory(settings)
    registry = SessionRegistry(settings.max_concurrent_sessions)
    profile_store = ProfileStore(settings, profile_parser)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await profile_store.close()
        await registry.shutdown()

    app = FastAPI(title="Scam the Scammers Local Listener", version="0.4.0", lifespan=lifespan)
    # Also reject DNS rebinding against the local service.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.state.registry = registry
    app.state.profile_store = profile_store
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(
            STATIC / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
                "Permissions-Policy": "microphone=(self), camera=()",
            },
        )

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

    @app.get("/api/config")
    async def config():
        return JSONResponse(
            {
                "configured": not settings.missing(),
                "missing": settings.missing(),
                "warning_threshold": settings.warning_threshold,
                "whatsapp_threshold": settings.whatsapp_threshold,
                "whatsapp_cooldown_seconds": settings.whatsapp_cooldown_seconds,
                "email_status": settings.email_status(),
                "whatsapp_status": settings.whatsapp_status(),
                "guardian_phone": settings.value("guardian_phone"),
                "fallback_email": settings.value("fallback_email")
                if settings.email_status() == "configured"
                else "",
                "caller_default_region": settings.caller_default_region,
                "max_session_seconds": settings.max_session_seconds,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/settings")
    async def personal_settings():
        return JSONResponse(profile_store.public(), headers={"Cache-Control": "no-store"})

    @app.post("/api/settings")
    async def update_personal_settings(request: Request):
        headers = {"Cache-Control": "no-store"}
        origin = urlsplit(request.headers.get("origin", ""))
        if (
            request.url.query
            or origin.scheme != "http"
            or origin.netloc != request.headers.get("host")
            or origin.path
            or origin.query
            or origin.fragment
        ):
            return JSONResponse({"detail": "Same-origin request required."}, 403, headers=headers)
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return JSONResponse({"detail": "JSON request required."}, 415, headers=headers)
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 32768:
                        return JSONResponse(
                            {"detail": "Settings request too large."}, 413, headers=headers
                        )
                    raw.extend(chunk)
        except TimeoutError:
            return JSONResponse({"detail": "Settings request timed out."}, 408, headers=headers)
        try:
            update = SettingsUpdate.model_validate_json(raw)
        except ValueError:
            return JSONResponse({"detail": "Invalid settings request."}, 422, headers=headers)
        result = await profile_store.update(update)
        return JSONResponse(result, headers=headers)

    @app.post("/api/caller-lookup")
    async def caller_lookup(request: Request):
        # Keep entered phone numbers out of URLs/access logs. The local lookup
        # works without microphone access or cloud-provider configuration.
        headers = {"Cache-Control": "no-store"}
        origin = urlsplit(request.headers.get("origin", ""))
        if (
            request.url.query
            or origin.scheme != "http"
            or origin.netloc != request.headers.get("host")
            or origin.path
            or origin.query
            or origin.fragment
        ):
            return JSONResponse({"detail": "Same-origin request required."}, 403, headers=headers)
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return JSONResponse({"detail": "JSON request required."}, 415, headers=headers)
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 1024:
                        return JSONResponse(
                            {"detail": "Lookup request too large."}, 413, headers=headers
                        )
                    raw.extend(chunk)
        except TimeoutError:
            return JSONResponse({"detail": "Lookup request timed out."}, 408, headers=headers)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"detail": "Invalid lookup request."}, 422, headers=headers)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"caller_number"}
            or not isinstance(payload["caller_number"], str)
            or len(payload["caller_number"]) > 64
        ):
            return JSONResponse({"detail": "Invalid caller number."}, 422, headers=headers)
        caller = await asyncio.to_thread(
            lookup_caller,
            payload["caller_number"],
            settings.caller_default_region,
            settings.scam_db_path,
        )
        return JSONResponse({"caller": caller.public()}, headers=headers)

    @app.websocket("/audio/stream")
    async def stream(ws: WebSocket):
        # Browsers supply Origin; compare to the actual host, never proxy headers.
        origin = urlsplit(ws.headers.get("origin", ""))
        if (
            settings.missing()
            or registry.draining
            or ws.url.query
            or origin.scheme != "http"
            or origin.netloc != ws.headers.get("host")
            or origin.path
            or origin.query
            or origin.fragment
        ):
            await ws.close(code=1008)
            return
        await handle_stream(ws, settings, factory, registry, profile_store=profile_store)

    return app
