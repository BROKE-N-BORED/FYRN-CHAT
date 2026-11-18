"""Fyrn discovery tests: launcher path, search fallback,
not-found, ambiguous, env override, depth and hidden-folder
bounds, and the Fyrn-is-running check. Standard library only:
no Qt, no real Fyrn install."""

import fcntl
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fyrnchattest.finder import (FyrnAmbiguousError,
                                 FyrnNotFoundError,
                                 find_fyrn_install,
                                 fyrn_is_running)


def make_install(root, version="0.5"):
    """A minimal Fyrn install in root: install.sh, the fyrn
    package (just __init__.py with a version), and a venv
    python marker. Returns the install dir."""
    root = Path(root)
    (root / "fyrn").mkdir(parents=True, exist_ok=True)
    (root / "fyrn" / "__init__.py").write_text(
        '__version__ = "%s"\n' % version, encoding="utf-8")
    (root / "install.sh").write_text("# fake\n", encoding="utf-8")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    marker = venv_bin / "python"
    marker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    marker.chmod(0o755)
    return root


def make_launcher(home, app_dir):
    """A ~/.local/bin/fyrn launcher naming app_dir."""
    launcher = Path(home) / ".local" / "bin" / "fyrn"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        '#!/usr/bin/env bash\nAPP_DIR="%s"\n' % app_dir,
        encoding="utf-8")
    return launcher


class FinderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-finder-"))
        self.home = self.tmp / "home"
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_launcher_path_wins(self):
        install = make_install(self.home / "fyrn")
        make_launcher(self.home, install)
        found = find_fyrn_install(home=self.home, environ={})
        self.assertEqual(found.folder, install)
        self.assertEqual(found.version, "0.5")
        self.assertEqual(found.python,
                         install / ".venv" / "bin" / "python")

    def test_stale_launcher_falls_through_to_search(self):
        install = make_install(self.home / "moved-fyrn")
        make_launcher(self.home, self.home / "gone")
        found = find_fyrn_install(home=self.home, environ={})
        self.assertEqual(found.folder, install)

    def test_search_finds_a_nested_install(self):
        install = make_install(self.home / "Documents" / "fyrn")
        found = find_fyrn_install(home=self.home, environ={})
        self.assertEqual(found.folder, install)

    def test_search_finds_install_at_depth_six(self):
        install = make_install(
            self.home / "a" / "b" / "c" / "d" / "e" / "fyrn")
        found = find_fyrn_install(home=self.home, environ={})
        self.assertEqual(found.folder, install)

    def test_search_stops_past_depth_six(self):
        make_install(
            self.home / "a" / "b" / "c" / "d" / "e" / "f" / "fyrn")
        with self.assertRaises(FyrnNotFoundError):
            find_fyrn_install(home=self.home, environ={})

    def test_search_skips_hidden_folders(self):
        make_install(self.home / ".hidden" / "fyrn")
        with self.assertRaises(FyrnNotFoundError):
            find_fyrn_install(home=self.home, environ={})

    def test_not_found_says_so_plainly(self):
        with self.assertRaises(FyrnNotFoundError) as caught:
            find_fyrn_install(home=self.home, environ={})
        self.assertIn("not installed", str(caught.exception))

    def test_ambiguous_lists_both_folders(self):
        first = make_install(self.home / "fyrn")
        second = make_install(self.home / "fyrn2")
        with self.assertRaises(FyrnAmbiguousError) as caught:
            find_fyrn_install(home=self.home, environ={})
        self.assertEqual(set(caught.exception.candidates),
                         {first, second})
        self.assertIn("More than one", str(caught.exception))

    def test_env_override_wins(self):
        install = make_install(self.home / "custom")
        other = make_install(self.home / "fyrn")
        make_launcher(self.home, other)
        found = find_fyrn_install(
            home=self.home,
            environ={"FYRN_INSTALL_DIR": str(install)})
        self.assertEqual(found.folder, install)

    def test_env_override_to_a_non_install_fails(self):
        plain = self.home / "plain"
        plain.mkdir()
        with self.assertRaises(FyrnNotFoundError):
            find_fyrn_install(
                home=self.home,
                environ={"FYRN_INSTALL_DIR": str(plain)})

    def test_env_override_resolves_ambiguity(self):
        first = make_install(self.home / "fyrn")
        make_install(self.home / "fyrn2")
        found = find_fyrn_install(
            home=self.home,
            environ={"FYRN_INSTALL_DIR": str(first)})
        self.assertEqual(found.folder, first)

    def test_missing_venv_is_not_an_install(self):
        root = self.home / "fyrn"
        (root / "fyrn").mkdir(parents=True)
        (root / "fyrn" / "__init__.py").write_text(
            '__version__ = "0.5"\n', encoding="utf-8")
        (root / "install.sh").write_text("# fake\n",
                                         encoding="utf-8")
        with self.assertRaises(FyrnNotFoundError):
            find_fyrn_install(home=self.home, environ={})

    def test_version_unknown_without_a_version_line(self):
        root = self.home / "fyrn"
        (root / "fyrn").mkdir(parents=True)
        (root / "fyrn" / "__init__.py").write_text(
            "# no version here\n", encoding="utf-8")
        (root / "install.sh").write_text("# fake\n",
                                         encoding="utf-8")
        venv_bin = root / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        marker = venv_bin / "python"
        marker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        marker.chmod(0o755)
        found = find_fyrn_install(home=self.home, environ={})
        self.assertEqual(found.version, "unknown")


class FyrnRunningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-running-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._held = None

    def tearDown(self):
        if self._held is not None:
            self._held.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_lock_file_means_not_running(self):
        self.assertFalse(fyrn_is_running(home=self.home))
        # And the check created nothing in Fyrn's home.
        self.assertFalse((self.home / ".fyrn").exists())

    def test_unlocked_file_means_not_running(self):
        lock_dir = self.home / ".fyrn"
        lock_dir.mkdir()
        (lock_dir / "instance.lock").write_text("",
                                                encoding="utf-8")
        self.assertFalse(fyrn_is_running(home=self.home))

    def test_locked_file_means_running(self):
        lock_dir = self.home / ".fyrn"
        lock_dir.mkdir()
        lock_path = lock_dir / "instance.lock"
        lock_path.write_text("", encoding="utf-8")
        self._held = open(lock_path, "a+")
        fcntl.flock(self._held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertTrue(fyrn_is_running(home=self.home))


if __name__ == "__main__":
    unittest.main()
