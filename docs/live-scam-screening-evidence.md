# Live scam screening: evidence and demo

## Investigating missing WhatsApp alerts

On 2026-10-10 the user reported a high alert without a received WhatsApp message.
The current browser activity established an actual critical score of 100 and a tier-90
CallMeBot send attempt at 17:35:27, followed by an error. The configured threshold of
80 did not block this attempt. The old handler discarded the HTTP status and body,
so the precise reason for that historical failure cannot be recovered.

Unauthenticated connectivity probes reached the API without supplying a recipient,
key or message. A missing-parameter response used HTTP 201 with a leading `ERROR:`.
The user then explicitly approved exactly one diagnostic message. That single request
returned HTTP 200 and CallMeBot's terminal queue acknowledgement. The user initially
reported no message, then confirmed that the guardian received it after a delay.
No retry or second diagnostic was sent. This establishes user-confirmed delivery of
the short diagnostic, not acceptance or delivery of the earlier full alert.

The provider adapter now retains HTTP status and maps only leading error responses
to fixed categories. The activity detail distinguishes recipient/key, message-size,
rate-limit, unexpected HTTP and ambiguous transport failures, without displaying
provider bodies, keys, numbers or message text. Echoed outgoing text is not treated
as a provider error. Retry suppression and acceptance validation remain intact.

Validation: 283 Python tests, 22 JavaScript tests, Ruff lint/format, mypy and both
build artifacts passed. The local server was restarted with the same personal
settings handed over in memory; no profile was written to storage or reparsed by
a provider. No audio or browser implementation changed in this follow-up.

## Resumed personalized guardian screening

Continued the interrupted working tree on `feat/personalized-screening` from
`1c06373` on 2026-10-10, with three sub-agents requested by the user. This section
supersedes the older single-recipient, pre-session-only settings behavior below.

The verified flow is: edit guardian/personal settings in the browser, accept bounded
server-memory preferences, reassess the active conversation, select eligible contacts
and severity, send a recipient-bound CallMeBot request, then display provider acceptance.
The supplied default recipient and key already existed in ignored `.env` with mode
`0600`; configuration was checked without sending messages or printing either value.

Personal settings provide contact cards, family context and live warning/WhatsApp
thresholds. Each additional recipient requires its own server-side key. Gemini summaries
and inferred speaker roles are attributed; original transcript segments retain stable
IDs. Fixed personalized warnings and WhatsApp messages do not claim acoustic identity
matching, a listen-in service, or completed warning playback. The banner now says
**Guardian alert accepted**, with delivery explicitly unverified.

Review corrected fallback sessions that ignored server WhatsApp threshold/cooldown
settings, and rejected stale/equal preference updates in the alert manager. Settings
changes invalidate unsent contact work. TTS now rechecks cancellation and settings
revision after potentially blocked activity publication, before invoking Gradium;
obsolete warning audio already queued in the session is discarded on receive.

| Boundary/check | Executed evidence |
| --- | --- |
| Browser → settings API → active session | Offline Chrome: extracted contacts, live preference changes, stale response rejection and settings failures |
| Assessment → recipient selection | Synthetic regressions: independent scam/number flags, configurable threshold, 50/75/90 tiers, contact removal and recipient-specific keys |
| Recipient → CallMeBot | Fake transport: encoded requests, bounded timeouts/responses, exact destination, queue acknowledgement, no ambiguous-send retry |
| Response → browser | Mock acceptance for two contacts and both channels; explicit acceptance banner; error, unknown and stopped states |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | Passed, 28 files |
| `uv run mypy src` | Passed, 13 source files |
| `uv run pytest -q` | 274 passed; one upstream Starlette/httpx TestClient deprecation warning |
| `node --test tests/test_browser_audio.mjs` | 22 passed |
| `uv build` | Source distribution and wheel built |
| `uv run --with playwright python tests/browser_listener.py` | 6 synthetic sessions; no page errors |
| Local server HTTP checks | Health/readiness, page/assets and profile/config passed; key absent from public responses; no screening initiated |
| Runner JSON comparison | All six schemas/settings/output contracts preserved; only prompt templates changed |
| Candidate source and wheel private-value scan | Passed; supplied key and recipient absent |

The live audio harness disables both messaging providers, including the additional
recipient-key map. **No real WhatsApp or email was sent in this continuation.** Live
CallMeBot acceptance/delivery, physical phone acoustics and fraud calibration remain
unverified. Earlier Gradium/Gemini live synthetic evidence below was not rerun.

