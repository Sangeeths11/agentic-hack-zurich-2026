# Live scam screening work order

Accepted on 2026-10-10 in the existing Entire-captured Terminal Codex session.
The parent Cursor agent will independently review the result. Do not spawn agents.

## Product and provider scope

Replace the open-ended conversational agent with a local laptop microphone screening
pipeline: browser PCM -> backend -> Gradium streaming STT -> Gemini structured
classification -> live UI. Only optional eligible warning text goes to Gradium TTS
and browser playback. Score-only results must never synthesize speech. No greetings,
free replies, Twilio, telephony integration or unrelated contacts application.
Keep the exact brand **Scam the Scamers** and all UI, documentation, comments and
work-order text in English. Preserve the animated listener, sound activation,
responsive Listener / Tools & activity split and tabs, and phone-on-speaker workflow.
Microphones detect neither caller ID nor accepted phone calls.

Classify six criteria, each from 0 to 100: family-member claim; emotional distress
(e.g. arrested); urgency (within the hour); secrecy (do not tell Mum and Dad);
financial request (CHF 5,000 cash); courier pickup. Return all six scores and an
overall 0–100 risk indicator. Colors: below 50 green, 50 amber, 75 red, 90 critical.
An isolated family claim must not imply fraud. Document an aggregate and rubric,
with uncertainty, rather than representing model scores as calibrated probabilities.
Default spoken-warning threshold 50, independently configurable. High risk without
warning text still updates the UI and email tiers, without fabricating speech.

Use concrete, injectable adapters for Gradium STT/TTS, Gemini and Resend. Verify API
formats against official Gradium, Google Gemini and Resend documentation. Preserve
existing valid .env configuration: GRADIUM_API_KEY, LLM_API_KEY, Google's LLM_BASE_URL
and LLM_MODEL=gemini-3.5-flash-lite. Prefer an optional GEMINI_API_KEY alias; do not
judge keys by prefixes, print secrets, replace keys, send them to other hosts or
silently fall back to a different LLM. If Google rejects the key, report to parent
and continue fake-provider validation. Bound requests and avoid provider-managed
conversation state where controllable.

Treat speech as untrusted data. Strictly validate finite complete 0–100 scores,
short strings and malformed/blocked responses. Bound accumulated context and audio
queues; retain earlier evidence; debounce/coalesce text; prevent stale results from
replacing newer ones. Respect STT text/end_text/flush/EOS semantics, including short
last utterances. Silence and a lack of TTS are normal, not a first-audio failure.

Integrate and preserve the existing suspicious-number SQLite dataset, with tests,
without redownloading it. Offer optional caller-number input and documented Swiss
normalization/default country. Distinguish omitted, invalid, no match, matched and
unavailable lookup states; no match is not proof of safety. Show source metadata
and pass lookup context to Gemini without invented score adjustments.

Alert a server-configured fallback person with Resend on first crossing 50, then
75 and 90. A direct jump to 90 sends one highest-tier alert. Track attempted and
successful tiers independently, suppress repeats/jitter, use stable idempotency
and document/test bounded retries. Email contains severity, time, caller context
and useful scores, not full transcripts. Configure RESEND_API_KEY, ALERT_FROM_EMAIL
(verified sender), FALLBACK_EMAIL privately. Show configuration and recipient in
the local UI before listening, never claim delivery merely from configuration or
API acceptance. Missing email settings must not block local screening.

**Latest instruction: credentials will be added later. Never send real email in
this development task. Use fake transport for every delivery test; do not wait for
credentials.** Runtime alerts remain part of the requested product.

## Safety and lifecycle

Keep keys server-side and contacts out of commits/logs. No application persistence
or logging of microphone audio or transcripts. Optional transcript is memory-only.
Show actual STT, Gemini, DB, TTS and Resend lifecycle/results/errors, with waiting or
unavailable scores before valid classification. Explain provider processing.
Preserve same-origin WebSockets, TrustedHost, CSP, bounded messages/buffers, timeouts,
session caps, concurrency and provider/mic/playback cancellation on pause/disconnect.
Guard warning playback against feedback using echo cancellation and bounded explicit
suppression while maintaining media heartbeats; promptly resume afterwards. Handle
cancel/interruption and autoplay errors. Do not claim acoustic proof from fake audio.
Remove only genuinely superseded code/dependencies. Update doctor and .env.example;
no diagnostic that waits for an unsolicited greeting. Preserve useful env compatibility.

