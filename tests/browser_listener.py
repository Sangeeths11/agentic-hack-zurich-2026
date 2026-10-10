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
from support import DemoFactory, simulation_settings

from scam_the_scamers.main import create_app

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
    factory = DemoFactory()
    app = create_app(simulation_settings(max_session_seconds=60), factory)
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
                assert await page.get_by_text("No tools connected yet", exact=True).is_visible()
                await page.screenshot(path=str(artifacts / "listener-desktop.png"), full_page=True)
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.wait_for_timeout(500)
                assert not factory.sessions, "Silence must never open a provider session"
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(page, "testAudio.starts > 0")
                await page.evaluate("testAudio.gain.gain.value = 0")
                await wait_browser(page, "testAudio.stops > 0")
                assert len(factory.sessions) == 1
                activity = await page.locator("#activity-log").inner_text()
                for title in (
                    "Listener enabled",
                    "Sound detected",
                    "Agent connected",
                    "Preparing a reply",
                    "Agent speaking",
                    "Reply interrupted",
                ):
                    assert title in activity, activity
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
                await page.get_by_role("button", name="End conversation").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                assert "Ended by you" in await page.locator("#activity-log").inner_text()
                await page.get_by_role("button", name="Pause listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'off'")
                assert await page.locator("#microphone-status").inner_text() == "Off"
                assert await page.locator("#activity-status").inner_text() == "Standby"
                assert await page.evaluate(
                    "testAudio.stream.getTracks().every(t => t.readyState === 'ended')"
                )
                assert await page.evaluate("testAudio.contexts.every(c => c.state === 'closed')")
                assert not app.state.registry.sockets
                assert factory.sessions[0].closed
                print(
                    "Browser: silence, auto-trigger, PCM playback, interruption, "
                    "end/rearm and pause passed",
                    flush=True,
                )

                # Restart and verify the automatic idle return, with a real worklet clock.
                await page.get_by_role("button", name="Enable listener").click()
                await wait_browser(page, "document.querySelector('#orb').dataset.state === 'armed'")
                await page.evaluate("testAudio.gain.gain.value = 0.12")
                await wait_browser(
                    page, "document.querySelector('#orb').dataset.state === 'speaking'"
                )
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
