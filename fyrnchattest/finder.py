"""Finding the Fyrn install on this PC.

Fyrn Chat Test runs on the Fyrn that is actually installed: it
finds the install, runs under Fyrn's venv, and imports the
installed fyrn package. A Fyrn install is a folder with
install.sh, the fyrn package, and a venv.

The search order:
  1. FYRN_INSTALL_DIR, when set (the launcher passes it, and it
     is the escape hatch when more than one Fyrn folder exists).
  2. The ~/.local/bin/fyrn launcher, which names its folder in
     an APP_DIR line.
  3. A search of the home folder (hidden folders pruned, 6
     levels deep, the same bounds as Fyrn's own launcher).

Standard library only, so the launcher check and the tests run
without Qt. Nothing here writes anything: finding Fyrn never
modifies Fyrn.
"""

import os
import re
from pathlib import Path

# How deep the home-folder search goes (Fyrn's launcher uses the
# same bound: deep enough for a folder inside Documents, shallow
# enough to stay quick).
SEARCH_MAX_DEPTH = 6

# The launcher's APP_DIR line: APP_DIR="/home/joshua/fyrn"
_APP_DIR_RE = re.compile(r'^APP_DIR="([^"]*)"\s*$', re.MULTILINE)


class FyrnNotFoundError(Exception):
    """No Fyrn install could be found on this PC."""


class FyrnAmbiguousError(Exception):
    """More than one Fyrn install was found, and the tool will
    not guess which one to test."""

    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = list(candidates)


class FyrnInstall:
    """A Fyrn install: its folder, its venv python, its version."""

    def __init__(self, folder, python, version):
        self.folder = Path(folder)
        self.python = Path(python)
        self.version = version

    def __repr__(self):
        return "FyrnInstall(%s, version=%s)" % (self.folder,
                                                self.version)


def _read_version(folder):
    """Fyrn's version, read from its __init__.py without importing
    it (importing needs the venv on the path, which the finder
    does not assume). "unknown" when the file cannot be read."""
    try:
        text = (Path(folder) / "fyrn" / "__init__.py").read_text(
            encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def _is_fyrn_install(folder):
    """True when a folder is a usable Fyrn install: install.sh,
    the fyrn package, and a venv python that runs."""
    folder = Path(folder)
    return (
        (folder / "install.sh").is_file()
        and (folder / "fyrn" / "__init__.py").is_file()
        and os.access(folder / ".venv" / "bin" / "python", os.X_OK)
    )


def _install_from(folder):
    """A FyrnInstall for a folder already checked by
    _is_fyrn_install, or None."""
    folder = Path(folder)
    if not _is_fyrn_install(folder):
        return None
    return FyrnInstall(folder, folder / ".venv" / "bin" / "python",
                       _read_version(folder))


def _launcher_app_dir(home):
    """The folder named in the ~/.local/bin/fyrn launcher, or
    None when there is no launcher or no APP_DIR line in it."""
    launcher = Path(home) / ".local" / "bin" / "fyrn"
    try:
        text = launcher.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _APP_DIR_RE.search(text)
    return match.group(1) if match else None


def _search_home(home):
    """Every Fyrn install under the home folder, in a stable
    order: hidden folders pruned, 6 levels deep. A folder that
    cannot be listed is skipped, never fatal."""
    home = Path(home)
    found = []
    base_depth = len(home.parts)
    for root, dirs, _files in os.walk(home):
        depth = len(Path(root).parts) - base_depth
        # Prune hidden folders in place, and stop descending
        # past the depth bound.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if depth >= SEARCH_MAX_DEPTH:
            dirs[:] = []
        if _is_fyrn_install(root):
            found.append(Path(root))
            # A Fyrn install never contains another one: do not
            # descend into it.
            dirs[:] = []
    return sorted(found)


def find_fyrn_install(home=None, environ=None):
    """Find the Fyrn install on this PC.

    home defaults to the user's home folder; environ defaults to
    os.environ (both injectable so tests can use a fake home).
    Raises FyrnNotFoundError when nothing is found, and
    FyrnAmbiguousError when the search finds more than one.
    """
    home = Path(home) if home is not None else Path.home()
    env = environ if environ is not None else os.environ
    # 1. The explicit override: the launcher passes it, and it is
    # the way out when more than one Fyrn folder exists.
    override = env.get("FYRN_INSTALL_DIR")
    if override:
        install = _install_from(override)
        if install is not None:
            return install
        raise FyrnNotFoundError(
            "FYRN_INSTALL_DIR is set to %s, but that folder is "
            "not a Fyrn install (it needs install.sh, the fyrn "
            "package, and a venv)." % override)
    # 2. The Fyrn launcher names its folder.
    app_dir = _launcher_app_dir(home)
    if app_dir:
        install = _install_from(app_dir)
        if install is not None:
            return install
        # A stale launcher (Fyrn moved, never re-launched) falls
        # through to the search instead of failing outright.
    # 3. Search the home folder.
    candidates = _search_home(home)
    if len(candidates) == 1:
        return _install_from(candidates[0])
    if len(candidates) > 1:
        raise FyrnAmbiguousError(
            "More than one Fyrn folder was found, so the test "
            "does not know which Fyrn to test:\n%s\n\nRun the "
            "Fyrn you want from its own folder once (its "
            "launcher then points at it), or set FYRN_INSTALL_DIR "
            "to the folder to test."
            % "\n".join("  %s" % c for c in candidates),
            candidates)
    raise FyrnNotFoundError(
        "Fyrn is not installed on this PC, so there is nothing "
        "to test. Install Fyrn first, then run Fyrn Chat Test "
        "again.")


def get_home():
    """This tool's home folder (logs, lock file, reports).

    FYRN_CHAT_TEST_HOME overrides it; tests point it at a temp
    folder so they never touch the real home. It is the tool's
    own folder, never Fyrn's: the tool keeps its reports and
    logs separate from the thing it tests.
    """
    override = os.environ.get("FYRN_CHAT_TEST_HOME")
    if override:
        return Path(override)
    return Path.home() / ".fyrn-chat-test"


def ensure_home():
    """Create the home folder (plus logs and reports) if needed."""
    home = get_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "reports").mkdir(parents=True, exist_ok=True)
    return home


def fyrn_is_running(home=None):
    """True when Fyrn itself is running right now: it holds an
    exclusive lock on ~/.fyrn/instance.lock while it runs.

    Only checked when the lock file already exists: a missing
    file means Fyrn has never run, and creating it here would
    write into Fyrn's home folder, which this tool never does.
    """
    import fcntl

    home = Path(home) if home is not None else Path.home()
    lock_path = home / ".fyrn" / "instance.lock"
    if not lock_path.exists():
        return False
    try:
        handle = open(lock_path, "a+")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        # The lock releases when the handle closes; the file
        # itself is Fyrn's and is left exactly as it was.
        handle.close()
    return False