Entire resolved the executing active session and latest prompt. Doctor reported Git
hooks OK and Codex hook approval records present, plus an older ended session with
uncondensed checkpoint data. Brain reported a stale branch tip and dirty/unindexed
working tree at entry, so current source and executed tests supplied the evidence.
Authenticated repository inspection reported both Entire mirrors ready. Hosted
Trails still returned repository-not-found HTTP 404; no hosted evaluation is claimed.

## Guardian WhatsApp follow-up

Implemented on `feat/whatsapp-guardian-alerts` from `b770f03` on 2026-10-10 with
three explicitly requested sub-agents. All preceding lookup, additive scoring,
independent flag, transcript and activity features remain included.

CallMeBot receives bounded, encoded HTTPS requests with a server-held key and the
guardian selected before the session. Swiss/other configured national formats and
international prefixes normalize on the backend. The UI locks the contact while
listening and never persists edits or sends a message merely from editing it.
Missing settings remain visible without blocking screening. Supplied configuration
was saved only to ignored local files; neither credentials nor private contacts
appear in tracked source or the built wheel. Exact local checkpoint redactions were
added for the supplied values before committing.

Messages use actual scores and criterion labels, plus independent scam/number flags.
They do not invent voice matching, identities, amounts, listen-in links, callback
numbers or warning playback. WhatsApp tiers 50/75/90 are independent of Resend;
direct jumps, pending coalescing and score jitter do not duplicate attempted tiers.
CallMeBot has no documented idempotency key, so ambiguous sends are not retried.
Only the documented terminal queue acknowledgement emits a success banner. HTTP 200,
configuration, errors and timeouts do not establish acceptance or delivery. The UI
tracks each channel's acceptance and retains the highest accepted severity.

Executed checks:

| Check | Result |
| --- | --- |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | Passed, 24 files |
| `uv run mypy src` | Passed, 12 source files |
| `uv run pytest -q` | 210 passed; one existing upstream TestClient deprecation warning |
| `node --test tests/test_browser_audio.mjs` | 19 passed |
| `uv build` | Source distribution and wheel built |
| Offline Chrome harness | 5 synthetic sessions, zero page errors; fake WhatsApp acceptance, dual-channel banners, national/international recipient forms, missing-key visibility, graph and mobile layout, pause during an in-flight send |
| Runner comparison excluding `prompt.template` | All six schemas/settings/output contracts preserved |
| Tracked-source and wheel private-value scan | Passed; local configuration and redaction files remain ignored |
| Restarted localhost health/readiness/config checks | Passed; configured guardian default loaded; key absent from public config; no screening initiated |

Provider tests exercise encoded Unicode messages, exact destination, request-log
privacy, real documented response shape, redirects/rejections, ambiguous acknowledgements,
bounded bodies/timeouts and cancellation. Session tests cover cross-channel independence,
region normalization, disabled/invalid settings, flags, cleanup and new-session reset.
The live audio harness now explicitly disables both messaging channels regardless of
local environment settings. **No real WhatsApp or email was sent.** CallMeBot credential
validity, queue acceptance and handset delivery remain unverified; existing Gradium/
Gemini synthetic live evidence is retained below and was not unnecessarily rerun.

Entire capture still resolves to the executing session and latest request; doctor
reports trusted Codex hook approval records and working Git hooks. Brain was refreshed
with `--agent none --raw` from the clean starting checkout. Runner prompts now cover
CallMeBot's retry/acceptance and privacy contracts. Hosted Trails still returns 404;
no hosted evaluation or hackathon score is claimed.

## Additive scoring and live visibility follow-up

Implemented on `feat/live-screening-alerts` from `main` commit `c091487` on
2026-10-10, with three sub-agents explicitly requested by the user. This section
supersedes the older weighted-score behavior described in the historical evidence below.

The lookup now runs independently before microphone activation and reports its result
before Gradium opens during screening. Existing ordinary database records were already
queryable; the missing visible warning and provider-startup dependency hid the result.
Two preserved legacy entries were also incorrectly rejected by phone-length validation
before the query. Exact stored matches now warn without modifying the dataset.

Six finite, complete model scores are added and capped at 100 in the backend. Gemini's
independent scam judgment and matched numbers can raise a high warning without inflating
scores. Tests prove a total of 10 with a scam flag raises tier 75; absent warning text
still never invokes TTS. The browser shows actual pipeline activity, the bounded
accumulated transcript, full-panel warning colors and acceptance-only guardian banners.
Lookup warnings are visible beside the input on mobile; guardian banners appear in
every workspace view. Transcript text is rendered literally and cleared at session end.

