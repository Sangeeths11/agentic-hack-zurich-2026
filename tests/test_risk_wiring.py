import asyncio
import sqlite3

from fastapi.testclient import TestClient

from familyshield.agent import AgentEvent
from familyshield.main import create_app
from familyshield.security import stream_ticket
from familyshield.simulation import (
    ACCOUNT_SID,
    CALL_SID,
    signature,
    simulation_settings,
    start_message,
)


class _TranscriptSession:
    """Speaks a scripted sequence of transcript segments, then one audio
    frame — the test waits for that frame on the websocket as proof every
    prior transcript event (processed strictly in order, one at a time, by
    the same relay loop) has already been scored before it sends "stop".
    """

    def __init__(self, texts: list[str]):
        self.events: asyncio.Queue = asyncio.Queue()
        for text in texts:
            self.events.put_nowait(AgentEvent("transcript", text=text))
        self.events.put_nowait(AgentEvent("audio", b"\xff" * 160))
        self.closed = False

    async def send_audio(self, audio: bytes) -> None:
        pass

    async def receive(self) -> AgentEvent | None:
        try:
            return self.events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def close(self) -> None:
        self.closed = True


class _TranscriptFactory:
    def __init__(self, texts: list[str]):
        self._texts = texts
        self.sessions: list[_TranscriptSession] = []

    async def open(self) -> _TranscriptSession:
        session = _TranscriptSession(self._texts)
        self.sessions.append(session)
        return session


def _call_row(db_path: str, call_sid: str) -> tuple[int, str]:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT risk_score, risk_level FROM calls WHERE call_sid = ?", (call_sid,)
    ).fetchone()
    conn.close()
    assert row is not None
    return row


def _run_call(settings, texts: list[str]) -> None:
    factory = _TranscriptFactory(texts)
    with TestClient(create_app(settings, factory)) as client:
        params = {"AccountSid": ACCOUNT_SID, "CallSid": CALL_SID}
        client.post(
            "/twilio/voice", data=params, headers=signature(settings, "/twilio/voice", params)
        )
        with client.websocket_connect(
            "/twilio/stream", headers=signature(settings, "/twilio/stream", websocket=True)
        ) as ws:
            ws.send_json(start_message(stream_ticket(settings, CALL_SID)))
            # Synchronization point: this only arrives after every queued
            # transcript event ahead of it in the session has been scored.
            assert ws.receive_json()["event"] == "media"
            ws.send_json({"event": "stop", "streamSid": "MZ" + "3" * 32})
            assert ws.receive()["type"] == "websocket.close"


def test_a_scripted_scam_call_escalates_the_recorded_risk_score():
    settings = simulation_settings()
    _run_call(
        settings,
        [
            "Grandma, it's me, Marco.",
            "I've been arrested. I'm in a cell.",
            "I need it within the hour.",
            "Please don't tell Mum and Dad.",
            "I need five thousand francs, in cash.",
            "A courier will come to your door.",
        ],
    )
    score, level = _call_row(settings.contacts_db_path, CALL_SID)
    # Unknown caller (+10) plus all six text signals, matching test_risk.py's
    # unit-level reproduction of the same walkthrough.
    assert score == 55
    assert level == "amber"


def test_benign_caller_speech_leaves_risk_at_the_unknown_number_baseline():
    settings = simulation_settings()
    _run_call(settings, ["Hi, I just wanted to say hello."])
    score, level = _call_row(settings.contacts_db_path, CALL_SID)
    assert score == 10  # only the unknown-number seed — no text signal fired
    assert level == "none"
