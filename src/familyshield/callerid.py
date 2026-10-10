"""Caller-ID signal via Twilio Lookup. A known number is a signal, not proof
of identity — caller IDs can be spoofed. This never blocks the call flow:
any failure (funding, network, unsupported country) degrades to an empty
result rather than raising.
"""

from dataclasses import dataclass

import httpx

from .config import Settings

_LOOKUP_URL = "https://lookups.twilio.com/v2/PhoneNumbers/{number}"


@dataclass(frozen=True)
class LookupResult:
    line_type: str = ""
    carrier_name: str = ""
    caller_name: str = ""
    error: str = ""


async def lookup_caller_id(settings: Settings, phone_number: str) -> LookupResult:
    if not phone_number:
        return LookupResult(error="no phone number")
    auth_token = settings.value("twilio_auth_token")
    if not settings.twilio_account_sid or not auth_token:
        return LookupResult(error="twilio credentials not configured")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                _LOOKUP_URL.format(number=phone_number),
                params={"Fields": "line_type_intelligence,caller_name"},
                auth=(settings.twilio_account_sid, auth_token),
            )
    except httpx.HTTPError as exc:
        return LookupResult(error=f"lookup request failed: {type(exc).__name__}")

    if response.status_code != 200:
        return LookupResult(error=f"lookup returned {response.status_code}")

    body = response.json()
    line_type_intel = body.get("line_type_intelligence") or {}
    caller_name_block = body.get("caller_name") or {}
    return LookupResult(
        line_type=str(line_type_intel.get("type") or ""),
        carrier_name=str(line_type_intel.get("carrier_name") or ""),
        caller_name=str(caller_name_block.get("caller_name") or ""),
    )
