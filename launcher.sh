#!/usr/bin/env bash
# Fyrn Chat Test launcher: installed to
# ~/.local/bin/fyrn-chat-test by install.sh, which fills in
# __APP_DIR__ with the folder the tool was installed from. It
# starts the tool from that folder, finds Fyrn's venv python to
# run under, and if the tool folder moved or was renamed, it
# looks for the new home before giving up, so the desktop icon
# keeps working after a move.
set -euo pipefail
APP_DIR="__APP_DIR__"

# A Fyrn install is a folder with install.sh, the fyrn package,
# and a venv. Prints the folder, or nothing.
find_fyrn() {
  local dir=""
  if [ -f "$HOME/.local/bin/fyrn" ]; then
    dir="$(sed -n 's/^APP_DIR="\(.*\)"$/\1/p' \
      "$HOME/.local/bin/fyrn" | head -1)"
    if [ -f "$dir/install.sh" ] \
        && [ -f "$dir/fyrn/__init__.py" ] \
        && [ -x "$dir/.venv/bin/python" ]; then
      printf '%s' "$dir"
      return 0
    fi
  fi
  local found=""
  local count=0
  while IFS= read -r -d '' init; do
    dir="$(dirname "$(dirname "$init")")"
    if [ -f "$dir/install.sh" ] \
        && [ -x "$dir/.venv/bin/python" ]; then
      found="$dir"
      count=$((count + 1))
    fi
  done < <(find "$HOME" -maxdepth 6 -type d -name '.*' -prune -o \
    -type f -path '*/fyrn/__init__.py' -print0 2>/dev/null)
  if [ "$count" -eq 1 ]; then
    printf '%s' "$found"
    return 0
  fi
  if [ "$count" -gt 1 ]; then
    echo "MORE-THAN-ONE"
    return 0
  fi
  return 1
}

fail_plain() {
  if command -v zenity >/dev/null 2>&1; then
    zenity --error --title="Fyrn Chat Test" --text="$1" 2>/dev/null
  else
    echo "$1" >&2
  fi
  exit 1
}

# The tool folder moved. Look for it: a tool install is a folder
# with install.sh and the fyrnchattest package. Hidden folders
# are skipped (the search stays in the user's own files).
if [ ! -f "$APP_DIR/fyrnchattest/__init__.py" ]; then
  CANDIDATES=()
  while IFS= read -r -d '' init; do
    dir="$(dirname "$(dirname "$init")")"
    if [ -f "$dir/install.sh" ]; then
      CANDIDATES+=("$dir")
    fi
  done < <(find "$HOME" -maxdepth 6 -type d -name '.*' -prune -o \
    -type f -path '*/fyrnchattest/__init__.py' -print0 2>/dev/null)

  LAUNCHER="$(readlink -f "${BASH_SOURCE[0]}")"

  if [ "${#CANDIDATES[@]}" -eq 1 ]; then
    # Exactly one tool folder: that is the move. Point the
    # desktop icons at the new folder, rewrite this launcher to
    # the new folder, and start from there.
    NEW_DIR="${CANDIDATES[0]}"
    # Escape the folder for sed's replacement side (&, |, and "
    # would otherwise break the rewrite or the quoted assignment).
    ESCAPED_DIR="${NEW_DIR//&/\\&}"
    ESCAPED_DIR="${ESCAPED_DIR//|/\\|}"
    ESCAPED_DIR="${ESCAPED_DIR//\"/\\\"}"
    for desktop in \
        "$HOME/.local/share/applications/Fyrn-Chat-Test.desktop" \
        "$HOME/Desktop/Fyrn-Chat-Test.desktop"; do
      if [ -f "$desktop" ]; then
        sed -i "s|^Icon=.*|Icon=$ESCAPED_DIR/assets/fyrn-chat-test-48.png|" \
          "$desktop"
      fi
    done
    # Rewrite via a temp file and mv, then exec the new launcher
    # straight away: bash reads a running script incrementally,
    # so nothing may run from this file after the overwrite.
    NEW_LAUNCHER="$(mktemp)"
    sed "s|^APP_DIR=.*|APP_DIR=\"$ESCAPED_DIR\"|" "$LAUNCHER" \
      > "$NEW_LAUNCHER"
    chmod +x "$NEW_LAUNCHER"
    mv "$NEW_LAUNCHER" "$LAUNCHER"
    exec "$LAUNCHER" "$@"
  fi

  if [ "${#CANDIDATES[@]}" -eq 0 ]; then
    fail_plain "Fyrn Chat Test moved, and the launcher could not find its new folder. Run bash install.sh again from the Fyrn Chat Test folder."
  else
    fail_plain "Fyrn Chat Test moved, and more than one Fyrn Chat Test folder was found, so the launcher does not know which one to start. Run bash install.sh again from the folder you want."
  fi
fi

# The tool folder is where it was. Find Fyrn to run under.
FYRN_DIR="$(find_fyrn)" || fail_plain "Fyrn is not installed on this PC, so Fyrn Chat Test has nothing to test. Install Fyrn first."
if [ "$FYRN_DIR" = "MORE-THAN-ONE" ]; then
  fail_plain "More than one Fyrn folder was found, so Fyrn Chat Test does not know which Fyrn to test. Run the Fyrn you want from its own folder once, then try again."
fi

# PYTHONPATH points at the tool folder: without it, the package
# only works when the terminal happens to be in the tool folder.
# FYRN_INSTALL_DIR tells the tool which Fyrn it is testing.
PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  FYRN_INSTALL_DIR="$FYRN_DIR" \
  exec "$FYRN_DIR/.venv/bin/python" -m fyrnchattest "$@"
