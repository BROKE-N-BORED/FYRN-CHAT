#!/usr/bin/env bash
# Fyrn Chat Test installer: menu icon and a fyrn-chat-test
# launcher. The tool runs under Fyrn's own venv (PySide6 +
# llama-cpp-python are already there), so this installer creates
# no venv and downloads nothing. Fyrn must be installed first.
# Run with BASH (never sh). No terminal is needed to use the
# tool — double-click Install-Fyrn-Chat-Test instead.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "Installing Fyrn Chat Test..."

# Disk space, before anything is created: the tool itself is
# small, but the desktop files and launcher still need room.
# 100 MB is the floor. If free space cannot be measured, warn
# and carry on: an unmeasurable check must not block an install.
FREE_KB="$(df -P -k "$DIR" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
case "$FREE_KB" in
  ''|*[!0-9]*)
    echo "Could not check free disk space; continuing anyway."
    ;;
  *)
    if [ "$FREE_KB" -lt 102400 ]; then
      echo "Not enough free disk space to install Fyrn Chat Test."
      echo "It needs at least 100 MB free where it is installed."
      exit 1
    fi
    ;;
esac

# Find Fyrn: the launcher names its folder, and a search is the
# fallback (the same bounds as Fyrn's own launcher).
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

FYRN_DIR="$(find_fyrn)" || {
  echo "Fyrn is not installed on this PC, so Fyrn Chat Test has"
  echo "nothing to test. Install Fyrn first, then run this"
  echo "installer again."
  exit 1
}
if [ "$FYRN_DIR" = "MORE-THAN-ONE" ]; then
  echo "More than one Fyrn folder was found, so the installer"
  echo "does not know which Fyrn to test. Run the Fyrn you want"
  echo "from its own folder once, then run this installer again."
  exit 1
fi
echo "Found Fyrn at: $FYRN_DIR"

FYRN_PYTHON="$FYRN_DIR/.venv/bin/python"

# Fatal check: the packages must actually import before the
# installer reports success (a silent ModuleNotFoundError behind
# a menu icon is how installs die quietly).
if ! "$FYRN_PYTHON" - <<'PYEOF'
import sys
missing = []
try:
    import PySide6  # noqa: F401
except ImportError:
    missing.append("PySide6")
try:
    import llama_cpp  # noqa: F401
except ImportError:
    missing.append("llama-cpp-python")
if missing:
    print("Install failed: Fyrn's venv is missing: %s"
          % ", ".join(missing))
    sys.exit(1)
print("Packages present: PySide6, llama-cpp-python")
PYEOF
then
  exit 1
fi

# Warning only: can the GUI actually start on this machine's
# system Qt libraries? If not, name the failure — the install
# itself is still complete, and the message says what is missing.
if ! QT_QPA_PLATFORM=offscreen "$FYRN_PYTHON" -c "
from PySide6.QtWidgets import QApplication
import sys
app = QApplication(sys.argv)
" 2>"$DIR/.gui-smoke-error.txt"; then
  echo "WARNING: the Fyrn Chat Test window could not start here."
  echo "The install is complete, but the GUI failed its first start:"
  sed 's/^/  /' "$DIR/.gui-smoke-error.txt" | head -5
  echo "This usually means a system Qt library is missing."
  echo "Try: sudo apt install libegl1 libgl1"
fi
rm -f "$DIR/.gui-smoke-error.txt"

mkdir -p "$HOME/.local/share/applications" "$HOME/.local/bin"

# The desktop file starts the tool through the launcher in
# ~/.local/bin/fyrn-chat-test (not run.sh directly): the
# launcher finds the folder again if it moved, so the icon keeps
# working after a move. Desktop files do not expand $HOME, so
# the home folder is baked in too.
sed -e "s|__APP_DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" \
  Fyrn-Chat-Test.desktop \
  > "$HOME/.local/share/applications/Fyrn-Chat-Test.desktop"

if [ -d "$HOME/Desktop" ]; then
  cp "$HOME/.local/share/applications/Fyrn-Chat-Test.desktop" \
    "$HOME/Desktop/Fyrn-Chat-Test.desktop" || true
  chmod +x "$HOME/Desktop/Fyrn-Chat-Test.desktop" || true
  gio set "$HOME/Desktop/Fyrn-Chat-Test.desktop" \
    metadata::trusted true 2>/dev/null || true
fi

# The launcher, from the launcher.sh template: it starts the
# tool from this folder, and finds the folder again if it moved.
sed "s|__APP_DIR__|$DIR|g" launcher.sh \
  > "$HOME/.local/bin/fyrn-chat-test"
chmod +x "$HOME/.local/bin/fyrn-chat-test"

echo "Fyrn Chat Test installed."
echo "Start it from the Fyrn Chat Test icon, or run: fyrn-chat-test"
echo "If 'fyrn-chat-test' is not found, log out and back in once."

if [ "${FYRN_CHAT_TEST_GUI_INSTALL:-}" = "1" ]; then
  read -rp "Done. Press Enter to close."
fi
