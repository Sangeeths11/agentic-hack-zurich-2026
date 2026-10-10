"""Local listener startup and screening-provider diagnostics."""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from .config import Settings
from .providers import Classifier, Speech, STTSession


async def check_providers(
    settings: Settings,
    *,
    classifier: Classifier | None = None,
    speech: Speech | None = None,
    stt_open: Callable[[Settings], Awaitable[STTSession]] | None = None,
) -> dict:
    """Bounded synthetic probes; never record a person or send guardian messages."""
    from .caller import CallerLookup
    from .providers import GeminiClassifier, GradiumSTT, GradiumTTS, ProviderError

    classifier = classifier if classifier is not None else GeminiClassifier(settings)
    speech = speech if speech is not None else GradiumTTS(settings)
    stt_open = stt_open if stt_open is not None else GradiumSTT.open
    results: dict = {
        "status": "passed",
        "email": "not tested; doctor never sends email",
        "whatsapp": "not tested; doctor never sends WhatsApp messages",
    }
    samples = {
        "family_only": "Hi, this is your daughter. Shall we have lunch on Sunday?",
        "scam_scenario": "Mum, it is your daughter. I have been arrested and I am terrified. "
        "I need CHF 5,000 in cash within the hour. Do not tell Mum and Dad or anyone else. "
        "A courier will come to your house to collect the cash for my release.",
    }
    for name, sample in samples.items():
        try:
            result = await asyncio.wait_for(
                classifier.classify(sample, CallerLookup("not_provided")),
                settings.request_timeout_seconds,
            )
            results[name] = {
                "status": "passed",
                "overall": result.overall,
                "criteria": result.criteria.model_dump(),
                "warning_present": result.warning_text is not None,
            }
        except Exception as exc:
            results["status"] = "failed"
            results[name] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
    session = None
    try:
        import audioop

        audio = await speech.synthesize("This is a synthetic screening audio test.")
        results["tts"] = {"status": "passed", "pcm_bytes": len(audio)}
        session = await stt_open(settings)
        pcm, _ = audioop.ratecv(audio, 2, 1, 48000, 24000, None)

        async def feed():
            assert session is not None
            for offset in range(0, len(pcm), 3840):
                await session.send_audio(pcm[offset : offset + 3840])
                await asyncio.sleep(0.08)
            await session.finish()

        async def receive():
            assert session is not None
            segments = 0
            while (event := await session.receive()) is not None:
                if event.kind == "text" and event.text.strip():
                    segments += 1
            return segments

        tasks = [asyncio.create_task(feed()), asyncio.create_task(receive())]
        try:
            async with asyncio.timeout(30):
                _, segments = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        results["stt"] = {"status": "passed" if segments else "no transcript", "segments": segments}
        if not segments:
            results["status"] = "failed"
    except Exception as exc:
        results["status"] = "failed"
        results["gradium"] = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
    finally:
        if session is not None:
            try:
                await asyncio.wait_for(session.close(), 3)
            except Exception as exc:
                results["status"] = "failed"
                results["cleanup"] = type(exc).__name__
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Scam the Scammers local microphone listener")
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
            print("Screening configuration valid. This does not verify provider credentials.")
            print("Fallback email: " + settings.email_status() + ". No email sent.")
            print("Guardian WhatsApp: " + settings.whatsapp_status() + ". No WhatsApp sent.")
            if args.live:
                results = asyncio.run(check_providers(settings))
                print(json.dumps(results, indent=2))
                if results["status"] != "passed":
                    raise SystemExit(1)
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
