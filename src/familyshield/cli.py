"""Explicit operator commands. No public endpoint can initiate outbound calls."""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from pydantic import ValidationError
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from .agent import GradbotFactory
from .config import Settings


class OperatorError(Exception):
    """Safe, deliberately written diagnostic message."""


def twilio_client(settings: Settings) -> Client:
    return Client(
        settings.twilio_account_sid,
        settings.value("twilio_auth_token"),
        http_client=TwilioHttpClient(timeout=10, max_retries=0),
    )


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
    parser = argparse.ArgumentParser(description="Family Shield streaming phone assistant")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Run on 127.0.0.1:8082")
    check = commands.add_parser("doctor", help="Validate configuration without revealing secrets")
    check.add_argument("--live", action="store_true", help="Also test providers (uses API credits)")
    commands.add_parser("configure-twilio", help="Set this number's voice/status webhooks")
    commands.add_parser("call-me", help="Place one real call to TEST_PHONE_NUMBER")
    simulation = commands.add_parser("simulate", help="Offline signed Twilio/audio/interrupt test")
    simulation.add_argument("--output", type=Path, default=Path("artifacts/simulation.wav"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Twilio/httpx INFO logs can contain phone numbers or account URLs.
    for logger in ("twilio", "httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.WARNING)
    try:
        if args.command == "simulate":
            from .simulation import simulate

            print(json.dumps(simulate(args.output), indent=2))
            return
        settings = Settings()
        if args.command == "serve":
            import uvicorn

            uvicorn.run(
                "familyshield.main:create_app",
                factory=True,
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
            print("Configuration valid. This does not verify credentials or public reachability.")
            if args.live:
                twilio_client(settings).api.accounts(settings.twilio_account_sid).fetch()
                print(json.dumps(asyncio.run(check_voice(settings)), indent=2))
        elif args.command == "configure-twilio":
            client = twilio_client(settings)
            numbers = client.incoming_phone_numbers.list(
                phone_number=settings.twilio_phone_number, limit=2
            )
            if len(numbers) != 1:
                raise OperatorError(
                    "Configured Twilio number was not found uniquely in this account"
                )
            numbers[0].update(
                voice_url=settings.public_http_url + "/twilio/voice",
                voice_method="POST",
                status_callback=settings.public_http_url + "/twilio/status",
                status_callback_method="POST",
            )
            print("Twilio webhooks configured: " + settings.public_http_url + "/twilio/voice")
        elif args.command == "call-me":
            if not settings.test_phone_number:
                raise OperatorError("Set TEST_PHONE_NUMBER to your own smartphone first")
            if settings.test_phone_number == settings.twilio_phone_number:
                raise OperatorError("TEST_PHONE_NUMBER must differ from the agent number")
            call = twilio_client(settings).calls.create(
                to=settings.test_phone_number,
                from_=settings.twilio_phone_number,
                url=settings.public_http_url + "/twilio/voice",
                method="POST",
                status_callback=settings.public_http_url + "/twilio/status",
                status_callback_method="POST",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                timeout=30,
                time_limit=int(settings.max_call_seconds),
            )
            print("Test call created: " + call.sid)
    except OperatorError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
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
