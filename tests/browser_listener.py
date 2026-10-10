"""Real Chromium/Web Audio integration with synthetic microphone, no cloud or recording.

Run: uv run --with playwright python tests/browser_listener.py
Uses installed Chrome (no browser download). Screenshots go to artifacts/.
"""

import asyncio
import json
import socket
from pathlib import Path

import uvicorn
from playwright.async_api import async_playwright
from pydantic import SecretStr
from support import DemoFactory, assessment, simulation_settings

from scam_the_scamers.main import create_app
from scam_the_scamers.providers import ProviderError, TranscriptEvent

SYNTHETIC_MICROPHONE = """
(() => {
  window.testAudio = { contexts: [], starts: 0, stops: 0 };
  const NativeContext = window.AudioContext;
  window.AudioContext = class extends NativeContext {
    constructor(options) { super(options); window.testAudio.contexts.push(this); }
  };
  const nativeStart = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function(...args) {
    window.testAudio.starts++;
    return nativeStart.apply(this, args);
  };
  const nativeStop = AudioBufferSourceNode.prototype.stop;
  AudioBufferSourceNode.prototype.stop = function(...args) {
    window.testAudio.stops++;
    return nativeStop.apply(this, args);
  };
  navigator.mediaDevices.getUserMedia = async () => {
    const context = new NativeContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain(); gain.gain.value = 0;
    const output = context.createMediaStreamDestination();
    oscillator.frequency.value = 440;
    oscillator.connect(gain); gain.connect(output); oscillator.start();
    await context.resume();
    window.testAudio.gain = gain;
    window.testAudio.stream = output.stream;
    window.testAudio.syntheticContext = context;
    return output.stream;
  };
})();
"""


async def wait_browser(page, expression, deadline_ms=5000):
    # Evaluate through CDP instead of wait_for_function's in-page eval, so the
    # test keeps the app's strict CSP enabled.
    async with asyncio.timeout(deadline_ms / 1000):
        while True:
            if await page.evaluate(expression):
                return
            await asyncio.sleep(0.05)


