"""Scam phone number lookup module.

Provides sub-millisecond local lookups against the consolidated scam and robocall
database (73,000+ numbers in E.164 format).
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# Default path to the compiled SQLite database
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCAM_DB_PATH = REPO_ROOT / "data" / "scam_numbers" / "scam_numbers.db"
DEFAULT_SCAM_TXT_PATH = REPO_ROOT / "data" / "scam_numbers" / "scam_numbers_e164.txt"

_E164_REGEX = re.compile(r"^\+[1-9]\d{6,14}$")


@dataclass(frozen=True)
class ScamRecord:
    phone_number: str
    source: str
    raw_number: str
    created_at: str


def normalize_to_e164(phone_number: str, default_country_code: str = "1") -> str:
    """Normalize phone number to standard E.164 format (+[country_code][digits]).

    Strips spaces, dashes, parentheses. If missing '+', assumes default country code (NANP '1').
    """
    s = phone_number.strip()
    if not s:
        return ""
    if s.startswith("+"):
        cleaned = "+" + re.sub(r"\D", "", s[1:])
        return cleaned if _E164_REGEX.match(cleaned) else ""

    digits = re.sub(r"\D", "", s)
    if len(digits) == 10 and default_country_code == "1":
        candidate = f"+1{digits}"
        return candidate if _E164_REGEX.match(candidate) else ""
    if len(digits) == 11 and digits.startswith("1"):
        candidate = f"+{digits}"
        return candidate if _E164_REGEX.match(candidate) else ""

    return f"+{digits}" if _E164_REGEX.match(f"+{digits}") else ""


def is_scam_number(phone_number: str, db_path: str | Path | None = None) -> bool:
    """Return True if the given phone number exists in the scam database."""
    return lookup_scam_number(phone_number, db_path=db_path) is not None


def lookup_scam_number(phone_number: str, db_path: str | Path | None = None) -> ScamRecord | None:
    """Look up a phone number in the scam database.

    Returns a ScamRecord if matched, or None otherwise.
    """
    if not phone_number:
        return None

    path = Path(db_path) if db_path else DEFAULT_SCAM_DB_PATH
    if not path.is_file():
        return None

    normalized = normalize_to_e164(phone_number)
    candidates = [c for c in (phone_number.strip(), normalized) if c]

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        with conn:
            for candidate in candidates:
                cursor = conn.execute(
                    "SELECT phone_number, source, raw_number, created_at "
                    "FROM scam_numbers WHERE phone_number = ?",
                    (candidate,),
                )
                row = cursor.fetchone()
                if row is not None:
                    return ScamRecord(
                        phone_number=row["phone_number"],
                        source=row["source"],
                        raw_number=row["raw_number"],
                        created_at=row["created_at"],
                    )
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return None


def load_scam_set(txt_path: str | Path | None = None) -> set[str]:
    """Load the scam numbers text file into an in-memory set for O(1) in-process lookups."""
    path = Path(txt_path) if txt_path else DEFAULT_SCAM_TXT_PATH
    if not path.is_file():
        return set()

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}
