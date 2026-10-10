import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from support import LOCAL_HEADERS, LOCAL_URL, simulation_settings, start_message

from scam_the_scamers.agent import AgentEvent
from scam_the_scamers.main import create_app


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
        self.events.put_nowait(AgentEvent("audio", b"\xff" * 1920))
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


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


@pytest.fixture
def no_llm(monkeypatch):
    """Force every chat-completions call to fail fast, with no real network
    traffic — llm_api_key can't just be blanked out, because main.py's
    websocket route also gates the whole session on settings.missing()
    (gradbot needs that same key). This exercises bridge.py's fallback to
    the regex detector the same way a real LLM outage would.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "offline for this test"})

    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = _MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _risk_events(texts: list[str]) -> list[dict]:
    """Drain every message from a scripted session: the server closes the
    socket on its own once the queued transcript+audio events run out (no
    "stop" needed), sending "websocket.close" as the final message.
    """
    factory = _TranscriptFactory(texts)
    events = []
    with TestClient(create_app(simulation_settings(), factory), base_url=LOCAL_URL) as client:
        with client.websocket_connect(
            "ws://localhost:8082/audio/stream", headers=LOCAL_HEADERS
        ) as ws:
            ws.send_json(start_message())
            assert ws.receive_json()["event"] == "ready"
            while True:
                message = ws.receive()
                if message["type"] == "websocket.close":
                    break
                if "text" in message:
                    event = json.loads(message["text"])
                    if event["event"] == "risk":
                        events.append(event)
    return events


def test_a_scripted_scam_call_escalates_the_pushed_risk_score(no_llm):
    events = _risk_events(
        [
            "Grandma, it's me, Marco.",
            "I've been arrested. I'm in a cell.",
            "I need it within the hour.",
            "Please don't tell Mum and Dad.",
            "I need five thousand francs, in cash.",
            "A courier will come to your door.",
        ]
    )
    # One "risk" push per newly-scored category, in order, each cumulative.
    assert [e["score"] for e in events] == [5, 13, 20, 30, 38, 45]
    assert [e["tier"] for e in events] == ["none", "none", "amber", "red", "critical", "critical"]


def test_benign_speech_pushes_no_risk_event(no_llm):
    events = _risk_events(["Hi, I just wanted to say hello."])
    assert events == []
