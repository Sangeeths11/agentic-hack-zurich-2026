"""Local listener UI and same-origin browser audio WebSocket."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import AgentFactory, GradbotFactory
from .bridge import SessionRegistry, handle_stream
from .config import Settings

STATIC = Path(__file__).with_name("static")


def create_app(settings: Settings | None = None, factory: AgentFactory | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = settings if settings is not None else Settings()
    factory = factory if factory is not None else GradbotFactory(settings)
    registry = SessionRegistry(settings.max_concurrent_sessions)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await registry.shutdown()

    app = FastAPI(title="Scam the Scamers Local Listener", version="0.3.0", lifespan=lifespan)
    # Also reject DNS rebinding against the local service.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.state.registry = registry
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
        return {
            "configured": not settings.missing(),
            "missing": settings.missing(),
            "operator": settings.operator_name,
            "max_session_seconds": settings.max_session_seconds,
        }

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
        await handle_stream(ws, settings, factory, registry)

    return app
