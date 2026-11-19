"""Update tests: the version compare, the release check
against a stubbed opener, the checksum-verified download, the
install into a fake app folder, the staged update, and the
restart watcher. Nothing here touches the network or the real
tree: the app folder is always a temp folder, and the opener
is always a stub."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from fyrnchattest import updates
from fyrnchattest.updates import UpdateError

API_URL = updates.LATEST_RELEASE_URL
ZIP_URL = "https://example.com/fyrn-chat-test-v0.1.2.zip"
SIDECAR_URL = ZIP_URL + ".sha256"


class FakeResponse:
    """A urlopen response over bytes in memory."""

    def __init__(self, body, headers=None):
        self._body = bytes(body)
        self._pos = 0
        if headers is None:
            headers = {"Content-Length": str(len(self._body))}
        self.headers = dict(headers)

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class FakeUrlopen:
    """A urlopen stub: urls map to bytes (served with a
    Content-Length), a response object (served as-is), or an
    exception (raised). An unmapped URL raises KeyError: loud,
    so a test that hits the wrong URL fails instead of passing
    on an empty answer."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.requested = []

    def __call__(self, url, timeout=None):
        self.requested.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        if hasattr(value, "read"):
            return value
        return FakeResponse(value)


def make_release_payload(tag="v0.1.2", zip_url=ZIP_URL,
                         sidecar_url=SIDECAR_URL):
    """A GitHub latest-release payload for a tag."""
    zip_name = "fyrn-chat-test-%s.zip" % tag
    return {
        "tag_name": tag,
        "html_url": ("https://github.com/BROKE-N-BORED/FYRN-CHAT"
                     "/releases/tag/%s" % tag),
        "assets": [
            {"name": zip_name,
             "browser_download_url": zip_url},
            {"name": zip_name + ".sha256",
             "browser_download_url": sidecar_url},
        ],
    }


def make_fake_zip(path, files, modes=None):
    """A release-shaped zip: every file under the
    fyrn-chat-test/ prefix, with stored Unix modes (install.sh
    executable, the rest plain). files maps relpath to bytes."""
    modes = modes or {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            info = zipfile.ZipInfo("fyrn-chat-test/" + rel)
            info.create_system = 3
            info.external_attr = modes.get(rel, 0o100644) << 16
            zf.writestr(info, data)
    return path


def make_version_zip(path, version="0.1.2"):
    """A release-shaped zip for a version: the package files
    plus an executable install.sh."""
    return make_fake_zip(path, {
        "fyrnchattest/__init__.py":
            ('__version__ = "%s"\n' % version).encode("utf-8"),
        "fyrnchattest/gui.py":
            ("# gui %s\n" % version).encode("utf-8"),
        "install.sh": b"#!/usr/bin/env bash\n# install\n",
    }, modes={"install.sh": 0o100755})


def make_fake_app_dir(root, version="0.1.1"):
    """A fake installed app folder: the package, an executable
    install.sh, and one extra file the zip does not carry."""
    root = Path(root)
    (root / "fyrnchattest").mkdir(parents=True)
    (root / "fyrnchattest" / "__init__.py").write_text(
        '__version__ = "%s"\n' % version, encoding="utf-8")
    (root / "fyrnchattest" / "gui.py").write_text(
        "# gui %s\n" % version, encoding="utf-8")
    install = root / "install.sh"
    install.write_text("#!/usr/bin/env bash\n# install old\n",
                       encoding="utf-8")
    install.chmod(0o755)
    (root / "extra.txt").write_text("keep me\n", encoding="utf-8")
    return root


class VersionTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updates.parse_version("0.1.1"),
                         (0, 1, 1))
        self.assertEqual(updates.parse_version("v0.1.1"),
                         (0, 1, 1))
        self.assertEqual(updates.parse_version(" 0.1 "),
                         (0, 1))
        self.assertEqual(updates.parse_version("2"), (2,))

    def test_parse_version_refuses_non_versions(self):
        for bad in ("v0.1.1-beta", "latest", "", "0.1.x", "v",
                    None, 42, "0..1"):
            self.assertIsNone(updates.parse_version(bad),
                              "parsed %r as a version" % (bad,))

    def test_is_newer(self):
        self.assertTrue(updates.is_newer("0.1.2", "0.1.1"))
        self.assertTrue(updates.is_newer("v0.1.2", "0.1.1"))
        self.assertTrue(updates.is_newer("0.2", "0.1.9"))
        self.assertTrue(updates.is_newer("1.0", "0.9.9"))

    def test_is_newer_equal_and_older(self):
        self.assertFalse(updates.is_newer("0.1.1", "0.1.1"))
        self.assertFalse(updates.is_newer("0.1.1", "0.1.2"))
        # Shorter tuples pad with zeros: 0.1 and 0.1.0 are equal.
        self.assertFalse(updates.is_newer("0.1", "0.1.0"))
        self.assertFalse(updates.is_newer("0.1.0", "0.1"))
        self.assertTrue(updates.is_newer("0.1.0.1", "0.1"))

    def test_is_newer_unparsable_is_never_newer(self):
        self.assertFalse(updates.is_newer("latest", "0.1.1"))
        self.assertFalse(updates.is_newer("0.1.2", "dev"))
        self.assertFalse(updates.is_newer(None, "0.1.1"))


