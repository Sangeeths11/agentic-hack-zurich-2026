# Scam the Scammers

A local call-screening companion. Answer a call on your phone, turn on
speakerphone, and place your laptop nearby. The browser microphone sends audio
through the backend to Gradium STT. Gemini scores six warning signs; scores
update quietly. Only eligible caution text is sent to Gradium TTS and played aloud.
There are no greetings or open-ended agent replies.

The display name and command are **Scam the Scammers** / `scam-the-scammers`.
The old command remains an alias for existing launchers; the internal Python module
keeps its existing import path.

## Verified integration status

Gradium streaming STT, Gemini classification and optional Gradium warning TTS were
verified through real APIs and browser playback using synthetic speech. Gemini returns
six criterion scores; the server deterministically calculates the overall indicator
used for 50/75/90 alert tiers. The preserved local SQLite number lookup is integrated
and tested. Resend alerts are implemented and mock-tested, but credentials, verified
sender and fallback recipient remain unconfigured; no real email has been sent.
CallMeBot WhatsApp guardian alerts are implemented and tested with fake transport;
real WhatsApp acceptance and delivery have not been tested.

Entire session capture, commit checkpoint synchronization, Graph and Brain have been
verified. Six runner definitions are prepared, but hosted Trails/Runners remain
unavailable following the observed API 404; no hosted evaluation is claimed. See the
[executed evidence](docs/live-scam-screening-evidence.md). Real phone-on-speaker
acoustic testing, calibrated fraud accuracy and guardian-message delivery remain unverified.

## Getting started

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). Setup installs
Python 3.12 and the application dependencies.

```bash
scripts/setup.sh
# Privately add GRADIUM_API_KEY and GEMINI_API_KEY (or existing LLM_API_KEY) to .env.
scripts/run_local.sh
```

Open **http://localhost:8082**. Optionally enter the caller's phone number and click **Check number** for an immediate
local lookup, configure the guardian WhatsApp number and check the messaging status,
then click **Enable listener**, and allow microphone access.
Keep the tab open and computer awake. Sound above the adjustable threshold for
180 ms starts screening; a bounded pre-roll preserves the start of speech.
Ringing or music can trigger it. The app cannot detect call acceptance or caller ID.

The responsive workspace offers **Split view**, **Listener** and **Tools & activity**.
Switching views does not interrupt audio. Scores start as waiting, never invented zeroes.
A live pipeline graph highlights actual microphone, backend, SQLite, Gradium STT,
Gemini, local scoring, Gradium TTS, playback, CallMeBot and Resend activity. Streaming connections
and individual requests have distinct states. The most recent 60 events stay only in
page memory. The live transcript shows bounded accumulated context on separate lines.
Gradium timestamps delimit segments; Gemini can infer Caller or You from the words
and context. Labels explicitly say **inferred**, and unclear speech remains **Speaker
unknown**. This is not acoustic diarization, voice matching or verified identity.
Original transcribed text is preserved; the model supplies only roles for existing
turn IDs. Transcripts are never written to browser storage or application logs.

**End screening** flushes the last audio/text and returns to waiting. After 30 seconds
of quiet the browser also finalizes the session. **Pause listener** immediately
cancels work, stops playback and releases the microphone. The server enforces a
capture time limit, provider/media timeouts and a concurrency limit. Finalization
has at most the request timeout plus 15 seconds of grace; pause/disconnect still
cancels immediately during that grace period.

## Personal settings and emergency contacts

Use the **Settings** text box for your name, family details and emergency contacts:

> My name is Margaret. My daughter Emily lives in Bern. I have no sons.
> Emergency contact: +1 202 555 0123 (Emily, daughter).

The example is fictional. After a short typing pause, Gemini extracts fixed fields;
the UI shows numbered contact cards, relationships, phone numbers and WhatsApp readiness.
It never renders model-generated HTML. A contact destination must be present in your
text and normalize to a valid phone number. Review the cards for extraction mistakes.
Every eligible configured contact receives the alert; duplicate phone numbers are removed.

Accepted edits apply to the current call and trigger a fresh classification of the
existing conversation. Revision checks prevent late results from replacing newer
settings or alerting removed contacts. An already-sent request cannot be recalled.
If extraction fails, the previous accepted configuration stays active and the UI
explains the error. Clearing Settings removes its contacts. Personal settings stay
only in this local server's memory until it restarts, and are shared by tabs connected
to this server; they are not saved in browser storage.

Gemini receives the original personal context and parsed contacts alongside the bounded
conversation. Family inconsistencies can inform the family-claim criterion or independent
scam flag. Settings and speech remain untrusted data, not instructions to the model.
An inconsistency is a warning sign, not proof of fraud. Personalized voice cautions use
short fixed templates with the listener and a trusted contact's name; arbitrary model
prose, contact numbers and caller instructions are never spoken as a warning.

