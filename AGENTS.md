# Scam the Scamers

Build the local laptop-microphone listener and voice agent. Keep the interface
and documentation in English. Sound level triggers the agent; the app cannot
detect phone-call acceptance. Never present activity indicators as external tool
execution when no tool is wired. The standalone scam database is separate from
the voice agent until explicitly integrated.

Keep API keys on the server and audio/transcripts out of application storage and
logs. Preserve same-origin WebSocket checks, bounded buffers, timeouts, and
microphone/provider cleanup when pausing or disconnecting.

Validate relevant changes with `uv run ruff check src tests`,
`uv run ruff format --check src tests`, `uv run mypy src`, `uv run pytest -q`,
`node --test tests/test_browser_audio.mjs`, and `uv build`. Browser/audio changes
also use `uv run --with playwright python tests/browser_listener.py` when Chrome
is installed. These checks use synthetic audio; report live provider/phone tests
separately. Do not invent scores, token savings, checkpoints or test evidence.

Use the Entire workflow in README.md. Keep generated runner schemas and output
contracts intact. Hook trust, GitHub app access and Trails enablement must be
verified, not assumed from installed files. Follow higher-priority tool guidance
when it differs from the generated guide below.

Before relying on Brain, check freshness. On a clean checkout, refresh after new
commits or checkpoints with `entire brain refresh --agent none --raw`. With
Entire CLI 0.11.4 and Brain 0.1.0, compact Codex transcripts yielded zero
searchable exchanges; raw export restored conversation retrieval. Preserve this
flag until a version change is verified. Read-only `brief` and `query` commands
do not keep the index fresh. No automatic Brain watcher is configured here.

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->
