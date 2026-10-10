"""Combine the contact directory with the Twilio Lookup signal into one
caller-ID screening result, and persist it to the call log. Never blocks or
changes call routing by itself — a later stage decides what to do with it.
"""

from dataclasses import dataclass

from .callerid import LookupResult, lookup_caller_id
from .config import Settings
from .contacts import Contact, find_contact_by_phone, log_call


@dataclass(frozen=True)
class ScreeningResult:
    contact: Contact | None
    lookup: LookupResult


async def screen_caller(settings: Settings, caller_number: str) -> ScreeningResult:
    contact = (
        find_contact_by_phone(settings.contacts_db_path, caller_number) if caller_number else None
    )
    lookup = await lookup_caller_id(settings, caller_number)
    return ScreeningResult(contact=contact, lookup=lookup)


def record_call_screening(
    settings: Settings, *, call_sid: str, caller_number: str, result: ScreeningResult
) -> None:
    log_call(
        settings.contacts_db_path,
        call_sid=call_sid,
        caller_number=caller_number,
        contact_id=result.contact.id if result.contact else None,
        line_type=result.lookup.line_type,
        carrier_name=result.lookup.carrier_name,
        lookup_error=result.lookup.error,
    )
