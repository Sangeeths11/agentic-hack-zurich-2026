#!/usr/bin/env bash
# Local microphone listener. No public tunnel or phone configuration.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'Run scripts/setup.sh first.'; exit 1; }
[ -f .env ] || { echo 'Run scripts/setup.sh first.'; exit 1; }
uv run --frozen scam-the-scamers doctor
echo 'Open http://localhost:8082 and activate the listener. Ctrl-C stops the server.'
exec uv run --no-sync scam-the-scamers serve
