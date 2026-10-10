"""Render arbitrary text to speech outside the normal conversational turn.

Calls Gradium's TTS endpoint directly (the same provider gradbot already
uses for the agent's own replies), for text the *backend* decides must be
spoken right now — a risk warning — rather than text gradbot's LLM chose to
say in response to the caller. Confirmed live: Gradium's "pcm" output
format is already 48 kHz mono int16, matching GRADBOT_OUTPUT_RATE exactly,
so no resampling is needed before sending it to the browser.
"""

import logging

import gradium

from .config import Settings

log = logging.getLogger(__name__)


async def render_pcm16(settings: Settings, text: str) -> bytes | None:
    """Synthesize text to 48 kHz mono PCM16 bytes. Returns None on any
    failure (missing key, network error, empty text) — a TTS hiccup must
    never crash the live session, it just means the warning doesn't play.
    """
    text = (text or "").strip()
    if not text:
        return None
    api_key = settings.value("gradium_api_key")
    if not api_key:
        log.warning("warning_tts_skipped reason=no_gradium_key")
        return None
    try:
        client = (
            gradium.GradiumClient(api_key=api_key, base_url=settings.gradium_base_url)
            if settings.gradium_base_url
            else gradium.GradiumClient(api_key=api_key)
        )
        setup = gradium.TTSSetup(
            voice_id=settings.gradium_voice_id or None,
            output_format="pcm",
        )
        result = await client.tts(setup, text)
        if result.output_format != "pcm" or result.sample_rate != 48_000:
            log.warning(
                "warning_tts_unexpected_format format=%s rate=%s",
                result.output_format,
                result.sample_rate,
            )
            return None
        return result.pcm16().tobytes()
    except Exception as exc:  # noqa: BLE001 — a flaky TTS call must not crash the session.
        log.warning("warning_tts_failed type=%s", type(exc).__name__)
        return None
