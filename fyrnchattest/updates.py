"""Silent update check, download, verify, install, restart.

On launch Fyrn Chat Test quietly asks GitHub's public releases
endpoint for the newest release and compares its tag against
the running version. Up to date, nothing happens. A newer
release gets a popup asking whether to download it: never a
silent download. The download is checked against the release's
.sha256 sidecar before anything is installed, and after the
download a popup asks whether to restart now to finish the
update.

The deferred restart is honest staging, not a re-download:
when the user says no to restarting, the downloaded, verified
zip stays in ~/.fyrn-chat-test/updates/ with a marker naming
its version and checksum, and the next launch installs it
before the window shows (apply_staged_update, called from
__main__ before the single-instance lock) and restarts the
process fresh, so the window runs the new code and reports the
new version. Saying no to the download itself stages nothing:
the next launch just checks again.

Standard library only, so the check and the staged install run
without Qt. check_for_update never raises for a network or
parsing problem: a failed check comes back as state "unknown"
with a plain reason, so the GUI can stay silent instead of
crashing. The version compare is a straight numeric tuple
compare, the same shape as Pixel Forge's.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .finder import get_home

REPO = "BROKE-N-BORED/FYRN-CHAT"
LATEST_RELEASE_URL = ("https://api.github.com/repos/%s/releases/latest"
                      % REPO)
RELEASES_PAGE_URL = "https://github.com/%s/releases" % REPO
APP_ID = "fyrn-chat-test"
# The zip's top folder, from build_zip.py: every member lives
# under fyrn-chat-test/.
ZIP_PREFIX = APP_ID + "/"
# The unpacked tree must hold these, or it is not an update.
REQUIRED_FILES = ("fyrnchattest/__init__.py", "fyrnchattest/gui.py")
CHECK_TIMEOUT_S = 8
# Per socket read: a 64 KB chunk on any working connection
# arrives in well under this, and it bounds how long a cancel
# can wait on a hung download.
DOWNLOAD_TIMEOUT_S = 10
STAGED_MARKER = "staged.json"

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(Exception):
    """An update step failed, in plain words."""


class UpdateCancelled(Exception):
    """The download was stopped (the window closed mid-pull)."""


def latest_release_url():
    """The releases endpoint to check. FYRN_CHAT_TEST_UPDATE_URL
    overrides it: a test seam, so the GUI drive can point the
    check at a local server instead of GitHub."""
    return (os.environ.get("FYRN_CHAT_TEST_UPDATE_URL")
            or LATEST_RELEASE_URL)


def app_dir():
    """The folder the app is installed in: the folder holding
    the fyrnchattest package. FYRN_CHAT_TEST_APP_DIR overrides
    it: a test seam, so tests install into a temp folder and
    never touch the real tree."""
    override = os.environ.get("FYRN_CHAT_TEST_APP_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def staged_dir(home=None):
    """Where a downloaded, verified update waits: the tool's own
    home folder, updates/."""
    if home is None:
        home = get_home()
    return Path(home) / "updates"


def parse_version(text):
    """A version string as a tuple of ints: "0.1.1" becomes
    (0, 1, 1), "v0.1.1" the same. None when it is not a version
    at all: anything but an optional "v" followed by numbers
    and dots is refused, so a tag like "v0.1.1-beta" reads as no
    usable version rather than a wrong one."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest, current):
    """True when the latest version is newer than the running
    one. Either side unparsable means no: an unknown version is
    never reported as an update. Shorter tuples are padded with
    zeros, so 0.1 and 0.1.0 compare equal."""
    latest_parsed = parse_version(latest)
    current_parsed = parse_version(current)
    if latest_parsed is None or current_parsed is None:
        return False
    width = max(len(latest_parsed), len(current_parsed))
    latest_padded = latest_parsed + (0,) * (width - len(latest_parsed))
    current_padded = current_parsed + (0,) * (width - len(current_parsed))
    return latest_padded > current_padded


