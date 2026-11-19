#!/usr/bin/env python3
"""Build fyrn-chat-test-v<version>.zip with correct stored file
modes.

Scripts and launchers are stored as 0o100755 (regular file +
exec bits, so GUI extractors keep the exec bit); everything else
is 0o100644. The venv, logs, and caches stay out. The version
comes from fyrnchattest/__init__.py — the one place it lives.

The PNG icons are rendered from the SVG when they are missing,
so the build works from a fresh clone (the repo holds only
assets/fyrn-chat-test.svg; the PNGs are zip-only).
render_icons.py ships in the zip for exactly that: without it,
a fresh clone would have nothing to render with.
"""

import hashlib
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from fyrnchattest import __version__ as VERSION  # noqa: E402

APP_ID = "fyrn-chat-test"


def _release_tag(version):
    """Release tag from the version: a trailing .0 patch is
    dropped, so 0.1.0 ships as v0.1 and 0.1.1 as v0.1.1."""
    parts = version.split(".")
    if len(parts) == 3 and parts[2] == "0":
        return ".".join(parts[:2])
    return version


ZIP_NAME = "%s-v%s.zip" % (APP_ID, _release_tag(VERSION))
PREFIX = APP_ID + "/"

EXEC_FILES = {
    "install.sh",
    "run.sh",
    "Fyrn-Chat-Test.desktop",
    "Install-Fyrn-Chat-Test.desktop",
}

PLAIN_FILES = [
    "build_zip.py",
    "render_icons.py",
    "launcher.sh",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    ".gitignore",
    "assets/fyrn-chat-test.svg",
    "assets/fyrn-chat-test-48.png",
    "assets/fyrn-chat-test-256.png",
    "assets/fyrn-chat-test.png",
    "fyrnchattest/__init__.py",
    "fyrnchattest/__main__.py",
    "fyrnchattest/finder.py",
    "fyrnchattest/models.py",
    "fyrnchattest/smoke.py",
    "fyrnchattest/reports.py",
    "fyrnchattest/theme.py",
    "fyrnchattest/gui.py",
    "fyrnchattest/updates.py",
    "tests/__init__.py",
    "tests/helpers.py",
    "tests/test_helpers.py",
    "tests/test_finder.py",
    "tests/test_models.py",
    "tests/test_smoke.py",
    "tests/test_reports.py",
    "tests/test_gui.py",
    "tests/test_install.py",
    "tests/test_theme.py",
    "tests/test_updates.py",
]


def _ensure_icons():
    """Render the PNG icons from the SVG when they are missing."""
    missing = [name for name in ("assets/fyrn-chat-test-48.png",
                                 "assets/fyrn-chat-test-256.png",
                                 "assets/fyrn-chat-test.png")
               if not os.path.exists(os.path.join(ROOT, name))]
    if not missing:
        return True
    print("icons missing (%s), rendering from the SVG..."
          % ", ".join(missing))
    try:
        import render_icons
    except ImportError as exc:
        print("cannot render icons: %s" % exc)
        return False
    try:
        return render_icons.main() == 0
    except Exception as exc:
        print("icon render failed: %s" % exc)
        return False


def build():
    if not _ensure_icons():
        return 1
    zip_path = os.path.join(ROOT, ZIP_NAME)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    files = [(name, 0o100755) for name in sorted(EXEC_FILES)]
    files += [(name, 0o100644) for name in PLAIN_FILES]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, mode in files:
            full = os.path.join(ROOT, name)
            if not os.path.exists(full):
                print("MISSING: %s" % name)
                return 1
            info = zipfile.ZipInfo(PREFIX + name)
            info.create_system = 3  # Unix, so the mode bits are kept
            info.external_attr = mode << 16
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())
    # Verify stored modes.
    ok = True
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            mode = info.external_attr >> 16
            want = 0o100755 if os.path.basename(info.filename) \
                in EXEC_FILES else 0o100644
            if mode != want:
                print("BAD MODE: %s has %o, want %o"
                      % (info.filename, mode, want))
                ok = False
    size = os.path.getsize(zip_path)
    digest = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        digest.update(fh.read())
    print("built %s" % zip_path)
    print("files: %d" % len(files))
    print("size: %d bytes" % size)
    print("sha256: %s" % digest.hexdigest())
    print("modes ok: %s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(build())
