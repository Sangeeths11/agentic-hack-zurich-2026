"""Local contact directory and call log. SQLite, stdlib only.

Minimal data model per the brief: Contact (enrolled relative/caregiver) and
Call (one row per inbound call, with its screening/risk outcome). Household
is implicit — this is a single-household hackathon prototype.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    phone_number: str
    relationship: str
    role: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL UNIQUE,
    relationship TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'relative',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL UNIQUE,
    caller_number TEXT NOT NULL,
    contact_id INTEGER REFERENCES contacts(id),
    line_type TEXT NOT NULL DEFAULT '',
    carrier_name TEXT NOT NULL DEFAULT '',
    lookup_error TEXT NOT NULL DEFAULT '',
    risk_score INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _connect(path: str):
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_contact(row: sqlite3.Row) -> Contact:
    return Contact(row["id"], row["name"], row["phone_number"], row["relationship"], row["role"])


def add_contact(
    path: str, *, name: str, phone_number: str, relationship: str, role: str
) -> Contact:
    with _connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO contacts (name, phone_number, relationship, role) VALUES (?, ?, ?, ?)",
            (name, phone_number, relationship, role),
        )
        return Contact(cursor.lastrowid, name, phone_number, relationship, role)


def list_contacts(path: str) -> list[Contact]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT * FROM contacts ORDER BY name").fetchall()
        return [_row_to_contact(r) for r in rows]


def find_contact_by_phone(path: str, phone_number: str) -> Contact | None:
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE phone_number = ?", (phone_number,)
        ).fetchone()
        return _row_to_contact(row) if row is not None else None


def log_call(
    path: str,
    *,
    call_sid: str,
    caller_number: str,
    contact_id: int | None,
    line_type: str,
    carrier_name: str,
    lookup_error: str,
    risk_level: str = "none",
) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO calls
                (call_sid, caller_number, contact_id, line_type,
                 carrier_name, lookup_error, risk_level)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(call_sid) DO NOTHING
            """,
            (
                call_sid,
                caller_number,
                contact_id,
                line_type,
                carrier_name,
                lookup_error,
                risk_level,
            ),
        )


def update_call_risk(path: str, call_sid: str, score: int, risk_level: str) -> None:
    """Record a call's current cumulative risk score/tier. The caller
    (bridge.py's CallRiskScore) only ever increases its own total, but this
    still guards against writing a stale, out-of-order lower score.
    """
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT risk_score FROM calls WHERE call_sid = ?", (call_sid,)
        ).fetchone()
        if row is not None and score < row["risk_score"]:
            return
        conn.execute(
            "UPDATE calls SET risk_score = ?, risk_level = ? WHERE call_sid = ?",
            (score, risk_level, call_sid),
        )


def call_is_known_contact(path: str, call_sid: str) -> bool:
    """Whether the call already logged by record_call_screening matched an
    enrolled contact. Lets the bridge seed its risk score the same way the
    webhook's caller-ID screening already classified this call.
    """
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT contact_id FROM calls WHERE call_sid = ?", (call_sid,)
        ).fetchone()
        return row is not None and row["contact_id"] is not None
