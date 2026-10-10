#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v uv >/dev/null || { echo 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
uv sync --frozen --no-build-package gradbot
if [ ! -f .env ]; then
  (umask 077; cp .env.example .env)
  echo 'Created .env. Fill in the blank API credentials for the voice agent.'
fi
echo 'Setup complete. Next: edit .env, then scripts/run_local.sh.'
