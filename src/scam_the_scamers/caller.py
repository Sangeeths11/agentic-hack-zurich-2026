"""Explicit lookup states for the preserved local suspicious-number dataset."""

import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import phonenumbers

from familyshield.scam_db import ScamDatabaseUnavailable, lookup_scam_number


@dataclass(frozen=True)
class CallerLookup:
    status: str
    number: str = ""
    source: str = ""
    listed_at: str = ""

    def public(self) -> dict:
        return asdict(self)


def lookup_caller(number: str, region: str = "CH", db_path: Path | None = None) -> CallerLookup:
    if not number.strip():
        return CallerLookup("not_provided")
    if len(number) > 64 or not re.fullmatch(r"[+\d() .\-]+", number):
        return CallerLookup("invalid")
    try:
        parsed = phonenumbers.parse(number, region)
        if parsed.extension:
            return CallerLookup("invalid")
        normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return CallerLookup("invalid")
    try:
        record = lookup_scam_number(normalized, db_path=db_path, strict=True)
    except (ScamDatabaseUnavailable, sqlite3.Error, OSError):
        return CallerLookup("unavailable", normalized)
    # Some preserved blocklist entries have unusual lengths. An exact database
    # match is still useful evidence; numbering-plan validation must not hide it.
    if record is not None:
        return CallerLookup("matched", normalized, record.source, record.created_at)
    if not phonenumbers.is_possible_number(parsed):
        return CallerLookup("invalid", normalized)
    return CallerLookup("no_match", normalized)