def fetch_latest_release(urlopen=None):
    """The latest release from GitHub's public endpoint, as
    {"version", "url", "zip_url", "sidecar_url", "zip_name"}.
    Raises UpdateError with a plain reason when the network
    fails, the answer is not JSON, it carries no tag, or the
    release has no zip and sidecar to download: the caller
    turns every one of those into "unknown", never a crash.
    urlopen is a test seam: pass a stub and no network is
    touched."""
    opener = urlopen or urllib.request.urlopen
    try:
        with opener(latest_release_url(),
                    timeout=CHECK_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise UpdateError("the update check timed out")
        raise UpdateError("could not reach GitHub: %s" % reason)
    except TimeoutError:
        raise UpdateError("the update check timed out")
    except (ValueError, UnicodeDecodeError):
        raise UpdateError(
            "the releases page did not answer in a form "
            "Fyrn Chat Test understands")
    if not isinstance(payload, dict):
        raise UpdateError(
            "the releases page did not answer in a form "
            "Fyrn Chat Test understands")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise UpdateError(
            "the releases page did not name a version")
    version = parse_version(tag)
    if version is None:
        raise UpdateError(
            "the latest release tag did not look like a version: %s"
            % tag)
    # The release process publishes the zip and its .sha256
    # sidecar as assets, named after the tag.
    zip_name = "%s-%s.zip" % (APP_ID, tag)
    sidecar_name = zip_name + ".sha256"
    assets = payload.get("assets")
    by_name = {}
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict) \
                    and isinstance(asset.get("name"), str):
                by_name[asset["name"]] = asset.get(
                    "browser_download_url")
    zip_url = by_name.get(zip_name)
    sidecar_url = by_name.get(sidecar_name)
    if not isinstance(zip_url, str) or not zip_url \
            or not isinstance(sidecar_url, str) or not sidecar_url:
        raise UpdateError(
            "the latest release has no download files yet")
    url = payload.get("html_url") or RELEASES_PAGE_URL
    return {"version": ".".join(str(part) for part in version),
            "url": url, "zip_url": zip_url,
            "sidecar_url": sidecar_url, "zip_name": zip_name}


def check_for_update(current_version, urlopen=None):
    """One call, three outcomes, never an exception for a
    network or parsing problem:

    {"state": "newer", "latest": "0.1.1", "url": "<release>",
     "zip_url": ..., "sidecar_url": ..., "zip_name": ...} — a
     newer release is out, with its download.
    {"state": "current", "latest": "0.1", "url": "<release>"} —
     the running version is the newest (or newer than the
     latest release, a dev tree).
    {"state": "unknown", "latest": None, "url": None,
     "reason": "<plain words>"} — the check could not run.
    """
    try:
        release = fetch_latest_release(urlopen=urlopen)
    except UpdateError as exc:
        return {"state": "unknown", "latest": None, "url": None,
                "reason": str(exc)}
    except Exception:  # noqa: BLE001 - a check never crashes
        return {"state": "unknown", "latest": None, "url": None,
                "reason": "the update check could not run"}
    if is_newer(release["version"], current_version):
        return {"state": "newer", "latest": release["version"],
                "url": release["url"],
                "zip_url": release["zip_url"],
                "sidecar_url": release["sidecar_url"],
                "zip_name": release["zip_name"]}
    return {"state": "current", "latest": release["version"],
            "url": release["url"]}


def _read_response(url, urlopen=None):
    """Open a URL and return the response. Raises UpdateError
    in plain words when the network fails. The caller closes
    the response."""
    opener = urlopen or urllib.request.urlopen
    try:
        return opener(url, timeout=DOWNLOAD_TIMEOUT_S)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise UpdateError("the download timed out")
        raise UpdateError("could not reach GitHub: %s" % reason)
    except TimeoutError:
        raise UpdateError("the download timed out")
    except (OSError, ValueError) as exc:
        raise UpdateError("could not download the update (%s)"
                          % exc)


def fetch_sidecar_sha256(url, urlopen=None):
    """The expected SHA-256 from a .sha256 sidecar: the first
    word of the file, which must be 64 hex digits."""
    try:
        with _read_response(url, urlopen=urlopen) as response:
            text = response.read().decode("utf-8", errors="replace")
    except UpdateError:
        raise
    except (OSError, ValueError) as exc:
        raise UpdateError("could not read the checksum (%s)" % exc)
    words = text.strip().split()
    if not words or _SHA256_RE.match(words[0]) is None:
        raise UpdateError("the checksum file did not look right")
    return words[0].lower()


