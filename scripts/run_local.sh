#!/usr/bin/env bash
# Starts the app + tunnel and configures the number named in .env. Never places a call.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'Run scripts/setup.sh first.'; exit 1; }
CF="$(command -v cloudflared || true)"
[ -n "$CF" ] || CF='./.tools/cloudflared'
[ -x "$CF" ] || { echo 'Run scripts/setup.sh to install cloudflared.'; exit 1; }
[ -f .env ] || { echo 'Run scripts/setup.sh first.'; exit 1; }
# Validate all credentials are configured before opening the public tunnel.
PUBLIC_HTTP_URL=https://configuration-check.invalid uv run --frozen familyshield doctor
uv run --no-sync python - <<'PY'
import socket, sys
with socket.socket() as probe:
    try:
        probe.bind(('127.0.0.1', 8082))
    except OSError:
        sys.exit('Port 8082 is already occupied. Stop that service before starting this one.')
PY
TUNNEL_LOG="$(mktemp -t familyshield-tunnel.XXXXXX)"
pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
  rm -f "$TUNNEL_LOG"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$CF" tunnel --url http://127.0.0.1:8082 >"$TUNNEL_LOG" 2>&1 &
pids+=("$!")
PUBLIC_HTTP_URL=''
for _ in {1..45}; do
  kill -0 "${pids[0]}" 2>/dev/null || { cat "$TUNNEL_LOG"; exit 1; }
  PUBLIC_HTTP_URL="$(uv run --no-sync python - "$TUNNEL_LOG" <<'PY'
import pathlib, re, sys
match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', pathlib.Path(sys.argv[1]).read_text())
print(match.group() if match else '')
PY
)"
  [ -n "$PUBLIC_HTTP_URL" ] && break
  sleep 1
done
[ -n "$PUBLIC_HTTP_URL" ] || { cat "$TUNNEL_LOG"; exit 1; }
export PUBLIC_HTTP_URL
uv run --no-sync familyshield serve &
pids+=("$!")
for _ in {1..30}; do
  kill -0 "${pids[1]}" 2>/dev/null || { echo 'App exited. Check whether port 8082 is occupied.'; exit 1; }
  if uv run --no-sync python - <<'PY'
import httpx, sys
try:
    sys.exit(0 if httpx.get('http://127.0.0.1:8082/readyz', timeout=1).status_code == 200 else 1)
except httpx.HTTPError:
    sys.exit(1)
PY
  then break; fi
  sleep 1
done
uv run --no-sync python - <<'PY'
import httpx
httpx.get('http://127.0.0.1:8082/readyz', timeout=2).raise_for_status()
PY
uv run --no-sync familyshield configure-twilio
# Persist only the public URL so doctor/call-me from a second terminal use the same origin.
uv run --no-sync python - <<'PY'
import os
from dotenv import set_key
set_key('.env', 'PUBLIC_HTTP_URL', os.environ['PUBLIC_HTTP_URL'])
PY
echo "Agent ready at $PUBLIC_HTTP_URL. Call your Twilio number. Ctrl-C stops app + tunnel."
echo 'The configured Twilio webhook stays set to this tunnel until the next configure-twilio run.'
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do sleep 1; done
echo 'App or tunnel stopped.'
exit 1
