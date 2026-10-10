#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
uv sync --frozen --no-build-package gradbot
if [ ! -f .env ]; then
  (umask 077; cp .env.example .env)
  echo 'Created .env. Fill in the blank API credentials for live calls.'
fi
uv run --no-sync familyshield simulate
uv run --no-sync python scripts/install_tunnel.py
echo 'Setup verified. Next: edit .env, then scripts/run_local.sh.'
