# Scam the Scamers

A local call-screening companion. Answer a call on your phone, turn on
speakerphone, and place your laptop nearby. The browser microphone sends audio
through the backend to Gradium STT. Gemini scores six warning signs; scores
update quietly. Only eligible caution text is sent to Gradium TTS and played aloud.
There are no greetings or open-ended agent replies.

## Getting started

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). Setup installs
Python 3.12 and the application dependencies.

```bash
scripts/setup.sh
# Privately add GRADIUM_API_KEY and GEMINI_API_KEY (or existing LLM_API_KEY) to .env.
scripts/run_local.sh
```

Open **http://localhost:8082**. Optionally enter the caller's phone number, check the
fallback email status, click **Enable listener**, and allow microphone access.
Keep the tab open and computer awake. Sound above the adjustable threshold for
180 ms starts screening; a bounded pre-roll preserves the start of speech.
Ringing or music can trigger it. The app cannot detect call acceptance or caller ID.

The responsive workspace offers **Split view**, **Listener** and **Tools & activity**.
Switching views does not interrupt audio. Scores start as waiting, never invented zeroes.
Actual Gradium STT, Gemini, SQLite lookup, Gradium TTS and Resend results/errors appear
in the activity panel. The most recent 60 events stay only in page memory.

**End screening** flushes the last audio/text and returns to waiting. After 30 seconds
of quiet the browser also finalizes the session. **Pause listener** immediately
cancels work, stops playback and releases the microphone. The server enforces a
capture time limit, provider/media timeouts and a concurrency limit. Finalization
has at most the request timeout plus 15 seconds of grace; pause/disconnect still
cancels immediately during that grace period.

## Scores and warnings

Criterion scores describe evidence strength: 0 absent, 25 ambiguous, 50 plausible,
75 strong, 100 explicit. They are model judgments, not calibrated probabilities.
Gemini returns all six criterion scores and optional warning text. All six model
scores are displayed. The backend deterministically calculates the overall screening
indicator using the weighted sum below, rounded to one decimal; the UI labels it
**Calculated overall risk**. The model is not asked to return or validate that sum.
This avoids losing valid evidence to model arithmetic errors. Malformed, incomplete
or out-of-range criteria still show unavailable, without fabricating scores or speech.
All overall colors, email tiers and spoken-warning eligibility use this calculated total.

| Criterion | Example | Overall weight |
| --- | --- | ---: |
| Family-member claim | “I'm your daughter.” | 5% |
| Emotional distress | “I've been arrested.” | 10% |
| Urgency | “Within the hour.” | 20% |
| Secrecy | “Don't tell Mum and Dad.” | 20% |
| Financial request | “CHF 5,000 in cash.” | 25% |
| Courier pickup | “Someone will collect the cash.” | 20% |

A family claim by itself contributes at most **5/100**. Each criterion and the overall
use green below 50, amber from 50, red from 75 and critical from 90. Higher values
warrant caution and independent verification; they do not establish fraud. A criterion
can have strong evidence without the whole call being suspicious. The rubric needs
real-world calibration and may miss other scam patterns.

Default `WARNING_THRESHOLD=50` controls spoken cautions independently of email tiers.
Gemini may return no warning text, even at high scores; this never triggers TTS.
Eligible text must be one of three short caution phrases defined in `risk.py`, preventing
caller-injected instructions from being spoken. Each reached warning tier is attempted
at most once per session; failed/cancelled warnings are not repeatedly synthesized.
**Stop warning audio** cancels playback. Echo cancellation plus bounded silence forwarding
in the browser/backend prevents warning audio from feeding back into the classifier.
Speech overlapping a warning can be missed; real speakerphone acoustic testing is still
required. A browser audio error pauses the listener with an actionable message.

## Caller number lookup

The optional number is supplied by you, never detected. International `+` numbers
are supported; national numbers default to **Switzerland (`CALLER_DEFAULT_REGION=CH`)**.
For example `079 123 45 67` becomes `+41791234567`, not a US number. Use an explicit
international prefix for other countries or configure the default ISO region.

The existing [dataset and provenance](data/scam_numbers/README.md) are preserved and
queried locally with read-only, parameterized SQLite. The wheel includes the database;
`SCAM_DB_PATH` can explicitly override its location. UI states distinguish omitted,
invalid, matched, no match and unavailable. A match includes source/import time and
may be stale; caller ID can be spoofed. No match is **not** proof of safety. Lookup
status is explicit Gemini context, with no automatic numerical score bonus.