Controls let you set **spoken-warning** and **WhatsApp score thresholds** separately
(1–100), plus the minimum **WhatsApp escalation gap** (0–300 seconds, default 60).
Higher thresholds reduce score-triggered alerts. Independent Gemini scam judgments
and listed-number flags still qualify; the calculated score and 50/75/90 severity
colors do not change. Email keeps its existing 50/75/90 tiers. Sound activation has
its separate microphone sensitivity control.

## Scores and warnings

Criterion scores describe suspicious evidence in context: 0 absent, 25 ambiguous,
50 plausible, 75 strong, 100 explicit. Ordinary family greetings, routine agreed
payments and quoted/negated scam examples are not suspicious evidence by themselves.
Gemini returns all six scores, an independent `is_scam` judgment and optional safe
warning text. These judgments are not calibrated fraud probabilities.

The backend calculates **Calculated additive total** as
`min(100, family_claim + emotional_distress + urgency + secrecy + financial_request + courier_pickup)`,
rounded to one decimal. No averaging or weighting dilutes an isolated severe signal:
urgency 80 and five zeroes gives 80; urgency 30 plus secrecy 25 gives 55. All six
Gemini scores remain separately visible. Gemini never calculates or validates the sum.
Malformed, incomplete or out-of-range criteria remain unavailable.

| Criterion | Suspicious context example |
| --- | --- |
| Family-member claim | An unverifiable family identity used to demand help |
| Emotional distress | An alleged arrest or accident used to pressure the listener |
| Urgency | “Pay within the hour.” |
| Secrecy | “Don't tell family or authorities.” |
| Financial request | A suspicious demand for cash or financial details |
| Courier pickup | Someone collecting the demanded cash |

The calculated total uses caution at 50, high at 75 and critical at 90. Independently,
Gemini's scam judgment raises a high warning even when the six scores are low. A local
number match also raises a high warning without changing or inventing conversation
scores. The whole scoring panel turns orange for caution and red for high/critical;
it states the reason for the flag separately from the additive total. A listed number
may be stale or spoofed, and neither a low score nor a missing match establishes safety.

Default `WARNING_THRESHOLD=50` gates spoken cautions based on scores; `is_scam=true`
bypasses that threshold. **Score-only assessments never synthesize speech**:
an allowlisted caution intent must still be present. The backend personalizes that
intent using a fixed template when profile names are available. Gemini is
instructed to supply one when it judges the conversation a scam, even at low scores.
A number-only warning is visual and can alert a configured guardian during screening;
checking a number before screening does not send guardian messages or synthesize speech. Each
reached spoken-warning tier is attempted at most once per session; failed/cancelled
warnings are not repeatedly synthesized.
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
`SCAM_DB_PATH` can explicitly override its location. The **Check number** action works without microphone access or provider credentials.
Exact stored matches still warn when legacy entries have unusual phone-number lengths;
unmatched invalid numbers remain invalid. Starting a screening session repeats the lookup and sends its result before opening
Gradium, so provider delays or errors cannot hide a listed-number warning. UI states
distinguish omitted, invalid, matched, no match and unavailable. A match includes source/import time and
may be stale; caller ID can be spoofed. No match is **not** proof of safety. Lookup
status is explicit Gemini context, with no automatic numerical score bonus. A matched
number independently flags the scoring panel; clearing/changing the input invalidates
the previous lookup. Use `+` or `00` for international numbers.

## Guardian WhatsApp via CallMeBot

Set `CALLMEBOT_API_KEY` privately in `.env` and `GUARDIAN_PHONE` as its matching
default recipient, then restart the server. That contact prefills Settings. For
additional contacts, set `CALLMEBOT_RECIPIENT_KEYS` to a private JSON object mapping
each international number to its own activated key. Keys stay on the server; the
original key is never reused for an unrelated recipient. The legacy guardian field
is available only before personal settings take over. Use an international `+` or
`00` prefix, or a national number in `CALLER_DEFAULT_REGION` (Switzerland by default).

