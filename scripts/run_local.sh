#!/usr/bin/env bash
# Family Shield — run the Twilio bridge locally (macOS + Linux).
#
# Starts a cloudflare tunnel, writes the tunnel URL into .env as
# PUBLIC_HTTP_URL, points your Twilio number's voice webhook at it, then
# launches the FastAPI app. Ctrl-C stops both.
#
# Prereq: run scripts/setup.sh once first.
# Usage:  scripts/run_local.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE=".env"
PORT=8082
TUN_LOG=/tmp/familyshield_tunnel.log
APP_LOG=/tmp/familyshield_app.log

[ -f "$ENV_FILE" ] || { echo "no .env — run scripts/setup.sh first"; exit 1; }
command -v uv >/dev/null 2>&1 || { echo "uv not found — install from https://docs.astral.sh/uv/"; exit 1; }

CF="$(command -v cloudflared || true)"
[ -n "$CF" ] || CF="./.tools/cloudflared"
[ -x "$CF" ] || { echo "cloudflared not found — run scripts/setup.sh first"; exit 1; }

get_env(){ grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
set_env(){ # $1 key  $2 value
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import re, sys, pathlib
p = pathlib.Path(sys.argv[1]); k, v = sys.argv[2], sys.argv[3]; t = p.read_text()
pat = rf'(?m)^{re.escape(k)}=.*$'
t = re.sub(pat, f'{k}={v}', t) if re.search(pat, t) else t + f'\n{k}={v}\n'
p.write_text(t)
PY
}

pids=()
cleanup(){
  echo; echo "stopping…"
  for p in "${pids[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ---------- tunnel ----------
echo "==> starting cloudflare tunnel"
rm -f "$TUN_LOG"
"$CF" tunnel --url "http://localhost:$PORT" >"$TUN_LOG" 2>&1 &
pids+=($!)
URL=""
for _ in $(seq 1 30); do
  URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUN_LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "tunnel URL never appeared (see $TUN_LOG)"; exit 1; }
HOST="${URL#https://}"
echo "    tunnel: $URL"
set_env PUBLIC_HTTP_URL "https://$HOST"
echo "    wrote PUBLIC_HTTP_URL to .env"

# ---------- twilio webhook ----------
SID="$(get_env TWILIO_ACCOUNT_SID)"; TOK="$(get_env TWILIO_AUTH_TOKEN)"; NUM="$(get_env TWILIO_PHONE_NUMBER)"
if [ -n "$SID" ] && [ -n "$TOK" ] && [ -n "$NUM" ]; then
  echo "==> pointing Twilio $NUM voice webhook at the tunnel"
  PNSID="$(curl -s -u "$SID:$TOK" \
    "https://api.twilio.com/2010-04-01/Accounts/$SID/IncomingPhoneNumbers.json?PhoneNumber=$NUM" \
    | python3 -c 'import sys,json; l=json.load(sys.stdin).get("incoming_phone_numbers",[]); print(l[0]["sid"] if l else "")')"
  if [ -n "$PNSID" ]; then
    curl -s -o /dev/null -u "$SID:$TOK" \
      -d "VoiceUrl=https://$HOST/twilio/voice" -d "VoiceMethod=POST" \
      -d "StatusCallback=https://$HOST/twilio/status" -d "StatusCallbackMethod=POST" \
      "https://api.twilio.com/2010-04-01/Accounts/$SID/IncomingPhoneNumbers/$PNSID.json"
    echo "    $NUM -> https://$HOST/twilio/voice"
  else
    echo "    !! number $NUM not found on this Twilio account — webhook NOT set"
  fi
else
  echo "==> Twilio creds/number not set in .env — skipping webhook update"
fi

# ---------- app ----------
echo "==> starting app on :$PORT"
rm -f "$APP_LOG"
uv run uvicorn familyshield.main:app --app-dir src --host 0.0.0.0 --port "$PORT" >"$APP_LOG" 2>&1 &
pids+=($!)
healthy=0
for _ in $(seq 1 24); do
  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://localhost:$PORT/healthz" || true)"
  [ "$code" = "200" ] && { healthy=1; break; }
  sleep 2
done
[ "$healthy" = "1" ] && echo "    app healthy" || { echo "    app failed to start (see $APP_LOG)"; exit 1; }

cat <<EOF

Family Shield bridge is running.
  Public URL            : $URL
  Twilio voice webhook  : https://$HOST/twilio/voice
  Logs                  : tunnel=$TUN_LOG  app=$APP_LOG

Call your Twilio number now. Press Ctrl-C to stop everything.
EOF

wait
