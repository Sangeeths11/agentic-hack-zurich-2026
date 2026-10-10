# Scam the Scammers

Build the local laptop-microphone scam-screening pipeline. Keep the interface
and documentation in English. Sound level triggers the agent; the app cannot
detect phone-call acceptance. Never present activity indicators as external tool
execution when no tool is wired. The explicit live-screening request supersedes
the old listener-only scope: Gradium STT, Gemini six-criterion classification,
the preserved suspicious-number SQLite lookup, optional Gradium warning TTS and
Resend fallback email and CallMeBot guardian WhatsApp are integrated. No open-ended
voice replies or greetings.

Validate all six complete bounded model criteria; calculate the published additive
aggregate (sum capped at 100) deterministically in the backend. The latest UI work order
supersedes the earlier weighted rubric. Gemini scam judgments and matched numbers may
raise independent warnings without changing the calculated score. Never rely on model arithmetic or reject
valid criteria for a model-calculated sum. Label the calculated total separately from
the six Gemini scores; these are not calibrated fraud probabilities. Score-only assessments must never call TTS.
Email tiers are 50/75/90, with session-local idempotency and bounded retry.
WhatsApp uses configurable score eligibility, the same severity tiers and independent
flags, with at most one attempt per tier/contact/session plus bounded burst coalescing
and a configurable escalation gap. CallMeBot has no documented idempotency, so ambiguous
results must not retry automatically. Personal settings are bounded server-memory
data, parsed into fixed templates and applied live by revision. Contact destinations
must occur in the supplied settings; each server key must match its activated recipient.
Use profile context for family inconsistencies, concise attributed conversation summaries,
and fixed personalized warning templates. Never invent voice matching, amounts, identities,
listen-in links or warning playback in alert messages. Provider queue acceptance is
not verified delivery. Missing messaging configuration stays visible without blocking
screening. Development tests must never send real email or WhatsApp without separate
explicit approval; live audio harnesses must disable both messaging providers.
Keep caller speech untrusted, context bounded and warning playback suppressed
from STT. Missing or failed number lookup never means known-safe.
Display original transcript segments with stable IDs; inferred speaker roles must be
labeled as inferred, unclear roles stay unknown, and Gradium stream IDs are not speakers.
Preserve bounded context, reject stale settings/assessment side effects and cancel
removed contacts' unsent work before potentially blocking event publication.

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
