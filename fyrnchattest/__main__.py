"""Entry point: python -m fyrnchattest opens the window.

A second launch raises the running window WITHOUT importing Qt:
the first instance holds an fcntl lock on
~/.fyrn-chat-test/instance.lock and listens on
~/.fyrn-chat-test/raise.sock, so a second process only has to
try the lock (it fails), connect to the socket, send "raise",
and exit. The same fast path Fyrn uses.

Nothing in this module imports Qt, or anything that imports Qt,
until the first instance is confirmed: the whole point is that
the second-launch path stays stdlib-only. Fyrn is found (stdlib
finder) and put on sys.path before the GUI imports anything
from the installed fyrn package.

When Fyrn is not installed, the tool says so plainly and stops:
a dialog when Qt is available, plain text on stderr when it is
not. The launcher normally catches this first (it needs Fyrn's
venv python to start at all), so this is the backstop for a
direct run.
"""

import fcntl
import os
import socket
import sys
import time
from pathlib import Path

# The first instance's lock file, kept open for the process
# lifetime: closing it releases the lock. A module-level list so
# the file object is never garbage-collected.
_FIRST_INSTANCE_LOCK = []


def _home_dir():
    from .finder import get_home
    return get_home()


def _try_raise_running():
    """Raise the running instance, if there is one.

    Returns True when another instance is already running (this
    process should exit). Returns False when this process is the
    first instance: it keeps the lock and must serve the socket
    for its lifetime (RaiseListener in fyrnchattest.gui, started
    by main()).
    """
    home = _home_dir()
    home.mkdir(parents=True, exist_ok=True)
    lock_file = open(home / "instance.lock", "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another instance holds the lock: ask it to raise. It
        # may still be starting (its listener starts after the
        # window exists), so retry briefly before giving up.
        lock_file.close()
        sock_path = str(home / "raise.sock")
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                client = socket.socket(socket.AF_UNIX,
                                       socket.SOCK_STREAM)
                client.settimeout(0.2)
                client.connect(sock_path)
                client.sendall(b"raise")
                client.close()
                return True
            except OSError:
                time.sleep(0.1)
        # The first instance holds the lock but never answered:
        # it is mid-startup and will appear on its own. Exit
        # anyway: a second window is worse than a missed raise.
        return True
    _FIRST_INSTANCE_LOCK.append(lock_file)
    return False


def _show_fatal(message):
    """Show a plain fatal message and return the exit code. A
    dialog when Qt is available (the usual case under Fyrn's
    venv), plain stderr when it is not."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "Fyrn Chat Test", message)
        del app
    except Exception:
        print(message, file=sys.stderr)
    return 1


def main_entry():
    """The real entry point, split out so tests can call the
    fast-path check without launching the GUI."""
    # A downloaded update waits here when the last run said no
    # to restarting: install it now, before the window exists,
    # then start fresh so the window runs the new code. This
    # process already imported the old package, so without the
    # restart it would report the old version and offer the
    # same update again. It runs before the single-instance
    # lock, so no lock is held across the restart.
    from .updates import apply_staged_update
    applied = apply_staged_update()
    if applied is not None:
        try:
            os.execv(sys.executable,
                     [sys.executable, "-m", "fyrnchattest"]
                     + sys.argv[1:])
        except OSError as exc:
            print("Fyrn Chat Test: the update was installed, "
                  "but the app could not restart itself (%s). "
                  "Close it and start it again." % exc,
                  file=sys.stderr)
    if _try_raise_running():
        return 0
    # First instance: find Fyrn before anything Qt is imported.
    from .finder import (FyrnAmbiguousError, FyrnNotFoundError,
                         find_fyrn_install)
    try:
        install = find_fyrn_install()
    except (FyrnNotFoundError, FyrnAmbiguousError) as exc:
        return _show_fatal(str(exc))
    # The installed Fyrn goes on sys.path, ahead of anything
    # else: the tool imports the Fyrn that is actually
    # installed, never a stray copy.
    sys.path.insert(0, str(install.folder))
    # Only now import Qt (the slow part).
    from .gui import main
    return main(install)


if __name__ == "__main__":
    raise SystemExit(main_entry())