## Fallback email via Resend

Set these privately in `.env`, then restart the server:

```dotenv
RESEND_API_KEY=
ALERT_FROM_EMAIL=
FALLBACK_EMAIL=
```

Use a plain sender address verified in your Resend account and one valid recipient.
The local UI displays the configured fallback address before listening. Missing or
invalid configuration is clearly marked and does not prevent screening. Once configured,
starting the listener enables automatic runtime alerts; the browser cannot supply a
recipient, sender or arbitrary email body.

A first crossing of 50 sends a caution; reaching 75 or 90 escalates. A direct jump to
90 sends one critical alert. Repeated scores and falling/rising jitter do not resend
previous tiers. Pending unsent escalations coalesce to the highest. Attempted and
API-accepted tiers are tracked separately in session memory. Transport failures, 429
and 5xx receive at most **one retry after one second**, using the same idempotency key
and exact payload; a newer pending tier supersedes the retry. Authentication/validation
failures are not retried. No repeated retry on each transcript. New sessions have new
identities, so they may alert again; restarting is not a delivery recovery mechanism.

Emails contain severity, UTC time, caller context and criterion scores, without raw
transcripts. “Accepted by Resend” is not verified inbox delivery. No delivery webhook
is installed. **Development and `doctor --live` never send real email**; transport tests
use fakes. Email credentials and a separately authorized delivery test remain needed.

## Configuration and privacy

`.env.example` lists supported options. Existing `LLM_API_KEY` remains valid;
`GEMINI_API_KEY` takes precedence when nonempty. Existing Google configuration is
preserved: `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai` and
`LLM_MODEL=gemini-3.5-flash-lite`. Only Google's compatible endpoint and Gemini models
are accepted; there is no fallback to another LLM. API keys stay on the server.

```bash
uv run scam-the-scamers doctor         # Configuration only; no network or email
uv run scam-the-scamers doctor --live  # Bounded synthetic Gemini/Gradium probes; API credits
```

`doctor --live` exits 0 only when both Gemini examples, Gradium TTS and nonempty
Gradium transcription succeed. Rejected credentials, provider/time-out/cleanup errors
or missing transcription exit 1 with redacted diagnostics. Missing Resend settings
are reported separately and do not fail the screening checks; email is never tested.

Defaults: one session, five-minute cap, 20-second HTTP request timeout, 45-second STT
heartbeat timeout, 20-second media timeout and 1.5 seconds between classification
starts. No first-TTS timeout exists: low-risk sessions can remain silent throughout.
`FIRST_AUDIO_TIMEOUT_SECONDS` is obsolete and ignored for env compatibility.

STT uses 24 kHz PCM in 80 ms frames and flushes after a short acoustic pause. Text
segments accumulate; `end_text` and `flushed` are boundaries, not duplicate text.
One Gemini request runs at a time, coalescing new text and ordering assessments by
revision. Context is bounded to 12,000 characters; after clipping, the opening third
and latest conversation are retained and the UI discloses truncation. This can lose
middle context on long calls. Provider failures produce an unavailable assessment;
new speech can trigger the next attempt. Pause/disconnect cancels in-flight work.

While waiting, audio stays in the browser. During screening, Gradium processes audio,
Google processes bounded transcript/lookup context, and configured Resend alerts process
the risk summary and contact addresses. The app does not store or log audio/transcripts;
provider retention policies are separate. Classification uses stateless chat requests,
without provider conversation IDs. Contacts and keys are not committed. Same-origin
WebSockets, loopback Host checks, CSP, bounded queues/messages and timeout cleanup remain.

