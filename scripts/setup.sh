#!/usr/bin/env bash
# Family Shield — one-time local setup.
#
#   • installs deps into ./.venv via uv
#   • downloads cloudflared into ./.tools (project-local; no system change)
#     unless it's already on PATH
#   • scaffolds .env from .env.example (never overwrites an existing .env)
#
# Usage: scripts/setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v uv >/dev/null 2>&1 || { echo "uv not found — install from https://docs.astral.sh/uv/"; exit 1; }

echo "==> syncing dependencies (uv sync)"
uv sync

echo "==> cloudflared (tunnel so Twilio can reach your machine)"
TOOLS_DIR="$REPO_ROOT/.tools"
if command -v cloudflared >/dev/null 2>&1; then
  echo "    present on PATH: $(command -v cloudflared)"
else
  mkdir -p "$TOOLS_DIR"
  CF="$TOOLS_DIR/cloudflared"
  if [ -x "$CF" ] && "$CF" --version >/dev/null 2>&1; then
    echo "    present at $CF (project-local)"
  else
    PLAT="$(uname -s)"
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64|amd64)  cfarch=amd64 ;;
      arm64|aarch64) cfarch=arm64 ;;
      *)             cfarch=amd64 ;;
    esac
    base="https://github.com/cloudflare/cloudflared/releases/latest/download"
    echo "    downloading cloudflared into $TOOLS_DIR/ — nothing installed system-wide"
    if [ "$PLAT" = "Darwin" ]; then
      curl -fsSL "$base/cloudflared-darwin-${cfarch}.tgz" -o "$TOOLS_DIR/cf.tgz"
      tar -xzf "$TOOLS_DIR/cf.tgz" -C "$TOOLS_DIR"
      rm -f "$TOOLS_DIR/cf.tgz"
    else
      curl -fsSL "$base/cloudflared-linux-${cfarch}" -o "$CF"
    fi
    chmod +x "$CF"
    "$CF" --version >/dev/null 2>&1 || { echo "cloudflared download failed"; exit 1; }
    echo "    ready at $CF"
  fi
fi

if [ -f ".env" ]; then
  echo "==> .env already exists — leaving it alone"
else
  echo "==> scaffolding .env from .env.example"
  cp .env.example .env
  echo "    fill in TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER / DESTINATION_PHONE_NUMBER"
fi

cat <<EOF

Setup done. Next:
  1. Edit .env with your Twilio credentials and numbers.
  2. scripts/run_local.sh
EOF
