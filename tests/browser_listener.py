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
from support import DemoFactory, FakeEmail, FakeWhatsApp, assessment, simulation_settings

from scam_the_scamers.main import create_app
from scam_the_scamers.profiles import EmergencyContact, PersonalProfile
from scam_the_scamers.providers import ProviderError, TranscriptEvent
from scam_the_scamers.risk import SpeakerAssignment

PERSONAL_TEXT = (
    "My name is Margaret. Emily is my daughter and emergency contact +41791234567. "
    "Noah is my son and emergency contact +41791234568. Tyler is my grandson in Bern. "
    '<img src=x onerror="alert(1)">'
)
PERSONAL_PROFILE = PersonalProfile(
    listener_name="Margaret",
    emergency_contacts=(
        EmergencyContact(name="Emily", relationship="daughter", phone="+41791234567"),
        EmergencyContact(name="Noah", relationship="son", phone="+41791234568"),
    ),
)


class BrowserProfileParser:
    """Only synthetic, deterministic settings extraction; never calls Gemini."""

    def __init__(self):
        self.calls = []
        self.slow_started = asyncio.Event()

    async def parse(self, text):
        self.calls.append(text)
        if text == "My name is Old. Slow profile.":
            self.slow_started.set()
            await asyncio.sleep(1.5)
            return PersonalProfile(listener_name="Old")
        if text == "Unparseable profile":
            raise ProviderError("gemini", "synthetic_failure")
        return PERSONAL_PROFILE


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
    settings = simulation_settings(max_session_seconds=60, callmebot_api_key="", guardian_phone="")
    factory = DemoFactory(settings, assessment(49))
    assert isinstance(factory.email, FakeEmail), "Browser tests must never send email"
    assert isinstance(factory.whatsapp, FakeWhatsApp), "Browser tests must never send WhatsApp"
    profile_parser = BrowserProfileParser()
    app = create_app(settings, factory, profile_parser=profile_parser)
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
                assert "WhatsApp disabled" in await page.locator("#whatsapp-status").inner_text()
                await page.locator("#guardian-phone").fill("wrong-number")
                assert await page.locator("#guardian-phone").get_attribute("aria-invalid") == "true"
                assert "Invalid phone" in await page.locator("#whatsapp-status").inner_text()
                assert (
                    "without WhatsApp until corrected"
                    not in await page.locator("#whatsapp-status").inner_text()
                )
                for phone in ("+41791234567", "079 123 45 67", "0041 79 123 45 67"):
                    await page.locator("#guardian-phone").fill(phone)
                    assert (
                        await page.locator("#guardian-phone").get_attribute("aria-invalid")
                        == "false"
                    )
                    status = await page.locator("#whatsapp-status").inner_text()
                    assert "next screening session" in status
                    assert "Server WhatsApp configuration: not configured" in status
                    assert "API key" in status
                assert not factory.whatsapp.calls, (
                    "Editing guardian settings must not send messages"
                )
                await page.locator("#guardian-phone").fill("")
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
                assert (
                    not factory.sessions and not factory.email.calls and not factory.whatsapp.calls
                )
                await page.locator("#caller-number").fill("")
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "unavailable"
                await page.screenshot(path=str(artifacts / "listener-desktop.png"), full_page=True)
                factory.classifier.delay = 0.4
                await page.get_by_role("button", name="Enable listener").click()
                assert await page.locator("#guardian-phone").is_disabled()
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
                assert await page.locator("#guardian-phone").is_enabled()
                assert not factory.whatsapp.calls
                print(
                    "Browser: trigger, risk scores/colors, low-risk no TTS, warning playback, "
                    "feedback suppression, cancellation, end/rearm and pause passed",
                    flush=True,
                )

                factory.classifier.result = assessment(20)
                # Only injected fake providers exercise accepted messages, never real sends.
                settings.resend_api_key = SecretStr("offline")
                settings.alert_from_email = SecretStr("alerts@example.org")
                settings.fallback_email = SecretStr("guardian@example.org")
                settings.callmebot_api_key = SecretStr("offline")
                settings.guardian_phone = SecretStr("+41791234567")
                settings.callmebot_recipient_keys = SecretStr(
                    json.dumps({"+41791234568": "offline"})
                )
                factory.whatsapp.delay = 0.8
                await page.reload()
                await wait_browser(page, "!document.querySelector('#toggle').disabled")
                await page.get_by_role("button", name="Listener", exact=True).click()
                assert await page.locator("#guardian-phone").input_value() == "+41791234567"
                assert "WhatsApp configured" in await page.locator("#whatsapp-status").inner_text()
                await page.locator("#guardian-phone").fill("+41791234568")
                assert not factory.whatsapp.calls
                await page.locator("#caller-number").fill("+12013759200")
                # Restart and verify the automatic idle return, with a real worklet clock.
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#node-callmebot').dataset.active === 'true'"
                )
                assert await page.locator("#tool-callmebot").inner_text() == "sending"
                assert (
                    await page.locator(".flow-lines [data-to='callmebot']").get_attribute(
                        "data-active"
                    )
                    == "true"
                )
                await wait_browser(
                    page,
                    "document.querySelector('#guardian-note').textContent"
                    ".includes('Email (Resend)')",
                )
                assert "WhatsApp" not in await page.locator("#guardian-note").inner_text()
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '20 / 100'"
                )
                await wait_browser(
                    page,
                    "document.querySelector('#guardian-note').textContent"
                    ".includes('WhatsApp (CallMeBot)')",
                )
                await wait_browser(page, "!document.querySelector('#guardian-banner').hidden")
                assert await page.locator("#risk-panel").get_attribute("data-risk") == "red"
                banner = await page.locator("#guardian-message").inner_text()
                assert banner.startswith("Guardian alert accepted at ") and banner.endswith(
                    " — High"
                )
                assert len(factory.email.calls) == 1
                assert len(factory.whatsapp.calls) == 1
                assert factory.whatsapp.calls[0][0] == "+41791234568"
                assert "Email (Resend): High" in await page.locator("#guardian-note").inner_text()
                assert (
                    "WhatsApp (CallMeBot): High"
                    in await page.locator("#guardian-note").inner_text()
                )
                assert await page.locator("#node-callmebot").get_attribute("data-active") == "false"
                assert await page.locator("#tool-callmebot").inner_text() == "accepted"
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
                factory.whatsapp.delay = 0
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
                settings.resend_api_key = SecretStr("")
                factory.whatsapp.failures = [ProviderError("callmebot", "credentials_rejected")]
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
                await wait_browser(
                    page, "document.querySelector('#tool-callmebot').textContent === 'error'"
                )
                assert await page.locator("#node-callmebot").get_attribute("data-active") == "false"
                assert not await page.locator("#guardian-banner").is_visible()
                await page.get_by_role("button", name="Pause listener").click()
                await asyncio.sleep(0.1)
                assert factory.stts[-1].closed
                factory.classifier.error = None

                # An ambiguous provider response must never look like confirmed acceptance.
                factory.whatsapp.failures = [ProviderError("callmebot", "acceptance_unconfirmed")]
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#tool-callmebot').textContent === 'unknown'"
                )
                assert await page.locator("#node-callmebot").get_attribute("data-active") == "false"
                assert not await page.locator("#guardian-banner").is_visible()
                await page.get_by_role("button", name="Pause listener").click()

                # Pausing must clear a real in-flight indicator without claiming delivery.
                factory.whatsapp.delay = 10
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#tool-callmebot').textContent === 'sending'"
                )
                await page.get_by_role("button", name="Pause listener").click()
                assert await page.locator("#node-callmebot").get_attribute("data-active") == "false"
                assert (
                    await page.locator("#tool-callmebot").get_attribute("data-status") == "unknown"
                )
                assert "delivery unknown" in await page.locator("#tool-callmebot").inner_text()
                assert not await page.locator("#guardian-banner").is_visible()
                factory.whatsapp.delay = 0
                print(
                    "Browser: guardian phone, no sends on edits, per-channel acceptance, "
                    "WhatsApp sending/accepted/error/unknown/stopped and no false banner passed",
                    flush=True,
                )

                # Profile parsing uses the injected fake, including stale and error responses.
                await page.set_viewport_size({"width": 1280, "height": 1080})
                await page.locator("#caller-number").fill("")
                before_alerts = len(factory.whatsapp.calls)
                await page.locator("#personal-details").fill("My name is Old. Slow profile.")
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').dataset.status === 'parsing'",
                )
                await asyncio.wait_for(profile_parser.slow_started.wait(), 3)
                await page.locator("#personal-details").fill(PERSONAL_TEXT)
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').dataset.status === 'ready'",
                )
                assert (
                    await page.locator("#profile-listener-name").inner_text()
                    == "Listening for Margaret"
                )
                assert await page.locator("#emergency-contacts .emergency-contact").count() == 2
                assert (
                    "Emergency contact 1: +41791234567"
                    in await page.locator("#emergency-contacts").inner_text()
                )
                assert "Emily · daughter" in await page.locator("#emergency-contacts").inner_text()
                assert "Noah · son" in await page.locator("#emergency-contacts").inner_text()
                await page.locator("#profile-context-details summary").click()
                assert await page.locator("#profile-context img").count() == 0
                assert "<img src=x" in await page.locator("#profile-context").inner_text()
                await page.locator("#profile-context-details summary").click()
                assert not await page.locator("#guardian-fallback").is_visible()
                assert (
                    "2 emergency contacts"
                    in await page.locator("#guardian-override-status").inner_text()
                )
                await page.wait_for_timeout(1600)
                assert "Margaret" in await page.locator("#profile-listener-name").inner_text()
                assert len(factory.whatsapp.calls) == before_alerts

                await page.locator("#personal-details").fill("Unparseable profile")
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').dataset.status === 'error'",
                )
                assert (
                    "last accepted settings remain active"
                    in await page.locator("#profile-save-status").inner_text()
                )
                assert await page.locator("#emergency-contacts .emergency-contact").count() == 2
                assert await page.locator("#profile-retry").is_visible()
                await page.locator("#personal-details").fill(PERSONAL_TEXT)
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').dataset.status === 'ready'",
                )
                factory.classifier.result = assessment(20).model_copy(
                    update={"speaker_assignments": [SpeakerAssignment(turn_id=1, role="caller")]}
                )
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page,
                    "document.querySelector('#transcript-text').textContent"
                    ".includes('Caller (inferred)')",
                )
                assert await page.locator("#transcript-turn-1").get_attribute("data-turn-id") == "1"
                await page.evaluate("testAudio.gain.gain.value = 0")
                assert await page.locator("#personal-details").is_enabled()
                for control, value in (
                    ("warning-threshold", "95"),
                    ("whatsapp-threshold", "95"),
                    ("whatsapp-cooldown-seconds", "120"),
                ):
                    await page.locator(f"#{control}").evaluate(
                        "(input, value) => { input.value = value; "
                        "input.dispatchEvent(new Event('input', { bubbles: true })); }",
                        value,
                    )
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').textContent"
                    ".includes('applied to this live')",
                )
                assert len(factory.whatsapp.calls) == before_alerts
                assert await page.locator("#warning-threshold-value").inner_text() == "95 / 100"
                assert await page.locator("#whatsapp-cooldown-seconds-value").inner_text() == "120s"
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '20 / 100'"
                )
                # Delayed events from the previous profile cannot overwrite the live result.
                await factory.sessions[-1].notify(
                    {
                        "event": "assessment",
                        **assessment(100, is_scam=True).model_dump(),
                        "revision": 999,
                        "settings_revision": 0,
                        "context_truncated": False,
                    }
                )
                await factory.sessions[-1].notify(
                    {
                        "event": "transcript",
                        "revision": 999,
                        "settings_revision": 0,
                        "text": "Stale profile speech",
                        "turns": [{"id": 999, "text": "Stale profile speech", "role": "caller"}],
                    }
                )
                await factory.sessions[-1].notify(
                    {"event": "assessment_unavailable", "revision": 999, "settings_revision": 0}
                )
                await page.wait_for_timeout(150)
                assert await page.locator("#overall-score").inner_text() == "20 / 100"
                assert (
                    "Stale profile speech"
                    not in await page.locator("#transcript-text").inner_text()
                )
                speech_before = len(factory.speech.calls)
                factory.classifier.result = assessment(90, warning=True).model_copy(
                    update={
                        "speaker_assignments": [
                            SpeakerAssignment(turn_id=1, role="caller"),
                            SpeakerAssignment(turn_id=2, role="listener"),
                        ]
                    }
                )
                await factory.stts[-1].events.put(TranscriptEvent("text", "Is that really you?"))
                await factory.stts[-1].events.put(TranscriptEvent("boundary"))
                await wait_browser(
                    page, "document.querySelector('#overall-score').textContent === '90 / 100'"
                )
                await wait_browser(
                    page,
                    "document.querySelector('#transcript-text').textContent"
                    ".includes('You (inferred)')",
                )
                await page.wait_for_timeout(300)
                assert len(factory.speech.calls) == speech_before
                assert len(factory.whatsapp.calls) == before_alerts
                assert not await page.locator("#guardian-banner").is_visible()

                # Independent scam evidence remains eligible even at a high personal threshold.
                factory.classifier.result = assessment(10, warning=True, is_scam=True).model_copy(
                    update={
                        "speaker_assignments": [
                            SpeakerAssignment(turn_id=1, role="caller"),
                            SpeakerAssignment(turn_id=2, role="listener"),
                        ]
                    }
                )
                await factory.stts[-1].events.put(
                    TranscriptEvent("text", "A mixed speaker segment.")
                )
                await wait_browser(
                    page, "document.querySelector('#guardian-note').textContent.includes('Noah')"
                )
                assert "Emily" in await page.locator("#guardian-note").inner_text()
                assert "+41791234567" in await page.locator("#guardian-note").inner_text()
                assert "+41791234568" in await page.locator("#guardian-note").inner_text()
                assert len(factory.whatsapp.calls) == before_alerts + 2
                assert "Speaker unknown" in await page.locator("#transcript-text").inner_text()
                assert await page.locator("#transcript-text .transcript-turn").count() == 3
                assert (
                    await page.locator("#transcript-turn-1").get_attribute("data-role") == "caller"
                )
                assert (
                    await page.locator("#transcript-turn-2").get_attribute("data-role")
                    == "listener"
                )
                assert (
                    await page.locator("#transcript-turn-3").get_attribute("data-role") == "unknown"
                )
                await page.get_by_role("button", name="Split view", exact=True).click()
                await page.screenshot(
                    path=str(artifacts / "listener-personalized.png"), full_page=True
                )
                await page.get_by_role("button", name="Pause listener").click()
                await page.reload()
                await wait_browser(
                    page, "document.querySelector('#personal-details').value.includes('Margaret')"
                )
                assert await page.locator("#personal-details").input_value() == PERSONAL_TEXT
                assert await page.locator("#warning-threshold").input_value() == "95"
                assert await page.evaluate(
                    "localStorage.length === 0 && sessionStorage.length === 0"
                )
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.get_by_role("button", name="Listener", exact=True).click()
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.locator(".personal-settings").screenshot(
                    path=str(artifacts / "listener-personalized-mobile.png"), animations="disabled"
                )
                await page.locator("#personal-details").fill("")
                await wait_browser(
                    page,
                    "document.querySelector('#profile-save-status').dataset.status === 'ready'",
                )
                assert await page.locator("#emergency-contacts .emergency-contact").count() == 0
                assert (
                    "WhatsApp alerts are disabled"
                    in await page.locator("#guardian-override-status").inner_text()
                )
                assert not await page.locator("#guardian-fallback").is_visible()
                assert len(factory.whatsapp.calls) == before_alerts + 2
                print(
                    "Browser: automatic profile extraction/stale/error handling, emergency cards, "
                    "live settings/thresholds, two recipients, inferred turns, "
                    "memory-only reload passed",
                    flush=True,
                )

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
                            "whatsapp_status": "not_configured",
                            "guardian_phone": "",
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
