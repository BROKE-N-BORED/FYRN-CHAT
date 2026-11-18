#!/usr/bin/env bash
# Run Fyrn Chat Test from this folder, under Fyrn's venv.
# Install first with install.sh (or just double-click
# Install-Fyrn-Chat-Test).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

FYRN_DIR=""
if [ -f "$HOME/.local/bin/fyrn" ]; then
  FYRN_DIR="$(sed -n 's/^APP_DIR="\(.*\)"$/\1/p' \
    "$HOME/.local/bin/fyrn" | head -1)"
fi
if [ ! -x "$FYRN_DIR/.venv/bin/python" ]; then
  echo "Fyrn is not installed yet, so Fyrn Chat Test has nothing"
  echo "to test. Install Fyrn first."
  exit 1
fi

PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" \
  FYRN_INSTALL_DIR="$FYRN_DIR" \
  exec "$FYRN_DIR/.venv/bin/python" -m fyrnchattest "$@"