Executed checks:

| Check | Result |
| --- | --- |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | Passed, 23 files |
| `uv run mypy src` | Passed, 12 source files |
| `uv run pytest -q` | 152 passed; one upstream TestClient deprecation warning |
| `node --test tests/test_browser_audio.mjs` | 18 passed |
| `uv build` | Source distribution and wheel built |
| Offline Chrome harness | 3 synthetic sessions, zero page errors; lookup, independent flags, live graph, transcript safety/clipping, fake guardian acceptance, mobile layout and audio cleanup |
| `uv run scam-the-scamers doctor --live` | Gemini examples, Gradium TTS and nonempty STT passed; no email |
| `uv run --with playwright python tests/live_browser_listener.py --live` | Passed with synthetic Gradium speech; 4 valid assessments, zero unavailable assessments, calculated total 100, warning playback scheduled in muted Chrome, pause cleanup passed, zero page errors |
| Synthetic Gemini technical-support scam probe | Valid six-score response, `is_scam=true`, allowlisted warning; no email or audio services used |
| Runner comparison excluding `prompt.template` | All six schemas/settings/output contracts preserved |
| Restarted localhost health and lookup HTTP checks | Passed; an exact legacy database match returned before microphone/provider use |

The live family-lunch diagnostic returned total 25 with no warning text; the synthetic
family-emergency scam returned total 100 with an allowlisted warning. These are actual
probe results, not a calibration study. Low-score scam override is established by fake
provider tests, not by the live technical-support probe (which scored 100).
No real email was sent; live browser settings forcibly disabled Resend. Guardian success
and failure states use fake transport. Physical phone/speaker acoustics and inbox delivery
remain unverified. The 12,000-character default context bound remains explicit: opening
and recent speech are retained when middle context must be clipped.

Entire doctor found Git hooks OK and Codex approval records present. The active session
resolved to `01a1261b-5389-7422-b1e5-86a56453df2a` with this request. Brain was refreshed
with `--agent none --raw` on the clean checkout before the task brief. A supported
Graph `neighbors` query inspected the new alert method; the generated guide's older
`query` subcommand remains unavailable. Entire's repository API reports both mirrors
ready, and its web settings show GitHub synced. However, no Runners/Trails controls are
exposed there and `entire trail create` still returns repository-not-found HTTP 404.
No hosted Trail evaluation is claimed. Runner prompt context was updated for the
accepted additive scoring contract, preserving all generated schemas and outputs.

## Earlier implementation evidence

Implemented on `feature/live-scam-screening`, based on clean `setup/entire-hackathon`
commit `c2fcef8`. Accepted requirements: [work order](live-scam-screening-task.md).
All evidence below was executed on 2026-10-10. No additional agents were spawned.

## Captured development

Before any edit, `entire session current` resolved to the executing Codex session
`01a125d9-e2b1-72a0-8cc0-147fdb1e4624`, active, with the current implementation prompt.
Brain status showed current HEAD/seed `c2fcef8` and a clean checkout. The task brief
retrieved the obsolete listener-only fact, explicitly superseded by this request.
`graft callers GradbotFactory --depth all` identified callers across CLI, app and tests.
The installed Graph CLI does not have the guide's `query` subcommand; the supported
`entire graph neighbors --symbol lookup_scam_number --direction both --depth 2`
resolved the dataset helper, callers, normalization and tests instead.

Implementation commit: `9529501e5ad57e376b0ae24709aa5b7cbf55a79a`.
`entire checkpoint list` and `entire checkpoint explain HEAD` confirmed checkpoint
`01M4JZCBPCFGQJWJMCXPRQNFMX` linked to that commit and the same captured session.
The commit carries the real `Entire-Checkpoint` trailer. `git ls-remote` confirmed
both the feature branch at that commit and remote checkpoint ref
`refs/entire/checkpoints/MX/01M4JZCBPCFGQJWJMCXPRQNFMX` at
`bcdbaae0b4cea32eeb9ac3033a2ff404a3d8af94`.

The first branch push failed with a remote pack-file error after successful Entire
checkpoint synchronization. The remote checkpoint existed but the branch did not.
One ordinary retry of `git push -u origin feature/live-scam-screening` succeeded;
no force push was used.

A single actual `entire trail create` attempt targeted the pushed branch with
`setup/entire-hackathon` as base. It returned `repository not found (status 404)`.
Trails access and GitHub App repository access are therefore not established by
this work. No Trail or hosted Runner evaluation was created or claimed. Local
capture, Graph, Brain and checkpoint synchronization work independently of that
external access limitation. Runner prompt changes preserve their JSON contracts.