class CheckTests(unittest.TestCase):
    def _payload_bytes(self, payload):
        return json.dumps(payload).encode("utf-8")

    def test_newer_release_found(self):
        stub = FakeUrlopen({
            API_URL: self._payload_bytes(make_release_payload()),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "newer")
        self.assertEqual(result["latest"], "0.1.2")
        self.assertEqual(result["zip_url"], ZIP_URL)
        self.assertEqual(result["sidecar_url"], SIDECAR_URL)
        self.assertEqual(result["zip_name"],
                         "fyrn-chat-test-v0.1.2.zip")

    def test_up_to_date(self):
        stub = FakeUrlopen({
            API_URL: self._payload_bytes(
                make_release_payload(tag="v0.1.1")),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "current")
        self.assertEqual(result["latest"], "0.1.1")

    def test_dev_tree_newer_than_release_is_current(self):
        stub = FakeUrlopen({
            API_URL: self._payload_bytes(make_release_payload()),
        })
        result = updates.check_for_update("0.2", urlopen=stub)
        self.assertEqual(result["state"], "current")

    def test_offline_is_unknown_not_a_crash(self):
        stub = FakeUrlopen({
            API_URL: urllib.error.URLError("no network"),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("could not reach GitHub", result["reason"])

    def test_timeout_is_unknown(self):
        stub = FakeUrlopen({
            API_URL: urllib.error.URLError(
                TimeoutError("timed out")),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")
        self.assertIn("timed out", result["reason"])
        stub = FakeUrlopen({API_URL: TimeoutError("timed out")})
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")

    def test_malformed_json_is_unknown(self):
        stub = FakeUrlopen({API_URL: b"not json{"})
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")

    def test_non_dict_json_is_unknown(self):
        stub = FakeUrlopen({API_URL: b"[1, 2]"})
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")

    def test_missing_tag_is_unknown(self):
        stub = FakeUrlopen({API_URL: b"{}"})
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")

    def test_unparsable_tag_is_unknown(self):
        stub = FakeUrlopen({
            API_URL: self._payload_bytes(
                make_release_payload(tag="v0.1.2-beta")),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")

    def test_release_without_download_files_is_unknown(self):
        payload = {"tag_name": "v0.1.2", "assets": []}
        stub = FakeUrlopen({
            API_URL: self._payload_bytes(payload),
        })
        result = updates.check_for_update("0.1.1", urlopen=stub)
        self.assertEqual(result["state"], "unknown")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.zip_path = self.tmp / "release.zip"
        make_version_zip(self.zip_path, "0.1.2")
        self.zip_bytes = self.zip_path.read_bytes()
        self.sha = hashlib.sha256(self.zip_bytes).hexdigest()
        self.release = {"version": "0.1.2", "zip_url": ZIP_URL,
                        "sidecar_url": SIDECAR_URL,
                        "zip_name": "fyrn-chat-test-v0.1.2.zip"}

    def _sidecar(self, sha=None):
        return ("%s  fyrn-chat-test-v0.1.2.zip\n"
                % (sha or self.sha)).encode("utf-8")

    def test_download_and_verify_stages_the_zip(self):
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar(),
            ZIP_URL: self.zip_bytes,
        })
        progress = []
        dest = self.tmp / "updates"
        path = updates.download_and_verify(
            self.release, dest, urlopen=stub,
            progress_cb=lambda d, t: progress.append((d, t)))
        self.assertEqual(path, dest / "fyrn-chat-test-v0.1.2.zip")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), self.zip_bytes)
        marker = json.loads(
            (dest / "staged.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "0.1.2")
        self.assertEqual(marker["sha256"], self.sha)
        self.assertEqual(marker["zip"],
                         "fyrn-chat-test-v0.1.2.zip")
        self.assertEqual(progress[0], (0, len(self.zip_bytes)))
        self.assertEqual(progress[-1],
                         (len(self.zip_bytes),
                          len(self.zip_bytes)))

    def test_check_result_shape_uses_latest(self):
        # The GUI hands download_and_verify the check result,
        # which names the version "latest", not "version".
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar(),
            ZIP_URL: self.zip_bytes,
        })
        release = {"latest": "0.1.2", "zip_url": ZIP_URL,
                   "sidecar_url": SIDECAR_URL,
                   "zip_name": "fyrn-chat-test-v0.1.2.zip"}
        dest = self.tmp / "updates"
        updates.download_and_verify(release, dest, urlopen=stub)
        marker = json.loads(
            (dest / "staged.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "0.1.2")

    def test_checksum_mismatch_deletes_and_refuses(self):
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar("0" * 64),
            ZIP_URL: self.zip_bytes,
        })
        dest = self.tmp / "updates"
        with self.assertRaises(UpdateError) as caught:
            updates.download_and_verify(self.release, dest,
                                        urlopen=stub)
        self.assertIn("checksum", str(caught.exception))
        self.assertFalse(
            (dest / "fyrn-chat-test-v0.1.2.zip").exists())
        self.assertFalse((dest / "staged.json").exists())

    def test_bad_sidecar_refuses(self):
        stub = FakeUrlopen({
            SIDECAR_URL: b"not a checksum\n",
            ZIP_URL: self.zip_bytes,
        })
        with self.assertRaises(UpdateError) as caught:
            updates.download_and_verify(
                self.release, self.tmp / "updates", urlopen=stub)
        self.assertIn("checksum", str(caught.exception))

    def test_download_failure_is_plain_words(self):
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar(),
            ZIP_URL: urllib.error.URLError("no network"),
        })
        with self.assertRaises(UpdateError) as caught:
            updates.download_and_verify(
                self.release, self.tmp / "updates", urlopen=stub)
        self.assertIn("could not reach GitHub",
                      str(caught.exception))

    def test_cancel_before_start_leaves_nothing(self):
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar(),
            ZIP_URL: self.zip_bytes,
        })
        event = threading.Event()
        event.set()
        dest = self.tmp / "updates"
        with self.assertRaises(updates.UpdateCancelled):
            updates.download_and_verify(
                self.release, dest, urlopen=stub,
                cancel_event=event)
        self.assertEqual(list(dest.glob("*")), [])

    def test_cancel_mid_download_leaves_nothing(self):
        class CancellingResponse(FakeResponse):
            def __init__(self, body, event):
                super().__init__(body)
                self._event = event

            def read(self, size=-1):
                chunk = super().read(size)
                if chunk:
                    self._event.set()
                return chunk

        event = threading.Event()
        stub = FakeUrlopen({
            SIDECAR_URL: self._sidecar(),
            ZIP_URL: CancellingResponse(self.zip_bytes, event),
        })
        dest = self.tmp / "updates"
        with self.assertRaises(updates.UpdateCancelled):
            updates.download_and_verify(
                self.release, dest, urlopen=stub,
                cancel_event=event)
        self.assertEqual(list(dest.glob("*")), [])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.app = make_fake_app_dir(self.tmp / "app", "0.1.1")

    def test_install_replaces_files_and_keeps_modes(self):
        zip_path = self.tmp / "release.zip"
        make_version_zip(zip_path, "0.1.2")
        # Stale bytecode goes with the install.
        cache = self.app / "fyrnchattest" / "__pycache__"
        cache.mkdir()
        (cache / "stale.pyc").write_bytes(b"stale")
        updates.install_update(zip_path, self.app)
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.2"', init)
        gui = (self.app / "fyrnchattest"
               / "gui.py").read_text(encoding="utf-8")
        self.assertIn("0.1.2", gui)
        # The exec bit survives the install.
        self.assertTrue(os.access(self.app / "install.sh",
                                  os.X_OK))
        # A file the zip does not carry is left alone.
        self.assertEqual((self.app / "extra.txt").read_text(
            encoding="utf-8"), "keep me\n")
        self.assertFalse(cache.exists())

    def test_install_defaults_to_the_app_dir_env(self):
        # No folder passed: the install lands in app_dir(),
        # which reads FYRN_CHAT_TEST_APP_DIR in tests. (The
        # parameter once shadowed the app_dir function, so
        # this path called None: this test pins the fix.)
        zip_path = self.tmp / "release.zip"
        make_version_zip(zip_path, "0.1.2")
        with mock.patch.dict(
                os.environ,
                {"FYRN_CHAT_TEST_APP_DIR": str(self.app)}):
            updates.install_update(zip_path)
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.2"', init)

    def test_install_adds_new_files(self):
        zip_path = self.tmp / "release.zip"
        make_fake_zip(zip_path, {
            "fyrnchattest/__init__.py":
                b'__version__ = "0.1.2"\n',
            "fyrnchattest/gui.py": b"# gui 0.1.2\n",
            "fyrnchattest/updates.py": b"# new module\n",
        })
        updates.install_update(zip_path, self.app)
        self.assertEqual(
            (self.app / "fyrnchattest" / "updates.py").read_text(
                encoding="utf-8"), "# new module\n")

    def test_bad_zip_refuses_and_leaves_the_app_alone(self):
        zip_path = self.tmp / "release.zip"
        zip_path.write_bytes(b"this is not a zip file")
        with self.assertRaises(UpdateError):
            updates.install_update(zip_path, self.app)
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)

    def test_zip_without_the_package_refuses(self):
        zip_path = self.tmp / "release.zip"
        make_fake_zip(zip_path, {"README.md": b"# not an app\n"})
        with self.assertRaises(UpdateError) as caught:
            updates.install_update(zip_path, self.app)
        self.assertIn("did not look like Fyrn Chat Test",
                      str(caught.exception))
        self.assertFalse((self.app / "README.md").exists())

    def test_zip_with_an_escape_refuses(self):
        zip_path = self.tmp / "release.zip"
        make_fake_zip(zip_path, {
            "fyrnchattest/__init__.py":
                b'__version__ = "0.1.2"\n',
            "fyrnchattest/gui.py": b"# gui 0.1.2\n",
            "../evil.txt": b"escaped\n",
        })
        with self.assertRaises(UpdateError):
            updates.install_update(zip_path, self.app)
        self.assertFalse((self.tmp / "evil.txt").exists())
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.app = make_fake_app_dir(self.tmp / "app", "0.1.1")
        self.zip_path = self.tmp / "release.zip"
        make_version_zip(self.zip_path, "0.1.2")
        self.zip_bytes = self.zip_path.read_bytes()
        self.sha = hashlib.sha256(self.zip_bytes).hexdigest()

    def _stage(self, sha=None):
        staged = updates.staged_dir(self.home)
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "fyrn-chat-test-v0.1.2.zip").write_bytes(
            self.zip_bytes)
        marker = {"version": "0.1.2",
                  "zip": "fyrn-chat-test-v0.1.2.zip",
                  "sha256": sha or self.sha}
        (staged / "staged.json").write_text(
            json.dumps(marker), encoding="utf-8")
        return staged

    def test_staged_update_applies_and_cleans_up(self):
        self._stage()
        applied = updates.apply_staged_update(self.home,
                                              self.app)
        self.assertEqual(applied, "0.1.2")
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.2"', init)
        self.assertFalse(
            updates.staged_dir(self.home).exists())

    def test_nothing_staged_returns_none(self):
        self.assertIsNone(
            updates.apply_staged_update(self.home, self.app))

    def test_stray_zip_without_a_marker_is_cleared(self):
        staged = updates.staged_dir(self.home)
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "fyrn-chat-test-v0.1.2.zip").write_bytes(
            self.zip_bytes)
        self.assertIsNone(
            updates.apply_staged_update(self.home, self.app))
        self.assertFalse(
            (staged / "fyrn-chat-test-v0.1.2.zip").exists())
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)

    def test_bad_checksum_is_cleared_not_installed(self):
        self._stage(sha="0" * 64)
        self.assertIsNone(
            updates.apply_staged_update(self.home, self.app))
        self.assertFalse(
            updates.staged_dir(self.home).exists())
        init = (self.app / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)

    def test_marker_without_a_zip_is_cleared(self):
        staged = updates.staged_dir(self.home)
        staged.mkdir(parents=True, exist_ok=True)
        (staged / "staged.json").write_text(json.dumps(
            {"version": "0.1.2",
             "zip": "fyrn-chat-test-v0.1.2.zip",
             "sha256": self.sha}), encoding="utf-8")
        self.assertIsNone(
            updates.apply_staged_update(self.home, self.app))
        self.assertFalse(staged.exists())


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-upd-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_restart_command_falls_back_without_a_launcher(self):
        with mock.patch.object(updates, "_find_launcher",
                               return_value=None):
            self.assertEqual(updates.restart_command(),
                             [sys.executable, "-m",
                              "fyrnchattest"])

    def test_restart_command_uses_the_launcher(self):
        with mock.patch.object(updates, "_find_launcher",
                               return_value=["/bin/true"]):
            self.assertEqual(updates.restart_command(),
                             ["/bin/true"])

    def test_watcher_fires_once_the_pid_is_gone(self):
        # A pid that already exited: the watcher fires at once.
        proc = subprocess.Popen(["/bin/true"])
        proc.wait()
        marker = self.tmp / "restarted.txt"
        script = self.tmp / "launcher.sh"
        script.write_text("#!/bin/sh\necho restarted > %s\n"
                          % marker, encoding="utf-8")
        script.chmod(0o755)
        self.assertTrue(updates.schedule_restart(
            pid=proc.pid, command=[str(script)]))
        deadline = time.monotonic() + 5.0
        while not marker.exists() \
                and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(marker.exists(),
                        "the restart watcher never fired")
        self.assertEqual(marker.read_text(encoding="utf-8"),
                         "restarted\n")