The guardian must activate CallMeBot and the server key must match that number;
changing the field alone cannot authorize a different recipient. Follow the
[official CallMeBot setup](https://www.callmebot.com/blog/free-api-whatsapp-messages/).
Configuration status does not verify the key or delivery. Missing/invalid settings
remain visible and do not block screening or the independent fallback email.

By default alerts use **Amber at 50, Red at 75 and Critical at 90**; the WhatsApp
control changes the minimum score that triggers a message. A Gemini scam judgment
or a matched caller number independently raises tier 75, even without a qualifying
score. Repeated scores and jitter do not resend an attempted tier; a direct jump
to 90 sends only the highest pending alert. Each new session can alert again.
Messages address the contact and listener by configured names and include a short
Gemini conversation summary, attributed caller claims, family inconsistencies and
the actual score. A number-only alert explicitly says the conversation is not yet
available. Summaries can be mistaken; they are labeled as AI summaries. Messages do
not invent voice matching, callback numbers, listen-in links or playback completion.
The summary shares relevant conversation details with your selected contacts; the
complete raw transcript is not included.

An initial short debounce (3 seconds, bounded at 6 during continuous changes)
combines nearby assessments into one alert with the highest severity and latest
summary. Later escalations wait for the configured per-contact gap; pending updates
coalesce. Ending a session discards an escalation still waiting for that gap rather
than delaying shutdown or sending it later. Pausing cancels pending work. These
limits are session-local; a new screening session can alert again. The application
cannot control how WhatsApp groups messages or how CallMeBot queues them downstream.

CallMeBot has no documented idempotency key, so each tier is attempted once with a
bounded timeout and **no automatic retry**. A timeout or ambiguous response leaves
delivery unknown: resending could duplicate an already queued message. Only the
documented queue acknowledgement displays **Guardian alert accepted at [time] — [alert
level]**. The banner tracks WhatsApp and email separately and explains that provider
acceptance is not confirmed delivery. The activity graph shows real request and
failure states. Development tests and diagnostics never send real guardian messages.

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

A first crossing of 50 sends a caution; reaching 75 or 90 escalates. A Gemini scam
judgment or a matched number during screening independently qualifies for tier 75. A direct jump to
90 sends one critical alert. Repeated scores and falling/rising jitter do not resend
previous tiers. Pending unsent escalations coalesce to the highest. Attempted and
API-accepted tiers are tracked separately in session memory. Transport failures, 429
and 5xx receive at most **one retry after one second**, using the same idempotency key
and exact payload; a newer pending tier supersedes the retry. Authentication/validation
failures are not retried. No repeated retry on each transcript. New sessions have new
identities, so they may alert again; restarting is not a delivery recovery mechanism.

Emails contain severity, UTC time, caller context and available criterion scores,
without raw transcripts. A number-only alert explicitly says scores are not yet available.
Only a successful Resend response adds email to **Guardian alert accepted at [time] — [alert level]**
in the frontend. The banner also explains that this is API acceptance, not confirmed
inbox delivery. Errors, retry attempts and missing configuration never show a success banner. “Accepted by Resend” is not verified inbox delivery. No delivery webhook
is installed. **Development and `doctor --live` never send real email**; transport tests
use fakes. Email credentials and a separately authorized delivery test remain needed.

## Configuration and privacy

`.env.example` lists supported options. Existing `LLM_API_KEY` remains valid;
`GEMINI_API_KEY` takes precedence when nonempty. Existing Google configuration is
preserved: `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai` and
`LLM_MODEL=gemini-3.5-flash-lite`. Only Google's compatible endpoint and Gemini models
are accepted; there is no fallback to another LLM. API keys stay on the server.

```bash
uv run scam-the-scammers doctor         # Configuration only; no network or messages
uv run scam-the-scammers doctor --live  # Bounded synthetic Gemini/Gradium probes; API credits
```

`doctor --live` exits 0 only when both Gemini examples, Gradium TTS and nonempty
Gradium transcription succeed. Rejected credentials, provider/time-out/cleanup errors
or missing transcription exit 1 with redacted diagnostics. Missing Resend settings
and WhatsApp settings are reported separately and do not fail the screening checks;
guardian messaging is never tested by diagnostics.

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
Google processes personal Settings text plus bounded transcript/lookup context, and configured CallMeBot/Resend
alerts process the risk summary and contact addresses. CallMeBot requires its key,
recipient and encoded message in a HTTPS query; application HTTP debug logging is
suppressed for that request. The app does not store or log audio/transcripts;
provider retention policies are separate. Classification uses stateless chat requests,
without provider conversation IDs. Contacts and keys are not committed. Same-origin
WebSockets, loopback Host checks, CSP, bounded queues/messages and timeout cleanup remain.

Provider formats were verified against official documentation:
[Gradium STT](https://docs.gradium.ai/guides/speech-to-text),
[Gradium lifecycle](https://docs.gradium.ai/guides/websocket-lifecycle),
[Gradium TTS](https://docs.gradium.ai/guides/text-to-speech-rest),
[Gemini compatible structured output](https://ai.google.dev/gemini-api/docs/openai#structured-output),
[Resend send email and idempotency](https://resend.com/docs/api-reference/emails/send-email),
[CallMeBot WhatsApp API](https://www.callmebot.com/blog/free-api-whatsapp-messages/)
and its linked [response example](https://documenter.getpostman.com/view/20714522/UyrEiFgK).
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

Tests use fake providers, synthetic microphone audio and mock messaging transport, with no
API keys required. A separate opt-in check uses the existing Gradium/Gemini credentials
and modest API credits, with generated speech as the microphone source:

```bash
uv run --with playwright python tests/live_browser_listener.py --live
```

It forcibly disables Resend and CallMeBot, uses muted Chrome, checks valid partial assessments and
calculated totals, warning playback, microphone/provider cleanup and page errors.
It does not activate a physical microphone, send guardian messages or establish acoustic performance.

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
