"""Local HTTP + WebSocket benchmark with synthetic echo, no provider calls.

Run: uv run python scripts/benchmark.py --calls 4 --seconds 5
Measures transport/resampling overhead, NOT speech recognition or AI latency.
"""

import argparse
import asyncio
import base64
import json
import socket
import statistics
import time
from pathlib import Path
from xml.etree import ElementTree

import httpx
import uvicorn
from websockets.asyncio.client import connect

from familyshield.agent import AgentEvent
from familyshield.audio import UlawResampler
from familyshield.main import create_app
from familyshield.simulation import (
    ACCOUNT_SID,
    signature,
    simulation_settings,
    start_message,
    tone,
)


class EchoSession:
    def __init__(self):
        self.events = asyncio.Queue(maxsize=16)
        self.converter = UlawResampler(24000, 48000)
        self.closed = False
        self.events.put_nowait(AgentEvent("audio", tone(48000)))

    async def send_audio(self, audio):
        await self.events.put(AgentEvent("audio", self.converter.convert(audio)))

    async def receive(self):
        return await self.events.get()

    async def close(self):
        self.closed = True


class EchoFactory:
    def __init__(self):
        self.sessions = []

    async def open(self):
        session = EchoSession()
        self.sessions.append(session)
        return session


async def benchmark(calls: int, seconds: int) -> dict:
    settings = simulation_settings(max_concurrent_calls=calls)
    factory = EchoFactory()
    app = create_app(settings, factory)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            ws="websockets-sansio",
            ws_max_size=32768,
            lifespan="on",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    latencies = []
    frames = seconds * 50

    async def one_call(index: int):
        call_sid = f"CA{index + 1:032x}"
        stream_sid = f"MZ{index + 1:032x}"
        params = {"AccountSid": ACCOUNT_SID, "CallSid": call_sid}
        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"http://127.0.0.1:{port}/twilio/voice",
                data=params,
                headers=signature(settings, "/twilio/voice", params),
            )
        response.raise_for_status()
        ticket = (
            ElementTree.fromstring(response.text).find("./Connect/Stream/Parameter").attrib["value"]
        )
        async with connect(
            f"ws://127.0.0.1:{port}/twilio/stream",
            additional_headers=signature(
                settings,
                "/twilio/stream",
                websocket=True,
            ),
        ) as ws:
            await ws.send(json.dumps(start_message(ticket, call_sid, stream_sid)))
            for _ in range(5):
                assert json.loads(await ws.recv())["event"] == "media"
            sent_at: asyncio.Queue[float] = asyncio.Queue()
            payload = base64.b64encode(tone(8000, seconds=0.02)).decode()

            async def send():
                start = time.perf_counter()
                for i in range(frames):
                    await asyncio.sleep(max(0, start + i * 0.02 - time.perf_counter()))
                    await sent_at.put(time.perf_counter())
                    await ws.send(
                        json.dumps(
                            {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"track": "inbound", "payload": payload},
                            }
                        )
                    )

            async def receive():
                for _ in range(frames):
                    event = json.loads(await ws.recv())
                    assert event["event"] == "media"
                    assert event["streamSid"] == stream_sid
                    assert 0 < len(base64.b64decode(event["media"]["payload"])) <= 160
                    latencies.append((time.perf_counter() - await sent_at.get()) * 1000)

            await asyncio.gather(send(), receive())
            await ws.send(json.dumps({"event": "stop", "streamSid": stream_sid}))
            await ws.wait_closed()

    try:
        async with asyncio.timeout(5):
            while not server.started:
                if server_task.done():
                    server_task.result()
                await asyncio.sleep(0.01)
        async with asyncio.timeout(seconds + 15):
            await asyncio.gather(*(one_call(i) for i in range(calls)))
        assert not app.state.registry.sockets
        assert all(session.closed for session in factory.sessions)
    finally:
        server.should_exit = True
        await server_task
        sock.close()
    ordered = sorted(latencies)
    return {
        "mode": "localhost synthetic echo; excludes cloud AI and PSTN latency",
        "concurrent_calls": calls,
        "duration_seconds": seconds,
        "frames": len(latencies),
        "transport_p50_ms": round(statistics.median(ordered), 3),
        "transport_p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 3),
        "transport_max_ms": round(max(ordered), 3),
        "cleanup": "passed",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=4)
    parser.add_argument("--seconds", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.calls <= 100 or not 1 <= args.seconds <= 60:
        parser.error("Use 1–100 calls and 1–60 seconds")
    report = asyncio.run(benchmark(args.calls, args.seconds))
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