## Initial implementation checks

| Command / check | Actual result |
| --- | --- |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | 21 files already formatted |
| `uv run mypy src` | Passed, 12 source files |
| `uv run pytest -q` | 97 passed; one upstream Starlette TestClient deprecation warning |
| `node --test tests/test_browser_audio.mjs` | 16 passed |
| `uv build` | Source distribution and wheel built |
| `uv run --with playwright python tests/browser_listener.py` | Passed in installed Chrome; 3 synthetic sessions; no page errors |
| `git diff --check` | Passed |
| Runner JSON comparison against `c2fcef8` excluding only `prompt.template` | All six schemas/contracts identical |
| SQLite comparison against `c2fcef8` | Byte-identical, 73,491 rows |
| Built wheel inspection | Includes byte-identical SQLite; contains no `.env` |

Dataset SHA-256: `036afc2befaf7161242a70b5cb2570035052aa048c468c8916162c7b211b6889`.
The dataset was neither downloaded nor rebuilt.

Initial Python coverage included strict finite complete criteria/aggregate validation, bounded
safe warning phrases, all threshold boundaries, quiet score-only operation, high scores
without warning text, independent warning threshold, repeated/rising/falling/jumping
email tiers, fixed idempotent retry payloads, unconfigured and failed email, malformed/
blocked/time-out Gemini, context accumulation/coalescing/clipping, real adapter wire
contracts with fake transport, last short STT fragments, queue overflow, same-origin/
Host/recipient restrictions, concurrency recovery, provider/media/capture timeouts and
immediate cancellation even while finalizing a last assessment.

The browser harness uses a real AudioWorklet with synthetic oscillators and injectable
fake providers. It verifies no provider start on silence, sound trigger, streaming
scores and exact 49/50/75/90 colors, all six fields, known-number metadata, no low-risk
playback, eligible warning playback, silence forwarding during simulated feedback,
bounded resumption, warning cancellation, mic/provider release, end/quiet rearm,
view switching, mobile layout, denied microphone, unavailable assessments and missing
readiness. Desktop/mobile screenshots in ignored `artifacts/` were visually inspected.
These screenshots show synthetic fixtures, not real call assessments.

## Initial real provider probes, separate from browser tests

`uv run scam-the-scamers doctor --live` used the existing private Gradium and Google
credentials without altering `.env`, the configured Google endpoint, or
`gemini-3.5-flash-lite`. The Google-compatible structured-output requests succeeded:

- Benign family lunch invitation: family claim 100, other criteria 0, overall 5,
  no warning text.
- Complete synthetic family-emergency scenario (arrest, distress, one-hour deadline,
  secrecy, CHF 5,000 cash and courier): all six criteria 100, overall 100, warning
  text present.
- Gradium generated 261,120 PCM bytes for a short synthetic diagnostic sentence.
  The same generated audio, resampled to 24 kHz, produced seven nonempty STT segments
  and completed EOS. Raw provider transcripts/audio were not saved or printed.

These are two functional model examples, not calibration, fraud detection accuracy or
real telephone results. They also do not establish end-to-end browser-to-live-provider
acoustic performance. **No real email was sent.** Resend tests use mock transport.

## Independent review and executed fixes

The parent independently verified Google's models endpoint returned HTTP 200 and
included the existing `gemini-3.5-flash-lite`; no credential values were logged.
Their successful real-provider Chrome run used 8.64 seconds of synthetic Gradium
speech, reached all six scores 100 and overall 100/critical, started warning playback
in muted Chrome, released mic/providers on pause and had zero page errors. Email was
disabled. Earlier runs exposed unavailable partial assessments, including revisions
5, 7 and 9; direct partial-phrase probes isolated rejected model aggregate arithmetic.
The parent's temporary armed-before-trigger harness issue was separately corrected;
it was not an application defect. This paragraph records parent-supplied evidence.

Before review edits, `entire session current` again resolved to the same active
session above and the current review prompt. The branch was clean at `2851ee6`.
Brain reported a current manifest; graft callers mapped `Assessment` and
`check_providers` before the changes. Existing task context was sufficient for this
follow-up, without repeating the earlier Brain brief or structural Graph lookup.