## Entire workflow and delivery

Before edits verify `entire session current` resolves to this captured session and
current prompt; stop if not captured. Never modify trust or fake checkpoints. Read
AGENTS.md and .entire/agent-guide.md. Check Brain freshness, use a task brief and a
useful structural Graph lookup; use graft first and callers before the refactor.
Start from clean setup/entire-hackathon at c2fcef8 and create feature/live-scam-screening.
Preserve setup and dataset work and any unrelated concurrent changes. Update
AGENTS.md/README and supersede the retained listener-only fact: risk criteria,
caller lookup and fallback email are now explicitly authorized.

Preserve .entire/runners schemas and output contracts; tailor their prompts for
pipeline correctness, threshold evidence, privacy and delivery. Never rerun runner
setup. Try a real Trail on the pushed branch if available; parent previously saw
Trails API 404 and no switches. If still unavailable, report it separately without
repeated troubleshooting; continue local Entire work. Make focused passing commits,
push normally (no force), confirm checkpoint list, explain HEAD and remote checkpoint
ref. If first push fails after pre-push sync, inspect remote and retry once.
On clean committed checkout refresh `entire brain refresh --agent none --raw`,
remember decisions with supported paths/kinds and supersede old scope. CLI 0.11.4 /
Brain 0.1.0 compact transcript export lost searchable exchanges; preserve raw until
verified otherwise. Verify retrieval. No watcher, model distillation or bulk history import.

## Acceptance and evidence

Test with fake providers/transports: score-only no TTS, eligible warning path, all
criteria and full family-emergency case; 49/50/74/75/89/90, repeated/rising/falling
scores, direct 90 jump, send failures/idempotency; malformed/out-of-range/blocked
Gemini/timeouts; context accumulation/bounds/coalescing; known/unknown/unavailable
numbers; origin, cancellation, timeout and healthy silent sessions.

Adapt the real Chrome synthetic microphone harness: trigger -> numbers/colors ->
warning playback and actual tool activity; low-risk no playback; pause releases mic
and providers; tabs/mobile; feedback suppression; readiness and errors. Preserve
meaningful assertions and test new JS logic. Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
node --test tests/test_browser_audio.mjs
uv build
uv run --with playwright python tests/browser_listener.py
```

Bounded real Gradium/Gemini probes using only synthetic text/audio and modest usage
are authorized. No recording of people or real email. Record compact English demo
evidence, actual command results, branch/commits/checkpoints and honest limitations.
Separate captured development, fake/browser validation, real API probes, outstanding
manual speakerphone test and unavailable Trails/Runners. No invented results, jury
scores, token savings or email delivery; do not commit provider transcripts/secrets.

## Accepted independent-review fixes

The parent reproduced valid Gemini criteria being rejected because the model's
weighted total was arithmetically inconsistent. Revise the model schema to return
only six strictly validated criteria and optional warning text. Compute the total
in application code, clearly identify it as calculated in the UI, and use it for
colors, email tiers and warning gating. Preserve all six model scores; no wider
arithmetic tolerance or fabricated safe scores. High scores without text stay silent.
Retest the two reported partial family-emergency phrases and the live-provider
synthetic browser flow without repeating unrelated expensive probes.

Make `doctor --live` exit nonzero on failed provider checks, rejected credentials or
missing transcription; genuine success exits zero. Test with injected providers.
Retain the parent's successful independent synthetic browser result and distinguish
it from a real phone-on-speaker test. Run required checks, update evidence, commit
and push on the captured feature branch, verify local and remote checkpoint linkage,
and refresh Brain with `--agent none --raw` on the clean committed checkout.

After verification, confirm the old project server on port 8082 has no active call,
gracefully restart only that server with the updated backend, verify health/config
and leave the local demo ready. No real microphone activation or email sending.