async def main():
    settings = simulation_settings(max_session_seconds=60)
    factory = DemoFactory(settings, assessment(49))
    app = create_app(settings, factory)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    artifacts = Path("artifacts")
    await asyncio.to_thread(artifacts.mkdir, exist_ok=True)
    errors = []
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 1080})
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.add_init_script(SYNTHETIC_MICROPHONE)
                await page.goto(f"http://127.0.0.1:{port}")
                await wait_browser(page, "!document.querySelector('#toggle').disabled")
                assert await page.locator("#listener-panel").is_visible()
                assert await page.locator("#tools-panel").is_visible()
                assert "not configured" in await page.locator("#fallback-status").inner_text()
                assert await page.locator("#overall-score").inner_text() == "—"
                await page.locator("#caller-number").fill("+12013759200")
                await page.get_by_role("button", name="Check number", exact=True).click()
                await wait_browser(
                    page, "document.querySelector('#tool-db').textContent === 'matched'"
                )
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "red"
                assert "Listed number" in await page.locator("#risk-warning").inner_text()
                assert "Listed number" in await page.locator("#caller-check-status").inner_text()
                assert (
                    "shalom_karr_ai_blocklist" in await page.locator("#caller-result").inner_text()
                )
                assert await page.locator("#overall-score").inner_text() == "—"
                assert not factory.sessions and not factory.email.calls
                await page.locator("#caller-number").fill("")
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "unavailable"
                await page.screenshot(path=str(artifacts / "listener-desktop.png"), full_page=True)
                factory.classifier.delay = 0.4
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.wait_for_timeout(500)
                assert not factory.sessions, "Silence must never open a provider session"
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#node-gemini').dataset.active === 'true'"
                )
                assert await page.locator("#node-stt").get_attribute("data-active") == "true"
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '49 / 100'"
                )
                await page.evaluate("testAudio.gain.gain.value = 0")
                assert await page.evaluate("testAudio.starts === 0"), "Low risk must not play audio"
                assert not factory.speech.calls and not factory.email.calls
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "green"
                assert await page.locator("#tool-db").inner_text() == "not provided"
                assert await page.locator("#node-gemini").get_attribute("data-active") == "false"
                assert await page.locator("#tool-scoring").inner_text() == "complete"
                assert (
                    "Synthetic family emergency"
                    in await page.locator("#transcript-text").inner_text()
                )
                assert await page.locator("#criteria-scores .criterion").count() == 6
                factory.classifier.delay = 0

                # A low additive total must not hide Gemini's independent scam judgment.
                factory.classifier.result = assessment(10, is_scam=True)
                await factory.stts[0].events.put(
                    TranscriptEvent("text", '<img src=x onerror="alert(1)">')
                )
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '10 / 100'"
                )
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "red"
                assert "Gemini flagged" in await page.locator("#risk-warning").inner_text()
                assert "<img src=x" in await page.locator("#transcript-text").inner_text()
                assert await page.locator("#transcript-text img").count() == 0
                assert not factory.speech.calls, "Score-only assessments must not request TTS"
                assert not await page.locator("#guardian-banner").is_visible()

                # Exercise exact displayed thresholds through the real backend pipeline.
                for score, color in [(50, "amber"), (75, "red"), (90, "critical")]:
                    factory.classifier.result = assessment(score, warning=(score == 90))
                    await factory.stts[0].events.put(
                        TranscriptEvent("text", "Synthetic escalation.")
                    )
                    await wait_browser(
                        page,
                        f"document.querySelector('#overall-score').textContent === '{score} / 100'",
                    )
                    assert await page.locator("#risk-panel").get_attribute("data-risk") == color
                    assert (
                        await page.locator("#criterion-secrecy").get_attribute("data-risk")
                        == "green"
                    )
                await wait_browser(page, "testAudio.starts > 0")
                before_calls = len(factory.classifier.calls)
                before_frames = len(factory.stts[0].received)
                # Simulate speaker leakage with a loud synthetic mic during warning playback.
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await page.wait_for_timeout(180)
                guarded_frames = factory.stts[0].received[before_frames:]
                assert guarded_frames and all(not any(frame) for frame in guarded_frames)
                assert len(factory.classifier.calls) == before_calls, (
                    "Playback must not trigger another assessment"
                )
                await page.get_by_role("button", name="Stop warning audio").click()
                await wait_browser(page, "testAudio.stops > 0")
                await page.wait_for_timeout(700)
                assert any(factory.stts[0].received[-1]), (
                    "Microphone must resume after bounded guard"
                )
                await page.evaluate("testAudio.gain.gain.value = 0")
                assert len(factory.sessions) == 1
                assert len(factory.speech.calls) == 1 and not factory.email.calls
                activity = await page.locator("#activity-log").inner_text()
                for title in (
                    "Listener enabled",
                    "Sound detected",
                    "Screening connected",
                    "Gemini",
                    "Gradium STT",
                    "Gradium TTS",
                    "Resend email",
                    "Warning playing",
                ):
                    assert title in activity, activity
                assert "delivered" not in activity.lower()
                assert await page.locator("#microphone-status").inner_text() == "Listening"
                await page.screenshot(path=str(artifacts / "listener-active.png"), full_page=True)
                # Changing the workspace view must leave the audio session untouched.
                await page.get_by_role("button", name="Tools & activity", exact=True).click()
                assert not await page.locator("#listener-panel").is_visible()
                assert await page.locator("#tools-panel").is_visible()
                assert await page.evaluate(
                    "testAudio.stream.getTracks().every(t => t.readyState === 'live')"
                )
                assert len(factory.sessions) == 1 and not factory.sessions[0].closed
                await page.get_by_role("button", name="Clear activity", exact=True).click()
                assert await page.locator("#activity-log li").count() == 0
                assert await page.locator("#activity-status").inner_text() == "Live"
                await page.get_by_role("button", name="Listener", exact=True).click()
                assert not await page.locator("#tools-panel").is_visible()
                await page.get_by_role("button", name="End screening").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                assert await page.locator("#transcript-text").inner_text() == ""
                assert await page.locator("#node-stt").get_attribute("data-active") == "false"
                assert "Ended by you" in await page.locator("#activity-log").inner_text()
                await page.get_by_role("button", name="Pause listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'off'")
                assert await page.locator("#microphone-status").inner_text() == "Off"
                assert await page.locator("#activity-status").inner_text() == "Standby"
                assert await page.evaluate(
                    "testAudio.stream.getTracks().every(t => t.readyState === 'ended')"
                )
                assert await page.evaluate("testAudio.contexts.every(c => c.state === 'closed')")
                await asyncio.sleep(0.1)
                assert not app.state.registry.sockets
                assert factory.sessions[0].closed
                print(
                    "Browser: trigger, risk scores/colors, low-risk no TTS, warning playback, "
                    "feedback suppression, cancellation, end/rearm and pause passed",
                    flush=True,
                )

                factory.classifier.result = assessment(20)
                # A fake accepted provider response, never a real email, exercises the banner.
                settings.resend_api_key = SecretStr("offline")
                settings.alert_from_email = SecretStr("alerts@example.org")
                settings.fallback_email = SecretStr("guardian@example.org")
                await page.reload()
                await wait_browser(page, "!document.querySelector('#toggle').disabled")
                await page.get_by_role("button", name="Listener", exact=True).click()
                await page.locator("#caller-number").fill("+12013759200")
                # Restart and verify the automatic idle return, with a real worklet clock.
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '20 / 100'"
                )
                await wait_browser(page, "!document.querySelector('#guardian-banner').hidden")
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "red"
                banner = await page.locator("#guardian-message").inner_text()
                assert banner.startswith("Guardian alerted at ") and banner.endswith(" — High")
                assert len(factory.email.calls) == 1
                assert "unverified" in await page.locator("#guardian-banner").inner_text()
                assert await page.locator("#guardian-banner").is_visible()
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.locator("#guardian-banner").is_visible()
                assert await page.locator("#caller-check-status").is_visible()
                await page.screenshot(
                    path=str(artifacts / "listener-guardian-mobile.png"), full_page=True
                )
                await page.get_by_role("button", name="Tools & activity", exact=True).click()
                assert await page.locator("#guardian-banner").is_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(
                    path=str(artifacts / "listener-flow-mobile.png"), full_page=True
                )
                await page.get_by_role("button", name="Listener", exact=True).click()
                await page.set_viewport_size({"width": 1280, "height": 1080})
                await factory.stts[-1].events.put(TranscriptEvent("text", " synthetic" * 2000))
                await wait_browser(
                    page,
                    "document.querySelector('#transcript-note').textContent"
                    ".includes('Context limit reached')",
                )
                assert len(await page.locator("#transcript-text").inner_text()) <= 24000
                await page.evaluate("testAudio.gain.gain.value = 0")
                await wait_browser(
                    page,
                    "document.querySelector('#orb').dataset.state === 'armed'",
                    deadline_ms=35000,
                )
                assert len(factory.sessions) == 2 and factory.sessions[1].closed
                assert "30 seconds of quiet" in await page.locator("#activity-log").inner_text()
                await page.get_by_role("button", name="Pause listener").click()
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(path=str(artifacts / "listener-mobile.png"), full_page=True)
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.get_by_role("button", name="Tools & activity", exact=True).click()
                assert await page.locator("#tools-panel").is_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.screenshot(
                    path=str(artifacts / "listener-tools-mobile.png"), full_page=True
                )
                await page.get_by_role("button", name="Split view", exact=True).click()
                assert await page.locator("#listener-panel").is_visible()
                assert await page.locator("#tools-panel").is_visible()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                print(
                    "Browser: live activity, view switching, silence rearm "
                    "and mobile layout passed",
                    flush=True,
                )

                # An actual fake-provider error is visible; no fictional score survives.
                await page.get_by_role("button", name="Listener", exact=True).click()
                factory.classifier.error = ProviderError("gemini", "synthetic failure")
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page,
                    "document.querySelector('#assessment-status').textContent.includes('unavailable')",
                )
                assert await page.locator("#overall-score").inner_text() == "—"
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "red"
                assert "Listed number" in await page.locator("#risk-warning").inner_text()
                assert await page.locator("#tool-gemini").inner_text() == "error"
                await page.get_by_role("button", name="Pause listener").click()
                await asyncio.sleep(0.1)
                assert factory.stts[-1].closed
                factory.classifier.error = None

                # Denied access must be actionable and must not create a provider session.
                await page.evaluate(
                    "() => { navigator.mediaDevices.getUserMedia = async () => { "
                    "throw new DOMException('denied', 'NotAllowedError'); }; }"
                )
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'error'")
                assert "Microphone access was denied" in await page.locator("#error").inner_text()
                assert (
                    "Microphone access was denied"
                    in await page.locator("#activity-log").inner_text()
                )
                assert await page.locator("#activity-status").inner_text() == "Needs attention"
                await page.reload()
                await wait_browser(page, "!document.querySelector('#toggle').disabled")
                assert await page.locator("#listener-panel").is_visible()
                assert not await page.locator("#tools-panel").is_visible()
                assert await page.locator("#activity-log li").count() == 0
                assert not app.state.registry.sockets
                # Missing readiness must disable start before asking for microphone access.
                await page.route(
                    "**/api/config",
                    lambda route: route.fulfill(
                        json={
                            "configured": False,
                            "missing": ["GRADIUM_API_KEY"],
                            "email_status": "not_configured",
                            "fallback_email": "",
                            "caller_default_region": "CH",
                            "max_session_seconds": 60,
                        }
                    ),
                )
                await page.reload()
                await wait_browser(
                    page, "document.querySelector('#error').textContent.includes('Finish setup')"
                )
                assert await page.locator("#toggle").is_disabled()
                # Local lookup is useful even before provider keys are configured.
                await page.locator("#caller-number").fill("+12013759200")
                await page.get_by_role("button", name="Check number", exact=True).click()
                await wait_browser(
                    page,
                    "document.querySelector('#caller-check-status').dataset.matched === 'true'",
                )
                assert await page.locator("#caller-check-status").is_visible()
                assert not errors, errors
                print(
                    json.dumps(
                        {
                            "browser": "passed",
                            "sessions": len(factory.sessions),
                            "page_errors": errors,
                        }
                    )
                )
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await task
        sock.close()


if __name__ == "__main__":
    asyncio.run(main())