Provider formats were verified against official documentation:
[Gradium STT](https://docs.gradium.ai/guides/speech-to-text),
[Gradium lifecycle](https://docs.gradium.ai/guides/websocket-lifecycle),
[Gradium TTS](https://docs.gradium.ai/guides/text-to-speech-rest),
[Gemini compatible structured output](https://ai.google.dev/gemini-api/docs/openai#structured-output),
[Resend send email and idempotency](https://resend.com/docs/api-reference/emails/send-email).
The Google-compatible JSON-schema endpoint was selected to preserve the configured
endpoint/model and verified with actual requests; no Interactions conversation is created.

## Repository and verification

`providers.py` owns concrete network adapters; `agent.py` orchestrates injected providers;
`risk.py` defines validation/rubric; `alerts.py` manages delivery tiers; `caller.py` wraps
the preserved dataset helper. `bridge.py` handles the browser transport, `main.py` the
local HTTP routes, and `static/` the listener. Entire remains developer tooling.

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest -q
node --test tests/test_browser_audio.mjs
uv build
uv run --with playwright python tests/browser_listener.py  # Installed Chrome required
```

Tests use fake providers, synthetic microphone audio and mock email transport, with no
API keys required. A separate opt-in check uses the existing Gradium/Gemini credentials
and modest API credits, with generated speech as the microphone source:

```bash
uv run --with playwright python tests/live_browser_listener.py --live
```

It forcibly disables Resend, uses muted Chrome, checks valid partial assessments and
calculated totals, warning playback, microphone/provider cleanup and page errors.
It does not activate a physical microphone, send email or establish acoustic performance.

The offline Chrome harness checks sound activation, six live scores/colors,
low-risk silence, warning playback/suppression/cancellation, provider cleanup, end/quiet
rearm, tabs/mobile layout, readiness and errors. See the
[accepted work order](docs/live-scam-screening-task.md) and
[evidence/demo notes](docs/live-scam-screening-evidence.md) for executed results and limits.

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
   the Entire GitHub App access to `Sangeeths11/agentic-hack-zurich-2026` and import
   that repository. Existing GitHub collaborator permissions determine access.
3. Install local hooks from this checkout:

   ```bash
   entire enable --agent codex --yes --telemetry=false --absolute-git-hook-path
   entire doctor
   ```

   Start Codex from the repository root. If startup fails because the shared
   background server has incompatible settings, launch it without that server:

   ```bash
   codex --no-daemon
   ```

   This runs Codex without the shared background server, even if it is already
   running. In Codex, open `/hooks`, review and explicitly trust all seven Entire
   hooks, then start a fresh session. Installing the hooks does not grant trust;
   they must be approved before they can run.

   Ask the agent in the fresh session to run `entire session current`. Check that
   it resolves to its own active Codex session and shows your latest prompt.
   This confirms session registration; verify commit linkage separately with
   `entire checkpoint list` and `entire checkpoint explain HEAD` after committing
   a real change. An active session with zero checkpoints does not yet prove
   that a commit has been captured.
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
entire brain refresh --agent none --raw
entire brain brief "Understand the local listener and its constraints" --json
entire graph search --repo . --profile full --query "microphone session cleanup"
```

Brain is initialized without background model jobs or a machine-wide daemon.
Refresh it with `entire brain refresh --agent none --raw` after new checkpoints or
important commits. Record lasting decisions with `entire brain remember` and an
explicit `--path`. Re-run `entire brain setup` after the generated instruction
files are committed if it reports a dirty-worktree indexing error, then run the
raw refresh above.

With Entire CLI 0.11.4 and Brain 0.1.0, the default compact Codex export produced
zero searchable conversation exchanges in this repository. The supported `--raw`
export restored retrieval from the original captured transcript. It changes the
local Brain index, not the checkpoint stored in Git. Keep this flag during
refreshes until the installed versions are verified to handle compact records.
The default watcher is not enabled because its refresh path uses compact export.
Automatic fact extraction and model-backed background jobs are also not enabled;
the working memory sources are captured history, code, docs, and authored facts.

At the beginning of a substantial task, verify capture with
`entire session current`, check `entire brain status`, refresh if stale, then use
`entire brain brief "<task>" --json`. To recover a previous conversation directly:

```bash
entire brain query "no-daemon" --source conversation --limit 3 --json
```

Use a relevant Graph lookup for code structure and verify its source pointers.
After a change, run the appropriate checks, commit and push, verify its checkpoint,
then refresh Brain so the next task can retrieve it. This refresh is deterministic
and launches no additional coding agent. `entire graph stats --repo .` currently
looks for Claude Code transcripts; a missing report in this Codex workflow is not
evidence of token savings. Graft estimates belong to a separate tool.

Checkpoint metadata is pushed alongside development work. Built-in secret
redaction remains enabled, with email and phone-number redaction added. Keep
credentials and real call recordings out of development transcripts and screenshots;
image contents are not redacted. Historical imports are labeled read-only history,
not evidence that live hooks captured a session.

For the demo, show the listener, a real captured checkpoint, its passing validation,
and the Trail's actual findings. Demonstrate a useful Graph lookup or Brain recall.
Report measured results and clearly distinguish synthetic audio checks from a real
phone-on-speaker test.
