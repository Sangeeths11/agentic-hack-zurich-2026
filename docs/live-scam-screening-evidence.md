# Live scam screening: evidence and demo

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

## Executed checks

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

Python coverage includes strict finite complete criteria/aggregate validation, bounded
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

## Real provider probes, separate from browser tests

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