def sha256_of(path):
    """A file's SHA-256 hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path, expected_hex):
    """True when the file's SHA-256 matches the expected hex. A
    file that cannot be read does not match."""
    try:
        return sha256_of(path) == expected_hex.lower()
    except OSError:
        return False


def download_file(url, dest, urlopen=None, progress_cb=None,
                  cancel_event=None):
    """Download a URL to dest, chunked, with progress (bytes
    downloaded, total bytes or None) and a cancel. The download
    lands beside dest first and moves into place only when it
    is whole: a failed or cancelled download leaves no partial
    file behind. Raises UpdateError, or UpdateCancelled when
    the cancel fired."""
    dest = Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError("could not save the download (%s)" % exc)
    tmp = Path(str(dest) + ".part")
    try:
        with _read_response(url, urlopen=urlopen) as response:
            total = None
            headers = getattr(response, "headers", None)
            if headers is not None:
                try:
                    length = headers.get("Content-Length")
                except Exception:  # noqa: BLE001 - no total then
                    length = None
                if length:
                    try:
                        total = int(length)
                    except (TypeError, ValueError):
                        total = None
            downloaded = 0
            if progress_cb is not None:
                progress_cb(0, total)
            with open(tmp, "wb") as fh:
                while True:
                    if cancel_event is not None \
                            and cancel_event.is_set():
                        raise UpdateCancelled()
                    try:
                        chunk = response.read(65536)
                    except (OSError, ValueError) as exc:
                        raise UpdateError(
                            "the download stopped (%s)" % exc)
                    if not chunk:
                        break
                    try:
                        fh.write(chunk)
                    except OSError as exc:
                        raise UpdateError(
                            "could not save the download (%s)"
                            % exc)
                    downloaded += len(chunk)
                    if progress_cb is not None:
                        progress_cb(downloaded, total)
    except (UpdateError, UpdateCancelled):
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    except OSError as exc:
        # The .part file could not be created.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise UpdateError("could not save the download (%s)" % exc)
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise UpdateError("could not save the download (%s)" % exc)
    return dest


def download_and_verify(release, dest_dir, urlopen=None,
                        progress_cb=None, cancel_event=None):
    """Download a release's zip and sidecar into dest_dir,
    verify the zip against the sidecar, and write the staged
    marker naming the version and checksum. Returns the
    verified zip path. A mismatch deletes the zip and raises
    UpdateError: a download that fails its checksum is never
    installed, never staged."""
    dest_dir = Path(dest_dir)
    # The version arrives as "version" from a release record or
    # as "latest" from a check_for_update result: both are the
    # same version, and either key is accepted.
    version = release.get("version") or release.get("latest")
    if not version:
        raise UpdateError("the release did not name a version")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError("could not save the download (%s)" % exc)
    expected = fetch_sidecar_sha256(release["sidecar_url"],
                                    urlopen=urlopen)
    zip_path = dest_dir / release["zip_name"]
    download_file(release["zip_url"], zip_path, urlopen=urlopen,
                  progress_cb=progress_cb,
                  cancel_event=cancel_event)
    if not verify_file(zip_path, expected):
        try:
            zip_path.unlink()
        except OSError:
            pass
        raise UpdateError(
            "the download did not match its checksum, so it was "
            "not installed. Try again later.")
    marker = {"version": version,
              "zip": release["zip_name"], "sha256": expected}
    try:
        (dest_dir / STAGED_MARKER).write_text(
            json.dumps(marker), encoding="utf-8")
    except OSError as exc:
        try:
            zip_path.unlink()
        except OSError:
            pass
        raise UpdateError("could not save the download (%s)" % exc)
    return zip_path


def install_update(zip_path, folder=None):
    """Replace the installed app files with a release zip's
    contents. The zip unpacks to a temp folder first, and the
    unpacked tree must look like Fyrn Chat Test, so a bad zip
    never leaves the app half-updated. Stored file modes are
    kept (install.sh stays executable). Stale bytecode goes,
    so the next start compiles fresh. folder defaults to the
    installed app folder (app_dir()). Raises UpdateError with
    a plain reason on any failure. Safe while the app runs:
    Linux lets a running process's files be replaced under it,
    and the restart that follows loads the new code."""
    folder = Path(folder) if folder is not None else app_dir()
    zip_path = Path(zip_path)
    try:
        tmp_root = Path(tempfile.mkdtemp(prefix="fyrnct-update-"))
    except OSError as exc:
        raise UpdateError("could not unpack the update (%s)" % exc)
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    name = info.filename
                    if not name.startswith(ZIP_PREFIX):
                        continue
                    rel = name[len(ZIP_PREFIX):]
                    if not rel or rel.endswith("/"):
                        continue
                    # No escapes: nothing outside the app folder.
                    if rel.startswith("/") \
                            or ".." in Path(rel).parts:
                        raise UpdateError(
                            "the update file did not look right")
                    target = tmp_root / rel
                    target.parent.mkdir(parents=True,
                                        exist_ok=True)
                    with zf.open(info) as src, \
                            open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, 65536)
                    mode = info.external_attr >> 16
                    if mode:
                        os.chmod(target, mode & 0o7777)
        except (zipfile.BadZipFile, NotImplementedError):
            # BadZipFile: not a zip, or a bad CRC. NotImplemented-
            # Error: a compression the zip reader cannot do.
            raise UpdateError(
                "the update file was not a valid download")
        except UpdateError:
            raise
        except OSError as exc:
            raise UpdateError("could not unpack the update (%s)"
                              % exc)
        for required in REQUIRED_FILES:
            if not (tmp_root / required).is_file():
                raise UpdateError(
                    "the update file did not look like "
                    "Fyrn Chat Test")
        try:
            for path in sorted(tmp_root.rglob("*")):
                if not path.is_file():
                    continue
                target = folder / path.relative_to(tmp_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                os.chmod(target, path.stat().st_mode & 0o7777)
        except OSError as exc:
            raise UpdateError("could not install the update (%s)"
                              % exc)
        for cache in (folder / "fyrnchattest" / "__pycache__",
                      folder / "tests" / "__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def clear_staged(home=None):
    """Remove a staged update, if there is one."""
    shutil.rmtree(staged_dir(home), ignore_errors=True)


def apply_staged_update(home=None, app_dir=None):
    """Install a staged update (a verified zip waiting in the
    updates folder), and clean up after it. Returns the version
    applied, or None when nothing is staged. A staged update
    that fails its checksum, or fails to install, is removed
    and the old version keeps running: the failure is printed
    to stderr (the debug log is not up yet at launch) and the
    next launch's check offers the update again. Never raises:
    launch must never die on an update."""
    staged = staged_dir(home)
    marker_path = staged / STAGED_MARKER
    try:
        marker = json.loads(marker_path.read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        # No marker, nothing staged. A zip without a marker is
        # an interrupted download: clear it, so it never
        # installs half-staged.
        for stray in staged.glob("*.zip*"):
            try:
                stray.unlink()
            except OSError:
                pass
        return None
    if not isinstance(marker, dict):
        clear_staged(home)
        return None
    zip_name = marker.get("zip")
    expected = marker.get("sha256")
    version = marker.get("version")
    if not isinstance(zip_name, str) or not zip_name \
            or "/" in zip_name or ".." in Path(zip_name).parts \
            or not isinstance(expected, str) \
            or _SHA256_RE.match(expected) is None:
        clear_staged(home)
        return None
    zip_path = staged / zip_name
    if not zip_path.is_file():
        clear_staged(home)
        return None
    if not verify_file(zip_path, expected):
        print("Fyrn Chat Test: the downloaded update did not "
              "match its checksum, so it was not installed.",
              file=sys.stderr)
        clear_staged(home)
        return None
    try:
        install_update(zip_path, app_dir)
    except UpdateError as exc:
        print("Fyrn Chat Test: the downloaded update could not "
              "be installed (%s). The old version keeps running."
              % exc, file=sys.stderr)
        clear_staged(home)
        return None
    clear_staged(home)
    return version


def _find_launcher():
    """The installed launcher (~/.local/bin/fyrn-chat-test),
    or None when the app was not installed that way."""
    candidate = Path.home() / ".local" / "bin" / "fyrn-chat-test"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return [str(candidate)]
    found = shutil.which("fyrn-chat-test")
    if found:
        return [found]
    return None


def restart_command():
    """How to start the app again: the installed launcher when
    there is one, else this interpreter running the package the
    way the launcher would."""
    launcher = _find_launcher()
    if launcher is not None:
        return launcher
    return [sys.executable, "-m", "fyrnchattest"]


def schedule_restart(pid=None, command=None):
    """Restart the app once this process has exited: a detached
    shell waits for the process to go away, then starts the app
    the same way it was launched (the installed launcher, or
    this interpreter running the package). The wait matters:
    this process holds the single-instance lock, so a new
    instance started too soon would only raise this one and
    exit. Returns True when the watcher started. pid and
    command are test seams (a dead pid makes the watcher fire
    at once)."""
    if pid is None:
        pid = os.getpid()
    if command is None:
        command = restart_command()
    script = ("while kill -0 %d 2>/dev/null; do sleep 0.2; "
              "done; exec \"$@\"" % pid)
    try:
        subprocess.Popen(
            ["/bin/sh", "-c", script, "fyrn-chat-test-restart"]
            + list(command),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            close_fds=True)
    except OSError:
        return False
    return True
