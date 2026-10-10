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

## Entire at the hackathon

Entire records development sessions and reviews changes. It is developer tooling,
separate from the voice agent and its Tools & activity panel. The repository ships
Codex capture hooks, shared Graph/Brain guidance, and six Trail runners for
confidence, risk, security, drift, review and a change summary. The runner prompts
focus on our audio pipeline, privacy, session cleanup and demonstrated validation.
Runner metrics are not the hackathon jury score.

Follow the [event guide](https://hack.entire.io/agentic-ai-hack-zurich-2026/)
and [Entire quickstart](https://docs.entire.io/quickstart). Each teammate needs to:

1. Install the Entire CLI using its official installer and run `entire login`.
2. Connect their GitHub account in Entire. The repository owner/admin must give
   the Entire GitHub App access to `simonthor/agentic-hack-zurich-2026` and import
   that repository. Existing GitHub collaborator permissions determine access.
3. Install local hooks from this checkout:

   ```bash
   entire enable --agent codex --yes --telemetry=false --absolute-git-hook-path
   entire doctor
   ```

   Start Codex from the repository root, open `/hooks`, review and trust the
   seven Entire hooks, then start a fresh session. Installed hooks alone do not
   prove capture is running; verify a real checkpoint after making a change.
4. Ask the Entire team for Trails access using the event's access form. In the
   repository's Entire **Settings → General**, enable **Runners**, **Auto build
   on push**, and **Trails**. These switches require repository admin access.
   The checked-in `.entire/runners/` files are already tailored; running
   `entire runner setup -y` again can overwrite their prompts.
5. Create a Trail on the working branch, commit a focused change, link the
   captured session when prompted, and push. Verify the checkpoint and inspect
   the actual Runner results in Entire:

   ```bash
   entire trail create
   # Make and review a real change, then git add / git commit / git push.
   entire checkpoint list
   entire checkpoint explain HEAD
   entire doctor
   ```

   CLI login and committed runner files are not enough: the repository must be
   connected, Trails enabled, and a pushed Trail evaluated before calling the
   online setup complete. If no checkpoint exists, check hook trust and start a
   fresh supported Codex session. Do not fabricate a session or test result.

For local code discovery and retained project decisions, install Graph with
`entire plugin install graph` and Brain using the official installer linked in
the event guide. From a clean, committed checkout:

```bash
entire graph init-agents --repo .
entire brain setup --agent none --no-backfill --no-daemon
entire brain init-agents
entire brain brief "Understand the local listener and its constraints" --json
entire graph search --repo . --profile full --query "microphone session cleanup"
```

Brain is initialized without background model jobs or a machine-wide daemon.
Refresh it with `entire brain refresh --agent none` after new checkpoints or
important commits. Record lasting decisions with `entire brain remember` and an
explicit `--path`. Re-run `entire brain setup` after the generated instruction
files are committed if it reports a dirty-worktree indexing error.

Checkpoint metadata is pushed alongside development work. Built-in secret
redaction remains enabled, with email and phone-number redaction added. Keep
credentials and real call recordings out of development transcripts and screenshots;
image contents are not redacted. Historical imports are labeled read-only history,
not evidence that live hooks captured a session.

For the demo, show the listener, a real captured checkpoint, its passing validation,
and the Trail's actual findings. Demonstrate a useful Graph lookup or Brain recall.
Report measured results and clearly distinguish synthetic audio checks from a real
phone-on-speaker test.