Gemini now returns only six strictly validated criterion scores and optional bounded
warning text. The server calculates the weighted sum, rounded to one decimal, and
serializes it as `overall`. The UI explicitly labels this **Calculated overall risk**
and separately identifies all six Gemini evidence scores. There is no model overall
field or arithmetic tolerance to fail. Missing, non-finite, out-of-range, blocked or
malformed criteria still fail validation. Colors, email tiers and warning eligibility
all use the same calculated total. High scores without warning text remain silent.
The validation/serialization split follows Pydantic's documented
[computed fields](https://docs.pydantic.dev/latest/concepts/fields/#the-computed_field-decorator);
Google's [compatible structured output](https://ai.google.dev/gemini-api/docs/openai#structured-output)
was rechecked, and the configured endpoint/model/credentials were preserved.

`doctor --live` now reports an aggregate passed/failed status and exits 1 for any
failed screening probe, missing transcription or cleanup failure; genuine success
exits 0. Injected tests cover credential rejection, timeout, TTS/STT setup/stream
failures, empty transcription, cleanup and successful probes. Diagnostics redact
raw exception details and cancel the paired audio tasks after a stream failure.
No email adapter is invoked. The full live doctor was not unnecessarily repeated.

| Review check | Actual result |
| --- | --- |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | 23 files already formatted |
| `uv run mypy src` | Passed, 12 source files |
| `uv run pytest -q` | 115 passed; one existing upstream TestClient deprecation warning |
| `node --test tests/test_browser_audio.mjs` | 16 passed |
| `uv build` | Source distribution and wheel built |
| `uv run --with playwright python tests/browser_listener.py` | Passed; 3 synthetic sessions, zero page errors |
| Runner comparison excluding `prompt.template`; `git diff --check` | Passed; contracts unchanged |

Two bounded real Gemini calls repeated exactly the reported failing partial phrases:

| Synthetic phrase/context | Family | Distress | Urgency | Secrecy | Financial | Courier | Calculated overall | Warning text |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| “I'm your daughter. I've been arrested. Bring five thousand francs within an hour.” | 100 | 100 | 100 | 0 | 100 | 0 | 60 | Present |
| Same phrase, followed by “Tell nobody.” | 100 | 100 | 100 | 100 | 100 | 0 | 80 | Present |

The new opt-in `tests/live_browser_listener.py --live` adapts the parent's harness,
requires explicit API usage, supplies only synthetic audio and forcibly clears all
Resend configuration. The first review rerun reached warning playback and displayed
`100 / 100`, but a new harness assertion incorrectly parsed that formatted string
as a float. After correcting the assertion, the complete command
`uv run --with playwright python tests/live_browser_listener.py --live` passed:

- 8.8 seconds of newly synthesized Gradium speech, streamed through real browser
  audio, backend, Gradium STT and Gemini.
- Six valid assessments and **zero unavailable assessments** through warning playback.
- Final six criteria all 100; calculated overall 100; critical UI color.
- Gradium warning TTS played through the browser scheduling path in muted Chrome.
- Pause released microphone tracks and the backend session; zero page errors.
- Resend disabled; no real email sent. Screenshot remains ignored in `artifacts/`.

These are functional synthetic examples, not calibration, a real phone call or proof
of audible/acoustic echo performance. The previous Trails API 404 remains an external
access limitation; no new Trail or hosted Runner evaluation is claimed or retried.

## Demo and remaining limits

1. Start the local server and show the optional caller input and fallback status.
   Without email credentials, the UI must say not configured.
2. Enable the listener beside a phone on speaker. A real phone test remains pending;
   alternatively use the synthetic Chrome harness for a reproducible offline demo.
3. For the requested synthetic scenario, speak the fictional family claim, arrest,
   urgency, secrecy, cash demand and courier pickup. Expect increasing evidence,
   not guaranteed specific intermediate scores. A family claim alone is insufficient.
4. Show the six indicators, overall rubric, lookup source/unknown states and actual
   provider events. Only a returned eligible caution produces speech. Stop a warning,
   switch tabs, then pause and demonstrate microphone release.
5. Show real Git checkpoint linkage, keeping it separate from unavailable hosted
   evaluations and from synthetic runtime results.

Still required for email: privately set `RESEND_API_KEY`, a Resend-verified
`ALERT_FROM_EMAIL`, and `FALLBACK_EMAIL`, restart, and separately authorize a real
delivery test. No inbox delivery has been verified. Provider API acceptance is not
inbox delivery. New listening sessions can send new alerts once configured.

Manual real-phone-on-speaker validation, acoustic echo behavior and real-world rubric
calibration remain open. The suppression guard intentionally misses speech overlapping
a warning. Bounded context retains opening and recent speech but can drop middle
context on long calls. These limitations are also disclosed in the README/UI.
