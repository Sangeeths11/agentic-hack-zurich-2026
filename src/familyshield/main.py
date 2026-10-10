from __future__ import annotations

import logging

from fastapi import FastAPI

from .twilio_routes import router as twilio_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Family Shield — Twilio Bridge")
app.include_router(twilio_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
