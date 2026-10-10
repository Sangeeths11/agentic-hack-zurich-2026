"""Authenticate against configured public URLs, never untrusted proxy headers."""

import hashlib
import hmac
import time

from twilio.request_validator import RequestValidator

from .config import Settings


def valid_signature(
    settings: Settings, path: str, params, signature: str, *, websocket: bool = False
) -> bool:
    token = settings.value("twilio_auth_token")
    if not token or not settings.public_http_url or not signature:
        return False
    url = settings.public_http_url + path
    urls = [url]
    if websocket:
        # Support the HTTPS upgrade URL and exact wss Stream URL. Both are canonical
        # and require the same account secret; neither trusts Host/forwarded headers.
        urls.append(url.replace("https://", "wss://", 1))
    validator = RequestValidator(token)
    return any(validator.validate(candidate, params, signature) for candidate in urls)


def stream_ticket(settings: Settings, call_sid: str, expires: int | None = None) -> str:
    expires = expires if expires is not None else int(time.time()) + 120
    payload = f"{call_sid}.{expires}"
    digest = hmac.new(
        settings.value("twilio_auth_token").encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{expires}.{digest}"


def valid_ticket(settings: Settings, call_sid: str, ticket: str) -> bool:
    try:
        expires = int(ticket.split(".", 1)[0])
    except (ValueError, AttributeError):
        return False
    if not int(time.time()) <= expires <= int(time.time()) + 120:
        return False
    return hmac.compare_digest(stream_ticket(settings, call_sid, expires), ticket)
