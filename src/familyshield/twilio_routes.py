from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from .config import settings

log = logging.getLogger("familyshield.twilio")

router = APIRouter()

_validator = RequestValidator(settings.twilio_auth_token) if settings.twilio_auth_token else None


async def _verified_form(request: Request) -> dict:
    """Parse a Twilio webhook's form params after verifying its signature.

    Fails CLOSED when TWILIO_AUTH_TOKEN is unset, unless ALLOW_INSECURE_LOCAL=1
    is explicitly set for local dev without real Twilio credentials.
    """
    form = await request.form()
    params = {k: v for k, v in form.multi_items()}

    if _validator is None:
        if settings.allow_insecure_local:
            log.warning("TWILIO_AUTH_TOKEN unset — skipping signature check (ALLOW_INSECURE_LOCAL=1)")
            return params
        raise HTTPException(status_code=503, detail="server misconfigured: TWILIO_AUTH_TOKEN not set")

    # Reconstruct the URL Twilio signed. Behind a tunnel/proxy, the scheme and
    # host Twilio actually hit are carried in the forwarded headers.
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    signed_url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        signed_url += f"?{request.url.query}"

    signature = request.headers.get("x-twilio-signature", "")
    if not _validator.validate(signed_url, params, signature):
        log.warning("twilio signature mismatch on %s from %s", request.url.path, request.client)
        raise HTTPException(status_code=403, detail="invalid Twilio signature")

    return params


@router.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: Request) -> PlainTextResponse:
    """Answers an inbound call: greeting, then transfer to the configured
    destination number.

    This is the phone-path foundation only — it proves a real caller reaches
    a real phone through Twilio. The screening agent (claim extraction,
    confirmation, live protection) connects here next, replacing the
    unconditional dial with routing logic.
    """
    params = await _verified_form(request)
    log.info("inbound call from=%s to=%s sid=%s", params.get("From"), params.get("To"), params.get("CallSid"))

    response = VoiceResponse()
    response.say(
        f"Hello, this is {settings.operator_name}. Connecting your call now.",
        voice="Polly.Joanna",
    )

    if settings.destination_phone_number:
        response.dial(settings.destination_phone_number)
    else:
        response.say("No destination number is configured yet. Goodbye.")
        response.hangup()

    return PlainTextResponse(str(response), media_type="application/xml")


@router.post("/twilio/status")
async def twilio_status(request: Request) -> dict:
    """Call status callback — logs call lifecycle events (ringing, answered,
    completed) for the duration of the phone-path test.
    """
    params = await _verified_form(request)
    log.info(
        "call status: sid=%s status=%s from=%s to=%s",
        params.get("CallSid"),
        params.get("CallStatus"),
        params.get("From"),
        params.get("To"),
    )
    return {"ok": True}
