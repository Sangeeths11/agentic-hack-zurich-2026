# Scam the Scamers

A local voice agent that listens beside your phone and responds through your
laptop. Put your phone on speaker, place your laptop beside it, and enable the listener.

## Getting started

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).
Setup installs Python 3.12. Gradbot 0.1.9 natively supports
macOS on Apple Silicon and Linux x86_64.

```bash
scripts/setup.sh
# Add GRADIUM_API_KEY and LLM_API_KEY to .env
scripts/run_local.sh
```

Open **http://localhost:8082**, click **Enable listener**, and allow microphone
access. Your browser must support AudioWorklet. Keep the tab open and your
computer awake during the conversation.

## How it works

- Answer the call on your phone and turn on speakerphone.
- The animated circle shows the microphone level. After 180 ms above the
  adjustable threshold, the agent starts automatically. A short audio buffer
  preserves the start of your sentence while the connection opens.
- The agent introduces itself as an AI assistant, listens, and responds through
  the laptop speakers. You can interrupt its reply.
- **End conversation** returns the listener to waiting for the next conversation.
  This also happens automatically after 30 seconds of silence or the session limit.
- **Pause listener** ends the session, stops playback, and releases the microphone.

The workspace opens in **Split view** on a laptop: the listener on the left and
**Tools & activity** on the right. Switch to either panel on its own without
interrupting the microphone or conversation. On small screens, the listener is
shown first; use the view buttons to switch panels or show both vertically.

The activity panel shows the current microphone, agent and speaker states, plus
timestamped connection, reply, interruption, end and error events. It keeps the
latest 60 events in page memory; **Clear activity** clears this display without
ending the conversation. Reloading the page clears the history. No external tools
are currently configured; the tools section is a placeholder for future integrations.

The trigger detects sound levels, not phone call status. Ringing or music can
also activate it. Adjust the threshold and volume to suit your setup. The
requested echo cancellation depends on your browser and device; test with a second phone.

## Configuration

Only `GRADIUM_API_KEY` and `LLM_API_KEY` are required. `.env.example` lists
additional options: model provider, model, name, language, voice, and time limits.
Defaults: English, `gpt-4o-mini`, one session at a time, and a five-minute session limit.
Restart the server after changing configuration.

```bash
uv run scam-the-scamers serve          # Run the server on 127.0.0.1:8082
uv run scam-the-scamers doctor         # Check configuration only
uv run scam-the-scamers doctor --live  # Check live voice providers; uses API credits
```

While waiting, audio stays in the browser. During a session, Gradium and the
configured model provider process speech. The app saves no recordings or
transcripts. API keys stay on the server. Connection errors pause the listener
and display an error message.

## Repository

```text
src/scam_the_scamers/
  agent.py          Voice agent and Gradbot integration
  bridge.py         Audio streaming, interruptions, and session lifecycle
  config.py         Settings and provider credentials
  main.py           Local HTTP and WebSocket endpoints
  cli.py            Startup and diagnostics
  static/           Listener interface and browser audio
scripts/            Setup and local startup
tests/              Audio, protocol, and browser checks
```

## Verification

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
node --test tests/test_browser_audio.mjs  # Node 22.7+
uv build
```

Optional, with Chrome installed:

```bash
uv run --with playwright python tests/browser_listener.py
```

Tests use synthetic audio and require no API keys. The browser test covers
automatic activation, playback, interruptions, pausing, returning to listening
after silence, denied microphone access, live activity, and switching workspace
views while a conversation is active.
