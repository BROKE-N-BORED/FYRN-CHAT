"""Test helpers: fake .gguf files, a fake Fyrn install built
from the real Fyrn package files, a fake Ollama store, a modal
dialog watcher, and a GUI test base class.

The fake Fyrn install is the point: the tool is tested against
the REAL Fyrn code (core, discover, runner, debug_log), copied
from the canonical Fyrn tree into a temp folder, so the suite
tests the integration, not a stub. No real model is ever
needed: the engine is faked with Fyrn's own FakeRunner.

Nothing here touches the network or a real model.
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from fyrnchattest.finder import find_fyrn_install

# The sandbox's canonical Fyrn tree: the second place the suite
# looks for real Fyrn code (see resolve_fyrn_tree). It exists
# only in this sandbox, so it is a fallback, never the only
# source: the suite must also run on a PC where Fyrn is
# installed but this path does not exist.
CANONICAL_FYRN = Path(
    "/home/hatch/workspace/own-ollama-v01-build/own-ollama")

# The Fyrn package files the tool imports (all stdlib-only at
# import time, so they load without llama_cpp installed).
FYRN_FILES = ("__init__.py", "core.py", "discover.py", "runner.py",
              "debug_log.py")

_FAKE_FYRN_DIR = None


def _holds_fyrn_code(folder):
    """True when a folder holds the Fyrn package files the fake
    install is copied from."""
    folder = Path(folder)
    return (folder / "fyrn" / "core.py").is_file()


def resolve_fyrn_tree():
    """The Fyrn source tree the fake install is copied from, or
    None when no source is available:
      1. FYRN_INSTALL_DIR, when it holds fyrn/core.py.
      2. The sandbox canonical tree, when it exists.
      3. The tool's own finder (a real Fyrn install on this PC).
    The finder needs a full install (install.sh + venv), which
    the canonical tree is not — hence the canonical path stays
    as step 2 instead of the finder doing all the work.
    """
    override = os.environ.get("FYRN_INSTALL_DIR")
    if override and _holds_fyrn_code(override):
        return Path(override)
    if _holds_fyrn_code(CANONICAL_FYRN):
        return CANONICAL_FYRN
    try:
        return find_fyrn_install().folder
    except Exception:
        return None


def ensure_fake_fyrn():
    """Create (once per process) a fake Fyrn install in a temp
    folder: install.sh, the real fyrn package files, and a
    .venv/bin/python marker. Puts the folder first on sys.path
    so `import fyrn` finds it. Returns the install dir (Path).
    Raises RuntimeError when no Fyrn source is available: the
    suite tests against real Fyrn code and cannot run without
    it, and a missing tree fails loudly instead of testing
    against a stub nobody told the reader about.
    """
    global _FAKE_FYRN_DIR
    if _FAKE_FYRN_DIR is not None:
        return _FAKE_FYRN_DIR
    source = resolve_fyrn_tree()
    if source is None:
        raise RuntimeError(
            "No Fyrn source found: set FYRN_INSTALL_DIR to a "
            "folder holding fyrn/core.py, or install Fyrn on "
            "this PC. The suite tests against real Fyrn code "
            "and cannot run without it.")
    root = Path(tempfile.mkdtemp(prefix="fyrnct-fake-fyrn-"))
    (root / "fyrn").mkdir()
    for name in FYRN_FILES:
        shutil.copyfile(source / "fyrn" / name,
                        root / "fyrn" / name)
    (root / "install.sh").write_text("# fake Fyrn install\n",
                                     encoding="utf-8")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    marker = venv_bin / "python"
    marker.write_text("#!/bin/sh\nexec /bin/true\n",
                      encoding="utf-8")
    marker.chmod(0o755)
    sys.path.insert(0, str(root))
    _FAKE_FYRN_DIR = root
    return root


def make_fake_gguf(path, size):
    """Write a deterministic fake .gguf file of size bytes."""
    path = str(path)
    chunk = bytes(range(256)) * 4  # 1024 deterministic bytes
    with open(path, "wb") as fh:
        remaining = size
        while remaining > 0:
            piece = chunk[:min(len(chunk), remaining)]
            fh.write(piece)
            remaining -= len(piece)
    return path


def write_gguf_file(path, metadata, version=3, numbers=None,
                    tensors=None, file_type=0):
    """Write a minimal valid GGUF file with the given string
    metadata (dict key -> str) and numeric metadata (dict key ->
    int, written as UINT32), plus one uint32 entry
    (general.file_type) and one string array, so parser tests
    have a real file to read. tensors is a list of (name, dims,
    type) tuples written as tensor infos after the KV section
    (v3 only); the default is no tensors."""
    if tensors and version != 3:
        raise ValueError("tensors are only supported for GGUF v3")
    tensors = list(tensors or [])
    out = bytearray()
    out += b"GGUF"
    out += struct.pack("<I", version)
    entries = list(metadata.items())
    number_entries = list((numbers or {}).items())
    kv_count = len(entries) + len(number_entries) + 2
    if version == 3:
        out += struct.pack("<Q", len(tensors))  # tensor count
        out += struct.pack("<Q", kv_count)
    else:
        out += struct.pack("<I", 0)
        out += struct.pack("<I", kv_count)

    def put_string(text):
        blob = text.encode("utf-8")
        out.extend(struct.pack("<Q", len(blob)))
        out.extend(blob)

    for key, value in entries:
        put_string(key)
        out.extend(struct.pack("<I", 8))  # STRING
        put_string(value)
    for key, value in number_entries:
        put_string(key)
        out.extend(struct.pack("<I", 4))  # UINT32
        out.extend(struct.pack("<I", value))
    put_string("general.file_type")
    out.extend(struct.pack("<I", 4))  # UINT32
    out.extend(struct.pack("<I", file_type))
    put_string("general.tags")
    out.extend(struct.pack("<I", 9))  # ARRAY
    out.extend(struct.pack("<I", 8))  # of STRING
    out.extend(struct.pack("<Q", 2))
    put_string("test")
    put_string("gguf")
    for name, dims, tensor_type in tensors:
        put_string(name)
        out.extend(struct.pack("<I", len(dims)))
        for dim in dims:
            out.extend(struct.pack("<Q", dim))
        out.extend(struct.pack("<I", tensor_type))
        out.extend(struct.pack("<Q", 0))
    Path(path).write_bytes(bytes(out))
    return path


def make_fake_ollama_store(store_dir, models):
    """Build a fake Ollama store (manifests + blobs) in
    store_dir. models is a list of (name, tag, blob_bytes):
    name "qwen3" and tag "2b" become the model qwen3:2b. The
    blob is written as a real GGUF file when blob_bytes is a
    dict of metadata, else as raw bytes. Returns the store dir.
    """
    store_dir = Path(store_dir)
    blobs = store_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    for name, tag, blob_bytes in models:
        import hashlib
        if isinstance(blob_bytes, dict):
            blob_path = blobs / "tmp.gguf"
            write_gguf_file(blob_path, blob_bytes)
            data = blob_path.read_bytes()
            blob_path.unlink()
        else:
            data = blob_bytes
        digest = hashlib.sha256(data).hexdigest()
        (blobs / ("sha256-" + digest)).write_bytes(data)
        manifest_dir = (store_dir / "manifests" / "registry.ollama.ai"
                        / "library" / name)
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.ollama.image.manifest.v1",
            "layers": [{
                "mediaType": "application/vnd.ollama.image.model",
                "digest": "sha256:" + digest,
                "size": len(data),
            }],
        }
        (manifest_dir / tag).write_text(json.dumps(manifest),
                                        encoding="utf-8")
    return store_dir


class ModalWatcher:
    """Records and dismisses unexpected modal dialogs in GUI
    tests.

    A settle pump can hang forever inside a dialog's nested
    event loop, so the watcher runs on a persistent QTimer and
    clicks the dialog's default button (by role, never by text:
    QMessageBox buttons carry a mnemonic, "&Yes" not "Yes").
    """

    def __init__(self):
        self.seen = []  # (title, text) of every dialog dismissed
        self.enabled = True
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._sweep)
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _sweep(self):
        if not self.enabled:
            return
        app = QApplication.instance()
        if app is None:
            return
        for widget in app.topLevelWidgets():
            if not isinstance(widget, QDialog):
                continue
            if not widget.isModal() or not widget.isVisible():
                continue
            # Only auto-dismiss plain message boxes; the app's
            # own windows (How to Use) are not modal.
            if isinstance(widget, QMessageBox):
                self.seen.append((widget.windowTitle(),
                                  widget.text()))
                button = widget.defaultButton()
                if button is not None:
                    button.click()
                else:
                    widget.accept()


def pump(milliseconds=100):
    """Process Qt events for a while."""
    app = QApplication.instance()
    deadline = time.monotonic() + milliseconds / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout_s=10.0, tick_ms=10):
    """Pump Qt events until predicate() is true or the timeout
    passes. Returns True when the predicate held."""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        app.processEvents()
        time.sleep(tick_ms / 1000.0)
    return predicate()


class GuiTestCase(unittest.TestCase):
    """Base class for GUI tests: a QApplication, a fake Fyrn
    install, a temp tool home and settings, a temp models
    folder, and window tracking (every window is closed in
    tearDown, so a leaked worker cannot abort the suite)."""

    @classmethod
    def setUpClass(cls):
        cls.fyrn_dir = ensure_fake_fyrn()

    def setUp(self):
        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication([])
        from fyrnchattest.theme import DARK, build_stylesheet
        self.app.setStyleSheet(build_stylesheet(DARK))
        self.tmp = Path(tempfile.mkdtemp(prefix="fyrnct-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.models = self.tmp / "Models"
        self.models.mkdir()
        self._saved_env = {}
        for key in ("FYRN_CHAT_TEST_HOME",
                    "FYRN_CHAT_TEST_SETTINGS_FILE",
                    "FYRN_SETTINGS_FILE", "FYRN_INSTALL_DIR"):
            self._saved_env[key] = os.environ.get(key)
            os.environ.pop(key, None)
        os.environ["FYRN_CHAT_TEST_HOME"] = str(self.home)
        os.environ["FYRN_CHAT_TEST_SETTINGS_FILE"] = str(
            self.tmp / "settings.ini")
        self.windows = []
        self.watcher = ModalWatcher()

    def tearDown(self):
        self.watcher.stop()
        for window in self.windows:
            window.close()
            pump(50)
        pump(100)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_window(self, runner_factory=None, **kwargs):
        """A MainWindow over the fake Fyrn install and the temp
        models folder. runner_factory defaults to a FakeRunner
        with a short reply. Ollama discovery is off by default:
        the sandbox has a real Ollama store, and its models
        would leak into every test's list (the one test that
        needs Ollama discovery passes include_ollama=True with
        a patched store)."""
        from fyrn.runner import FakeRunner
        from fyrnchattest.finder import FyrnInstall
        from fyrnchattest.gui import MainWindow
        if runner_factory is None:
            runner_factory = lambda: FakeRunner(  # noqa: E731
                reply_text="Hello from the fake model.")
        kwargs.setdefault("include_ollama", False)
        install = FyrnInstall(self.fyrn_dir,
                              self.fyrn_dir / ".venv" / "bin"
                              / "python", "0.5-test")
        window = MainWindow(install,
                            runner_factory=runner_factory,
                            models_folder=self.models, **kwargs)
        self.windows.append(window)
        window.show()
        window.activateWindow()
        pump(100)
        return window

    def wait_scan(self, window, timeout_s=10.0):
        """Wait for the window's model scan to finish."""
        return wait_until(lambda: window._scan_worker is None,
                          timeout_s)

    def wait_loaded(self, window, timeout_s=10.0):
        """Wait for a load to finish (loaded or failed)."""
        return wait_until(lambda: window._load_worker is None,
                          timeout_s)

    def wait_chat_done(self, window, timeout_s=10.0):
        """Wait for a chat reply to finish."""
        return wait_until(lambda: window._chat_worker is None,
                          timeout_s)

    def wait_smoke_done(self, window, timeout_s=30.0):
        """Wait for a smoke test to finish."""
        return wait_until(lambda: window._smoke_worker is None,
                          timeout_s)
