# Family Shield

Agentic AI Hack Zurich, 10 October 2026. Family Shield screens phone calls
to older people before and during the call, confirms the caller's identity
with an enrolled relative, and lets a caregiver intervene if the
conversation turns risky. See the project brief for the full design.

## This phase: the Twilio foundation

Before the screening agent, we need a proven phone path: a real caller
dials the Twilio number, hears a greeting, and gets transferred to a real
phone. That's what's implemented here — nothing about claim extraction,
verification, or live protection yet.

- `POST /twilio/voice` — answers the call, speaks a greeting, dials
  `DESTINATION_PHONE_NUMBER`. Twilio's request signature is verified
  (`X-Twilio-Signature`), and the endpoint fails closed if `TWILIO_AUTH_TOKEN`
  isn't set.
- `POST /twilio/status` — logs call lifecycle events (ringing, answered,
  completed).
- `GET /healthz`

Next to build on top of this: the screening/confirmation flow replaces the
unconditional `dial()` in `twilio_routes.py` with routing logic, and a
`/twilio/stream` Media Streams WebSocket carries audio to the voice agent
(Gradium STT/TTS + an LLM for claim extraction, per `digigrad`'s
`bridge.py` pattern).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and a Twilio account with a
voice-enabled number.

```bash
scripts/setup.sh        # installs deps, downloads cloudflared, scaffolds .env
```

Edit `.env`:

- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` — from
  [console.twilio.com](https://console.twilio.com).
- `DESTINATION_PHONE_NUMBER` — the phone a test call should transfer to
  (stands in for "the protected person's phone" for now).

## Run

```bash
scripts/run_local.sh
```

This starts a cloudflare tunnel, points your Twilio number's voice webhook
at it automatically, and starts the app on port 8082. Call your Twilio
number — you should hear the greeting and be transferred to
`DESTINATION_PHONE_NUMBER`. Ctrl-C stops everything.

### Testing without Twilio credentials yet

Set `ALLOW_INSECURE_LOCAL=1` in `.env` to skip signature verification and
hit the webhook directly:

```bash
uv run uvicorn familyshield.main:app --app-dir src --port 8082 &
curl -X POST http://localhost:8082/twilio/voice -d "From=%2B41790000000"
```

Never set `ALLOW_INSECURE_LOCAL=1` on a publicly reachable host — it
disables Twilio's signature check, so anyone could POST to `/twilio/voice`
and make the bridge do whatever TwiML they want.

## Project layout

```
src/familyshield/
  main.py           FastAPI app, /healthz
  twilio_routes.py  /twilio/voice, /twilio/status, signature verification
  config.py         env var loading
scripts/
  setup.sh          one-time: deps, cloudflared, .env scaffold
  run_local.sh      tunnel + webhook config + run
```
