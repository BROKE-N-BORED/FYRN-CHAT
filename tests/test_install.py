"""Install tests: the release zip covers the whole tree with
correct modes, the scripts parse, the desktop files resolve,
and no secrets hide in the tree."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Bind the real tree's package before build_zip is loaded from
# a temp copy: its `from fyrnchattest import __version__` must
# reuse this one, not import the copy.
import fyrnchattest  # noqa: E402,F401

EXEC_FILES = {
    "install.sh",
    "run.sh",
    "Fyrn-Chat-Test.desktop",
    "Install-Fyrn-Chat-Test.desktop",
}

EXCLUDED = ("__pycache__", ".venv", ".git")


def _tree_files(root):
    """Every file in the tree, minus the documented exclusions:
    caches, bytecode, zips, the venv, and git metadata."""
    out = set()
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if any(part in EXCLUDED for part in path.parts):
            continue
        if rel.endswith((".pyc", ".pyo", ".zip")):
            continue
        out.add(rel)
    return out


def _load_build_zip(copy_root):
    """Import build_zip.py from a copied tree."""
    spec = importlib.util.spec_from_file_location(
        "fyrnct_build_zip", copy_root / "build_zip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ZipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build the zip in a temp copy of the tree, so the test
        # never dirties the build tree with a zip or icons.
        cls.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-zip-"))
        cls.copy = cls.tmp / "fyrn-chat-test"
        shutil.copytree(ROOT, cls.copy, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.zip", ".venv", ".git"))
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.builder = _load_build_zip(cls.copy)
        cls.build_rc = cls.builder.build()
        cls.zip_path = cls.copy / cls.builder.ZIP_NAME

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_succeeds(self):
        self.assertEqual(self.build_rc, 0)
        self.assertTrue(self.zip_path.is_file())

    def test_zip_covers_the_whole_tree(self):
        with zipfile.ZipFile(self.zip_path) as zf:
            names = {info.filename for info in zf.infolist()}
        prefix = self.builder.PREFIX
        in_zip = {name[len(prefix):] for name in names
                  if name.startswith(prefix)}
        tree = _tree_files(self.copy)
        # The build renders the PNG icons, so they are in the
        # tree by the time the walk runs.
        self.assertEqual(in_zip, tree)

    def test_zip_modes(self):
        with zipfile.ZipFile(self.zip_path) as zf:
            for info in zf.infolist():
                mode = info.external_attr >> 16
                name = os.path.basename(info.filename)
                want = 0o100755 if name in EXEC_FILES else 0o100644
                self.assertEqual(
                    mode, want,
                    "%s stored as %o, want %o"
                    % (info.filename, mode, want))

    def test_zip_has_the_icons(self):
        with zipfile.ZipFile(self.zip_path) as zf:
            names = set(zf.namelist())
        for icon in ("assets/fyrn-chat-test-48.png",
                     "assets/fyrn-chat-test-256.png",
                     "assets/fyrn-chat-test.png"):
            self.assertIn(self.builder.PREFIX + icon, names)


class ScriptTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for name in ("install.sh", "launcher.sh", "run.sh"):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / name)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0,
                             "%s fails bash -n: %s"
                             % (name, result.stderr))

    def test_installer_finds_fyrn_and_trusts_the_icon(self):
        text = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("find_fyrn", text)
        self.assertIn("Fyrn is not installed", text)
        self.assertIn("gio set", text)
        self.assertIn("FYRN_CHAT_TEST_GUI_INSTALL", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("python3 -m venv", text)

    def test_launcher_finds_fyrn_and_passes_it_on(self):
        text = (ROOT / "launcher.sh").read_text(encoding="utf-8")
        self.assertIn("find_fyrn", text)
        self.assertIn("FYRN_INSTALL_DIR", text)
        self.assertIn("-m fyrnchattest", text)
        self.assertIn("fyrnchattest/__init__.py", text)

    def test_run_sh_uses_fyrns_venv(self):
        text = (ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertIn(".venv/bin/python", text)
        self.assertIn("-m fyrnchattest", text)


class DesktopTests(unittest.TestCase):
    def _parse(self, name):
        entries = {}
        for line in (ROOT / name).read_text(
                encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                entries[key.strip()] = value.strip()
        return entries

    def test_app_desktop_resolves(self):
        entries = self._parse("Fyrn-Chat-Test.desktop")
        self.assertEqual(entries["Type"], "Application")
        self.assertEqual(entries["Exec"],
                         "__HOME__/.local/bin/fyrn-chat-test")
        self.assertEqual(
            entries["Icon"],
            "__APP_DIR__/assets/fyrn-chat-test-48.png")
        self.assertEqual(entries["Terminal"], "false")
        self.assertEqual(entries["StartupWMClass"],
                         "fyrn-chat-test")

    def test_install_desktop_runs_install_sh(self):
        entries = self._parse("Install-Fyrn-Chat-Test.desktop")
        self.assertIn("install.sh", entries["Exec"])
        self.assertIn("FYRN_CHAT_TEST_GUI_INSTALL",
                      entries["Exec"])
        self.assertEqual(entries["Terminal"], "true")


class SecretsTests(unittest.TestCase):
    def test_no_secret_shaped_values_in_the_tree(self):
        # The tests/ folder is excluded: the marker strings live
        # in this very test by design.
        bad = []
        for path in sorted(_tree_files(ROOT)):
            if path.startswith("tests/"):
                continue
            text = (ROOT / path).read_bytes()
            for marker in (b"sk-", b"ghp_", b"AKIA",
                           b"api_key=", b"password="):
                if marker in text:
                    bad.append("%s: %s" % (path, marker))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
