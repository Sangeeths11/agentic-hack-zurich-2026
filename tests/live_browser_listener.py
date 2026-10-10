"""Opt-in live provider/browser check; uses API credits, never real mic or guardian messages.

Run: uv run --with playwright python tests/live_browser_listener.py --live
Adapted from the parent's independent review harness. Chrome output is muted;
this verifies scheduled playback, not real speakerphone acoustic performance.
"""

import argparse
import asyncio
import base64
import json
import socket
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright, expect
from pydantic import SecretStr

from scam_the_scamers.config import Settings
from scam_the_scamers.main import create_app
from scam_the_scamers.providers import GradiumTTS
from scam_the_scamers.risk import Scores


async def main():
    settings = Settings().model_copy(
        update={
            "resend_api_key": SecretStr(""),
            "fallback_email": SecretStr(""),
            "alert_from_email": SecretStr(""),
            "callmebot_api_key": SecretStr(""),
            "guardian_phone": SecretStr(""),
            "max_session_seconds": 90,
        }
    )
    sentence = (
        "I'm your daughter. I've been arrested. Bring five thousand francs within an hour. "
        "Tell nobody. A courier will collect the cash."
    )
    audio = await GradiumTTS(settings).synthesize(sentence)
    encoded = base64.b64encode(audio).decode()
    print(
        json.dumps({"source": "synthetic Gradium speech", "duration_s": len(audio) / 96000}),
        flush=True,
    )
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while True:
                if server.started:
                    break
                await asyncio.sleep(0.02)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel="chrome", headless=True, args=["--mute-audio"]
            )
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                events = []

                def received(payload):
                    if not isinstance(payload, str):
                        return
                    try:
                        event = json.loads(payload)
                        if event.get("event") in {
                            "assessment",
                            "assessment_unavailable",
                            "warning",
                        }:
                            events.append(event)
                    except ValueError:
                        pass

                page.on("websocket", lambda ws: ws.on("framereceived", received))
                await page.add_init_script(
                    """(encoded => {
                    const NativeContext = window.AudioContext;
                    window.reviewAudio = { playbackStarts: 0 };
                    const originalStart = AudioBufferSourceNode.prototype.start;
                    AudioBufferSourceNode.prototype.start = function(...args) {
                        reviewAudio.playbackStarts++;
                        return originalStart.apply(this, args);
                    };
                    navigator.mediaDevices.getUserMedia = async () => {
                        const context = new NativeContext({sampleRate: 48000});
                        const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
                        const data = new DataView(bytes.buffer);
                        const buffer = context.createBuffer(1, bytes.length / 2, 48000);
                        const samples = buffer.getChannelData(0);
                        for (let i = 0; i < samples.length; i++) {
                            samples[i] = data.getInt16(i*2, true) / 32768;
                        }
                        const source = context.createBufferSource(); source.buffer = buffer;
                        const destination = context.createMediaStreamDestination();
                        source.connect(destination);
                        reviewAudio.stream = destination.stream;
                        reviewAudio.sourceContext = context;
                        reviewAudio.begin = async () => {
                            await context.resume();
                            originalStart.call(source);
                        };
                        await context.resume();
                        return destination.stream;
                    };
                })("""
                    + json.dumps(encoded)
                    + ");"
                )
                await page.goto(f"http://127.0.0.1:{port}")
                await page.get_by_role("button", name="Enable listener").click()
                await expect(page.locator("#orb")).to_have_attribute(
                    "data-state", "armed", timeout=8000
                )
                await page.evaluate("reviewAudio.begin()")
                await expect(page.locator("#orb")).not_to_have_attribute(
                    "data-state", "armed", timeout=8000
                )
                async with asyncio.timeout(55):
                    while not await page.evaluate("reviewAudio.playbackStarts > 0"):
                        if await page.locator("#orb").get_attribute("data-state") == "error":
                            raise RuntimeError(await page.locator("#error").inner_text())
                        if await page.locator("#orb").get_attribute("data-state") == "armed":
                            raise RuntimeError(
                                "Screening ended before warning playback; last score: "
                                + await page.locator("#overall-score").inner_text()
                            )
                        await asyncio.sleep(0.1)
                overall_display = await page.locator("#overall-score").inner_text()
                overall = float(overall_display.split(" / ")[0])
                risk = await page.locator("#risk-panel").get_attribute("data-risk")
                criteria = await page.locator("#criteria-scores").inner_text()
                assert risk in {"amber", "red", "critical"}, (overall, risk)
                assessments = [e for e in events if e["event"] == "assessment"]
                assert assessments, "No valid assessment reached the browser"
                for event in assessments:
                    assert event["overall"] == Scores.model_validate(event["criteria"]).aggregate()
                assert not any(e["event"] == "assessment_unavailable" for e in events), (
                    "Partial assessment failed"
                )
                assert float(overall) == assessments[-1]["overall"]
                assert float(overall) >= settings.warning_threshold or assessments[-1]["is_scam"]
                assert all(0 <= value <= 100 for value in assessments[-1]["criteria"].values())
                assert await page.get_by_text("CALCULATED ADDITIVE TOTAL", exact=True).is_visible()
                assert await page.get_by_text(
                    "All six Gemini evidence scores", exact=True
                ).is_visible()
                assert await page.locator("#warning-text").is_visible()
                assert "not configured" in await page.locator("#tool-resend").inner_text()
                assert "not configured" in await page.locator("#tool-callmebot").inner_text()
                await asyncio.to_thread(Path("artifacts").mkdir, exist_ok=True)
                await page.screenshot(path="artifacts/listener-live-provider.png", full_page=True)
                await page.get_by_role("button", name="Pause listener").click()
                await expect(page.locator("#orb")).to_have_attribute(
                    "data-state", "off", timeout=5000
                )
                assert await page.evaluate(
                    "reviewAudio.stream.getTracks().every(t => t.readyState === 'ended')"
                )
                await page.evaluate("reviewAudio.sourceContext.close()")
                async with asyncio.timeout(5):
                    while True:
                        if not app.state.registry.sockets:
                            break
                        await asyncio.sleep(0.05)
                assert not errors, errors
                print(
                    json.dumps(
                        {
                            "real_provider_browser_flow": "passed",
                            "overall": overall,
                            "risk": risk,
                            "criteria": criteria,
                            "warning_playback": "started in muted browser",
                            "email": "disabled; none sent",
                            "whatsapp": "disabled; none sent",
                            "pause_cleanup": "passed",
                            "page_errors": errors,
                            "valid_assessments": len(assessments),
                            "unavailable_assessments": 0,
                        }
                    ),
                    flush=True,
                )
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await server_task
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Authorize bounded Gradium/Gemini API usage"
    )
    if not parser.parse_args().live:
        parser.error("--live is required; uses real provider credits (never guardian messages)")
    asyncio.run(main())