class MainEntryTests(unittest.TestCase):
    def test_staged_update_restarts_fresh(self):
        from fyrnchattest import __main__ as main_mod
        with mock.patch.object(
                updates, "apply_staged_update",
                return_value="0.1.2"), \
            mock.patch.object(
                main_mod, "_try_raise_running",
                return_value=True), \
            mock.patch("os.execv") as execv_mock:
            self.assertEqual(main_mod.main_entry(), 0)
        self.assertEqual(execv_mock.call_count, 1)
        target, argv = execv_mock.call_args[0]
        self.assertEqual(target, sys.executable)
        self.assertEqual(argv[:3],
                         [sys.executable, "-m", "fyrnchattest"])

    def test_no_staged_update_runs_normally(self):
        from fyrnchattest import __main__ as main_mod
        with mock.patch.object(
                updates, "apply_staged_update",
                return_value=None), \
            mock.patch.object(
                main_mod, "_try_raise_running",
                return_value=True), \
            mock.patch("os.execv") as execv_mock:
            self.assertEqual(main_mod.main_entry(), 0)
        execv_mock.assert_not_called()

    def test_failed_restart_falls_through_to_launch(self):
        from fyrnchattest import __main__ as main_mod
        with mock.patch.object(
                updates, "apply_staged_update",
                return_value="0.1.2"), \
            mock.patch.object(
                main_mod, "_try_raise_running",
                return_value=True), \
            mock.patch("os.execv",
                       side_effect=OSError("no exec")):
            # The update installed; the restart failed; the app
            # still launches instead of dying.
            self.assertEqual(main_mod.main_entry(), 0)


if __name__ == "__main__":
    unittest.main()
