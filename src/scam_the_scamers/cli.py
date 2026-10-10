"""Local listener startup and voice-provider diagnostics."""

import argparse
import asyncio
import json
import logging
import sys
import time

from pydantic import ValidationError

from .agent import GradbotFactory
from .config import Settings


async def check_voice(settings: Settings) -> dict:
    """Exercise real STT + LLM + TTS startup, without making a telephone call."""
    session = None
    feeder = None
    started = time.monotonic()
    try:
        async with asyncio.timeout(30):
            session = await GradbotFactory(settings).open()

            async def feed_silence():
                while True:
                    await session.send_audio(b"\xff" * 480)  # 20ms at Gradbot's 24kHz
                    await asyncio.sleep(0.02)

            feeder = asyncio.create_task(feed_silence())
            while True:
                event = await session.receive()
                if event is None:
                    raise RuntimeError("Voice provider closed before the first audio")
                if event.kind == "audio" and event.audio:
                    return {
                        "voice_pipeline": "passed",
                        "first_audio_ms": round((time.monotonic() - started) * 1000),
                    }
    finally:
        if feeder is not None:
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
        if session is not None:
            await asyncio.wait_for(session.close(), 3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scam the Scamers local microphone listener")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Open the listener at http://localhost:8082")
    check = commands.add_parser("doctor", help="Validate configuration without revealing secrets")
    check.add_argument("--live", action="store_true", help="Also test providers (uses API credits)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for logger in ("httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.WARNING)
    try:
        settings = Settings()
        if args.command == "serve":
            import uvicorn

            from .main import create_app

            app = create_app(settings)
            print("Listener: http://localhost:8082 — enable the microphone in your browser.")
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=8082,
                ws_max_size=32768,
                ws_max_queue=16,
                timeout_graceful_shutdown=10,
                log_level="info",
            )
            return
        missing = settings.missing()
        if missing:
            print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
            raise SystemExit(1)
        if args.command == "doctor":
            print("Configuration valid. This does not verify provider credentials.")
            if args.live:
                print(json.dumps(asyncio.run(check_voice(settings)), indent=2))
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]).upper() for error in exc.errors()})
        print("Invalid configuration fields: " + ", ".join(fields), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as exc:
        # Do not print raw provider responses: these may contain secrets or phone numbers.
        print(
            f"Command failed ({type(exc).__name__}). Check configuration, provider dashboard "
            "and network. No automatic retry was made.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
