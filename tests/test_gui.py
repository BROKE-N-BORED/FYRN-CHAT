"""GUI tests: the window opens, the list shows what Fyrn
sees, chat streams through the fake engine, the smoke test
fills the table and saves a report, and load failure is a
plain message, never a crash. The engine is Fyrn's own
FakeRunner: no real model is ever needed."""

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QMessageBox, QPushButton,
                               QWidget)

from helpers import (GuiTestCase, ensure_fake_fyrn, make_fake_gguf,
                     pump, wait_until, write_gguf_file)
from test_updates import (SIDECAR_URL, ZIP_URL, FakeUrlopen,
                          make_fake_app_dir, make_release_payload,
                          make_version_zip)

ensure_fake_fyrn()

from fyrn.runner import FakeRunner, RunnerError  # noqa: E402
from fyrnchattest import updates  # noqa: E402


def make_update_stub(tmp, version="0.1.2", sha=None):
    """A FakeUrlopen serving a newer release, its zip, and its
    sidecar. sha overrides the sidecar's checksum (a wrong one
    tests the refusal)."""
    zip_path = tmp / "release.zip"
    make_version_zip(zip_path, version)
    zip_bytes = zip_path.read_bytes()
    digest = sha if sha is not None else hashlib.sha256(
        zip_bytes).hexdigest()
    sidecar = ("%s  fyrn-chat-test-v%s.zip\n"
               % (digest, version)).encode("utf-8")
    payload = make_release_payload(tag="v" + version)
    return FakeUrlopen({
        updates.LATEST_RELEASE_URL:
            json.dumps(payload).encode("utf-8"),
        ZIP_URL: zip_bytes,
        SIDECAR_URL: sidecar,
    })


class FailingRunner(FakeRunner):
    """A runner whose load always fails."""

    def load(self, path, progress_cb=None, cancel_event=None,
             display_name=None):
        raise RunnerError("The model could not be loaded (boom).")


class MemoryErrorRunner(FakeRunner):
    """A runner that loads fine but fails every reply the way
    an embedding model does: no reply memory."""

    def generate(self, prompt, on_token=None, cancel_event=None,
                 history=None, images=None):
        raise RunnerError("Memory is not initialized")


class WindowTests(GuiTestCase):
    def test_window_opens_with_nothing_selected(self):
        make_fake_gguf(self.models / "a.gguf", 2048)
        make_fake_gguf(self.models / "b.gguf", 4096)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        self.assertEqual(window.model_list.count(), 2)
        self.assertEqual(window.model_list.selectedItems(), [])
        self.assertIsNone(window._selected_path)
        self.assertFalse(window.load_button.isEnabled())
        self.assertEqual(window.windowTitle(), "Fyrn Chat Test")

    def test_list_shows_facts_from_the_header(self):
        write_gguf_file(
            self.models / "qwen3-2b.gguf",
            {"general.architecture": "qwen3"},
            numbers={"qwen3.block_count": 28,
                     "qwen3.attention.head_count": 16,
                     "qwen3.attention.head_count_kv": 8,
                     "qwen3.embedding_length": 2048,
                     "qwen3.context_length": 32768},
            tensors=[("blk.0.attn_q.weight", [2048, 2048], 0)],
            file_type=15)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        text = window.model_list.item(0).text()
        self.assertIn("qwen3-2b.gguf", text)
        self.assertIn("Q4_K_M", text)
        self.assertIn("32K context", text)
        # The fit badge depends on the machine's free RAM at
        # run time (Fits / Tight / Too big): assert one is
        # shown, not which one. The exact badge logic is
        # covered by the model-listing tests.
        self.assertTrue(any(badge in text for badge in
                            ("Fits", "Tight", "Too big")),
                        "no fit badge in %r" % text)

    def test_embedding_model_shows_embedding_only_badge(self):
        write_gguf_file(
            self.models / "nomic-embed.gguf",
            {"general.architecture": "bert"})
        write_gguf_file(
            self.models / "qwen3-2b.gguf",
            {"general.architecture": "qwen3"})
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        self.assertEqual(window.model_list.count(), 2)
        texts = [window.model_list.item(row).text()
                 for row in range(2)]
        embed_text = [t for t in texts if "nomic-embed" in t][0]
        chat_text = [t for t in texts if "qwen3-2b" in t][0]
        self.assertIn("Embedding only", embed_text)
        self.assertNotIn("Embedding only", chat_text)

    def test_empty_folder_shows_no_models(self):
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        self.assertEqual(window.model_list.count(), 0)
        self.assertIn("0 models",
                      window.statusBar().currentMessage())

    def test_ollama_models_appear_with_a_fake_store(self):
        from helpers import make_fake_ollama_store
        store = self.tmp / "ollama-store"
        make_fake_ollama_store(store, [
            ("qwen3", "2b", {"general.architecture": "qwen3"}),
        ])
        import fyrn.discover as discover
        orig = discover.ollama_store_dirs
        discover.ollama_store_dirs = lambda: [store]
        try:
            window = self.make_window(include_ollama=True)
            self.assertTrue(self.wait_scan(window))
        finally:
            discover.ollama_store_dirs = orig
        self.assertEqual(window.model_list.count(), 1)
        text = window.model_list.item(0).text()
        self.assertIn("qwen3:2b", text)
        self.assertIn("Ollama", text)

    def test_click_selects_and_enables_load(self):
        make_fake_gguf(self.models / "a.gguf", 2048)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        window.model_list.item(0).setSelected(True)
        pump(50)
        self.assertTrue(window.load_button.isEnabled())
        self.assertIsNotNone(window._selected_path)

    def test_ticks_are_separate_from_selection(self):
        make_fake_gguf(self.models / "a.gguf", 2048)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        item = window.model_list.item(0)
        item.setCheckState(Qt.Checked)
        pump(50)
        self.assertEqual(window.model_list.selectedItems(), [])
        self.assertIsNone(window._selected_path)
        self.assertTrue(window.run_button.isEnabled())

    def test_ticks_survive_refresh(self):
        make_fake_gguf(self.models / "a.gguf", 2048)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        window.model_list.item(0).setCheckState(Qt.Checked)
        pump(50)
        window._start_scan()
        self.assertTrue(self.wait_scan(window))
        self.assertEqual(window.model_list.item(0).checkState(),
                         Qt.Checked)
        self.assertTrue(window.run_button.isEnabled())


class ChatTests(GuiTestCase):
    def _loaded_window(self, runner_factory=None):
        make_fake_gguf(self.models / "a.gguf", 2048)
        window = self.make_window(runner_factory=runner_factory)
        self.assertTrue(self.wait_scan(window))
        window.model_list.item(0).setSelected(True)
        pump(50)
        window.load_button.click()
        self.assertTrue(self.wait_loaded(window))
        return window

    def test_load_chat_and_unload(self):
        window = self._loaded_window()
        self.assertTrue(window.controller.is_loaded)
        self.assertIn("Loaded: a.gguf", window.loaded_label.text())
        self.assertTrue(window.chat_input.isEnabled())
        window.chat_input.setPlainText("Hello there")
        window.send_button.click()
        self.assertTrue(self.wait_chat_done(window))
        text = window.chat_view.toPlainText()
        self.assertIn("You: Hello there", text)
        self.assertIn("Hello from the fake model.", text)
        self.assertIn("tokens per second",
                      window.stats_label.text())
        window.unload_button.click()
        pump(50)
        self.assertFalse(window.controller.is_loaded)
        self.assertEqual(window.loaded_label.text(),
                         "Nothing loaded.")
        self.assertFalse(window.chat_input.isEnabled())

    def test_enter_sends_shift_enter_newline(self):
        window = self._loaded_window()
        window.activateWindow()
        window.chat_input.setFocus()
        QTest.keyClick(window.chat_input, Qt.Key_H)
        QTest.keyClick(window.chat_input, Qt.Key_I)
        QTest.keyClick(window.chat_input, Qt.Key_Return,
                       Qt.ShiftModifier)
        self.assertEqual(window.chat_input.toPlainText(), "hi\n")
        QTest.keyClick(window.chat_input, Qt.Key_Return)
        self.assertTrue(self.wait_chat_done(window))
        self.assertIn("You: hi",
                      window.chat_view.toPlainText())

    def test_load_failure_is_a_plain_message(self):
        window = self._loaded_window(
            runner_factory=lambda: FailingRunner())
        self.assertFalse(window.controller.is_loaded)
        self.assertEqual(window.loaded_label.text(),
                         "Nothing loaded.")
        self.assertTrue(wait_until(
            lambda: len(self.watcher.seen) >= 1, timeout_s=5.0),
            "no load-failure dialog appeared")
        self.assertTrue(any("a.gguf" in text
                            for _title, text in
                            self.watcher.seen),
                        "no load-failure dialog: %s"
                        % self.watcher.seen)
        self.assertTrue(any("boom" in text
                            for _title, text in
                            self.watcher.seen))

    def test_embedding_model_load_is_refused(self):
        write_gguf_file(
            self.models / "nomic-embed.gguf",
            {"general.architecture": "bert"})
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        window.model_list.item(0).setSelected(True)
        pump(50)
        window.load_button.click()
        self.assertTrue(wait_until(
            lambda: len(self.watcher.seen) >= 1, timeout_s=5.0),
            "no embedding refusal dialog appeared")
        self.assertTrue(any("can't write replies" in text
                            for _title, text in
                            self.watcher.seen),
                        "no refusal text: %s" % self.watcher.seen)
        self.assertTrue(any("nomic-embed.gguf" in text
                            for _title, text in
                            self.watcher.seen))
        self.assertFalse(window.controller.is_loaded)
        self.assertEqual(window.loaded_label.text(),
                         "Nothing loaded.")

    def test_memory_not_initialized_gets_plain_words(self):
        from fyrnchattest.gui import explain_reply_error
        message = explain_reply_error(
            "The reply stopped with an error (Memory is not "
            "initialized).")
        self.assertIn("couldn't start a reply", message)
        self.assertIn("Embedding models fail this way", message)
        self.assertNotIn("Memory is not initialized", message)
        plain = explain_reply_error("Something else broke.")
        self.assertEqual(plain, "Something else broke.")

    def test_chat_failure_shows_plain_words(self):
        window = self._loaded_window(
            runner_factory=lambda: MemoryErrorRunner())
        self.assertTrue(window.controller.is_loaded)
        window.chat_input.setPlainText("Hello there")
        window.send_button.click()
        self.assertTrue(self.wait_chat_done(window))
        text = window.chat_view.toPlainText()
        self.assertIn("couldn't start a reply", text)
        self.assertIn("Embedding models fail this way", text)
        self.assertNotIn("Memory is not initialized", text)

    def test_unknown_arch_gets_the_honest_explanation(self):
        from fyrnchattest.gui import explain_load_error
        message = explain_load_error(
            "The model could not be loaded (unknown model "
            "architecture: 'lfm3'). The file may be broken.")
        self.assertIn("does not know this model yet", message)
        self.assertIn("not that the file is broken", message)
        plain = explain_load_error("The file is gone.")
        self.assertEqual(plain, "The file is gone.")


class SmokeGuiTests(GuiTestCase):
    def _ticked_window(self, names, runner_factory=None):
        for name in names:
            make_fake_gguf(self.models / name, 2048)
        window = self.make_window(runner_factory=runner_factory)
        self.assertTrue(self.wait_scan(window))
        for row in range(window.model_list.count()):
            window.model_list.item(row).setCheckState(Qt.Checked)
        pump(50)
        return window

    def test_smoke_run_passes_and_saves_a_report(self):
        window = self._ticked_window(["a.gguf", "b.gguf"])
        window.run_button.click()
        self.assertTrue(self.wait_smoke_done(window))
        self.assertEqual(window.results_table.rowCount(), 2)
        verdicts = [window.results_table.item(row, 6).text()
                    for row in range(2)]
        self.assertEqual(verdicts, ["PASS", "PASS"])
        self.assertIn("2 passed",
                      window.smoke_status_label.text())
        self.assertIn("Report saved",
                      window.report_label.text())
        report_dir = Path(window._last_report_dir)
        self.assertTrue((report_dir / "report.md").is_file())
        self.assertTrue((report_dir / "report.json").is_file())
        payload = json.loads(
            (report_dir / "report.json").read_text(
                encoding="utf-8"))
        self.assertEqual(payload["summary"]["passed"], 2)
        self.assertFalse(window.controller.is_loaded)

    def test_smoke_run_skips_embedding_model(self):
        write_gguf_file(
            self.models / "a-embed.gguf",
            {"general.architecture": "bert"})
        make_fake_gguf(self.models / "b-chat.gguf", 2048)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        for row in range(window.model_list.count()):
            window.model_list.item(row).setCheckState(Qt.Checked)
        pump(50)
        window.run_button.click()
        self.assertTrue(self.wait_smoke_done(window))
        verdicts = [window.results_table.item(row, 6).text()
                    for row in range(2)]
        self.assertEqual(verdicts, ["NOT TESTED", "PASS"])
        self.assertEqual(window.results_table.item(0, 1).text(),
                         "-")
        self.assertIn("1 not tested",
                      window.smoke_status_label.text())
        window.results_table.selectRow(0)
        pump(50)
        self.assertIn("Embedding only",
                      window.detail_box.toPlainText())
        report_dir = Path(window._last_report_dir)
        md = (report_dir / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("Embedding only", md)
        payload = json.loads(
            (report_dir / "report.json").read_text(
                encoding="utf-8"))
        self.assertEqual(payload["summary"]["not_tested"], 1)

    def test_smoke_run_with_a_failing_model(self):
        window = self._ticked_window(
            ["a.gguf"], runner_factory=lambda: FailingRunner())
        window.run_button.click()
        self.assertTrue(self.wait_smoke_done(window))
        self.assertEqual(window.results_table.item(0, 6).text(),
                         "FAIL")
        self.assertEqual(window.results_table.item(0, 1).text(),
                         "failed")
        self.assertIn("1 failed",
                      window.smoke_status_label.text())

    def test_smoke_cancel_marks_the_rest_not_tested(self):
        slow = lambda: FakeRunner(  # noqa: E731
            reply_text=" ".join(["word"] * 30),
            token_delay_s=0.05)
        window = self._ticked_window(["a.gguf", "b.gguf"],
                                     runner_factory=slow)
        window.run_button.click()
        pump(100)
        window.cancel_button.click()
        self.assertTrue(self.wait_smoke_done(window))
        verdicts = [window.results_table.item(row, 6).text()
                    for row in range(2)]
        self.assertIn("NOT TESTED", verdicts)
        self.assertFalse(window.controller.is_loaded)
        self.assertIsNotNone(window._last_report_dir)

    def test_result_click_shows_the_detail(self):
        window = self._ticked_window(["a.gguf"])
        window.run_button.click()
        self.assertTrue(self.wait_smoke_done(window))
        window.results_table.selectRow(0)
        pump(50)
        detail = window.detail_box.toPlainText()
        self.assertIn("Say hello", detail)
        self.assertIn("Hello from the fake model.", detail)

    def test_run_disabled_while_a_chat_model_is_loaded(self):
        make_fake_gguf(self.models / "a.gguf", 2048)
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        window.model_list.item(0).setCheckState(Qt.Checked)
        window.model_list.item(0).setSelected(True)
        pump(50)
        window.load_button.click()
        self.assertTrue(self.wait_loaded(window))
        self.assertTrue(
            window.controller.is_loaded,
            "the load did not complete: dialogs seen: %s"
            % self.watcher.seen)
        self.assertFalse(window.run_button.isEnabled())
        self.assertIn("Unload the chat model",
                      window.smoke_hint_label.text())


class ChromeTests(GuiTestCase):
    def test_every_button_has_a_tooltip(self):
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        bare = []
        for button in window.findChildren(QPushButton):
            if not button.toolTip():
                bare.append(button.text() or str(button))
        self.assertEqual(bare, [])

    def test_no_em_dash_in_visible_ui_text(self):
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        bad = []
        for widget in window.findChildren(QWidget):
            for getter in ("text", "placeholderText", "toolTip",
                           "windowTitle"):
                method = getattr(widget, getter, None)
                if method is None:
                    continue
                try:
                    value = method()
                except Exception:
                    continue
                if isinstance(value, str) and "\u2014" in value:
                    bad.append("%s: %s" % (getter, value))
        for action in window.menuBar().actions():
            if "\u2014" in action.text():
                bad.append("menu: %s" % action.text())
        self.assertEqual(bad, [])

    def test_how_to_use_opens(self):
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        from fyrnchattest.gui import HOW_TO_USE, HowToDialog
        seen = []

        def _close():
            # Runs inside the modal loop: the dialog is modal,
            # so a plain call to _show_how_to_use blocks until
            # this timer closes it.
            for widget in QApplication.instance().topLevelWidgets():
                if isinstance(widget, HowToDialog) \
                        and widget.isVisible():
                    seen.append(widget.windowTitle())
                    widget.accept()

        QTimer.singleShot(300, _close)
        window._show_how_to_use()
        self.assertEqual(seen, ["How to Use Fyrn Chat Test"])
        self.assertIn("Smoke test", HOW_TO_USE)
        self.assertIn("Shift and Enter", HOW_TO_USE)

    def test_window_geometry_is_remembered(self):
        window = self.make_window()
        self.assertTrue(self.wait_scan(window))
        # Inside the offscreen screen (800x600): a bigger rect
        # would be clamped on show, and the test would measure
        # the clamp, not the memory.
        window.setGeometry(40, 50, 700, 500)
        window.close()
        pump(100)
        second = self.make_window()
        self.assertTrue(self.wait_scan(second))
        geometry = second.geometry()
        self.assertEqual((geometry.x(), geometry.y(),
                          geometry.width(), geometry.height()),
                         (40, 50, 700, 500))

    def test_fyrn_models_folder_reads_from_fyrn_settings(self):
        import os
        settings_path = self.tmp / "fyrn-settings.ini"
        settings_path.write_text(
            "[General]\nmodels_folder=%s\n"
            % (self.tmp / "Elsewhere"),
            encoding="utf-8")
        os.environ["FYRN_SETTINGS_FILE"] = str(settings_path)
        from fyrnchattest.gui import get_fyrn_models_folder
        self.assertEqual(get_fyrn_models_folder(),
                         self.tmp / "Elsewhere")


class UpdateTests(GuiTestCase):
    """The update check: a popup when a newer release is out,
    silence when up to date or offline, a verified download,
    and the restart choice (now, or staged for next launch).
    The network is always a stub, and the install always lands
    in a temp app folder, never the real tree."""

    def _clicking_timer(self, clicks):
        """A repeating timer that clicks dialog buttons by
        title: clicks maps a dialog title to the standard
        button to press. Armed before the window exists, so no
        dialog can appear before it. Returns (timer, seen):
        seen collects (title, text) of every dialog that
        appeared. A dialog with no listed button gets its
        default button: a stray dialog must fail the test, not
        hang it inside a nested event loop."""
        seen = []
        timer = QTimer()
        timer.setInterval(50)

        def _sweep():
            app = QApplication.instance()
            for widget in app.topLevelWidgets():
                if not isinstance(widget, QMessageBox):
                    continue
                if not widget.isVisible():
                    continue
                title = widget.windowTitle()
                if (title, widget.text()) not in seen:
                    seen.append((title, widget.text()))
                if title in clicks:
                    button = widget.button(clicks[title])
                else:
                    button = widget.defaultButton()
                if button is not None:
                    button.click()

        timer.timeout.connect(_sweep)
        timer.start()
        self.addCleanup(timer.stop)
        return timer, seen

    def _wait_check_done(self, window, stub, timeout_s=10.0):
        """Wait for the launch check to have run and finished:
        the stub saw the request and the worker is gone."""
        return wait_until(
            lambda: updates.LATEST_RELEASE_URL in stub.requested
            and window._update_check_worker is None,
            timeout_s)

    def test_update_available_shows_popup(self):
        stub = make_update_stub(self.tmp)
        self.watcher.enabled = False
        _timer, seen = self._clicking_timer(
            {"Update available": QMessageBox.No})
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(wait_until(lambda: len(seen) >= 1,
                                   timeout_s=10.0),
                        "no update popup appeared")
        title, text = seen[0]
        self.assertEqual(title, "Update available")
        self.assertIn("0.1.2", text)
        self.assertIn("checksum", text)
        self.assertIn("Update skipped",
                      window.statusBar().currentMessage())
        # No means no download: nothing staged.
        self.assertFalse(
            (self.home / "updates").exists())
        self.assertTrue(window.isVisible())

    def test_update_download_install_and_restart(self):
        stub = make_update_stub(self.tmp)
        app_dir = make_fake_app_dir(self.tmp / "app", "0.1.1")
        os.environ["FYRN_CHAT_TEST_APP_DIR"] = str(app_dir)
        calls = []
        patcher = mock.patch.object(
            updates, "schedule_restart",
            side_effect=lambda *a, **k: calls.append(True)
            or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The watcher clicks Yes on both popups (both default
        # to Yes): download, then restart now.
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(wait_until(lambda: len(calls) >= 1,
                                   timeout_s=10.0),
                        "the restart was never scheduled; "
                        "dialogs seen: %s" % self.watcher.seen)
        self.assertTrue(wait_until(
            lambda: not window.isVisible(), timeout_s=5.0))
        init = (app_dir / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.2"', init)
        self.assertTrue(os.access(app_dir / "install.sh",
                                  os.X_OK))
        # Installed, so nothing stays staged.
        self.assertFalse((self.home / "updates").exists())

    def test_update_restart_no_stages_for_next_launch(self):
        stub = make_update_stub(self.tmp)
        app_dir = make_fake_app_dir(self.tmp / "app", "0.1.1")
        os.environ["FYRN_CHAT_TEST_APP_DIR"] = str(app_dir)
        self.watcher.enabled = False
        _timer, seen = self._clicking_timer({
            "Update available": QMessageBox.Yes,
            "Update downloaded": QMessageBox.No,
        })
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(wait_until(
            lambda: "Update downloaded" in [t for t, _ in seen],
            timeout_s=10.0),
            "no restart popup appeared; seen: %s" % seen)
        pump(200)
        staged = self.home / "updates"
        self.assertTrue(
            (staged / "fyrn-chat-test-v0.1.2.zip").is_file())
        marker = json.loads(
            (staged / "staged.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "0.1.2")
        # Not installed yet: the app folder is untouched.
        init = (app_dir / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)
        self.assertTrue(window.isVisible())
        self.assertIn("next time Fyrn Chat Test starts",
                      window.statusBar().currentMessage())
        # And the staged update applies cleanly at next launch.
        applied = updates.apply_staged_update(self.home, app_dir)
        self.assertEqual(applied, "0.1.2")
        init = (app_dir / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.2"', init)

    def test_up_to_date_shows_no_popup(self):
        payload = make_release_payload(tag="v0.1.1")
        stub = FakeUrlopen({
            updates.LATEST_RELEASE_URL:
                json.dumps(payload).encode("utf-8"),
        })
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(self._wait_check_done(window, stub))
        pump(200)
        self.assertEqual(self.watcher.seen, [])
        self.assertTrue(self.wait_scan(window))

    def test_offline_shows_no_popup(self):
        import urllib.error
        stub = FakeUrlopen({
            updates.LATEST_RELEASE_URL:
                urllib.error.URLError("no network"),
        })
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(self._wait_check_done(window, stub))
        pump(200)
        self.assertEqual(self.watcher.seen, [])
        self.assertTrue(self.wait_scan(window))

    def test_checksum_mismatch_is_refused_plainly(self):
        stub = make_update_stub(self.tmp, sha="0" * 64)
        app_dir = make_fake_app_dir(self.tmp / "app", "0.1.1")
        os.environ["FYRN_CHAT_TEST_APP_DIR"] = str(app_dir)
        # The watcher clicks Yes on the download popup; the
        # verify fails and the refusal dialog follows.
        window = self.make_window(check_updates=True,
                                  update_urlopen=stub)
        self.assertTrue(wait_until(
            lambda: any("checksum" in text
                        for _title, text in self.watcher.seen),
            timeout_s=10.0),
            "no checksum refusal appeared; seen: %s"
            % self.watcher.seen)
        pump(200)
        self.assertFalse((self.home / "updates"
                          / "fyrn-chat-test-v0.1.2.zip").exists())
        init = (app_dir / "fyrnchattest"
                / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"0.1.1"', init)
        self.assertTrue(window.isVisible())

    def _trigger_menu(self, window, menu_text, action_text):
        for action in window.menuBar().actions():
            if action.text() != menu_text:
                continue
            for item in action.menu().actions():
                if item.text() == action_text:
                    item.trigger()
                    return True
        return False

    def test_manual_check_reports_up_to_date(self):
        payload = make_release_payload(tag="v0.1.1")
        stub = FakeUrlopen({
            updates.LATEST_RELEASE_URL:
                json.dumps(payload).encode("utf-8"),
        })
        window = self.make_window(check_updates=False,
                                  update_urlopen=stub)
        self.assertTrue(self.wait_scan(window))
        self.assertTrue(self._trigger_menu(
            window, "Help", "Check for updates"))
        self.assertTrue(wait_until(
            lambda: any(title == "Up to date"
                        for title, _text in self.watcher.seen),
            timeout_s=10.0),
            "no up-to-date dialog appeared; seen: %s"
            % self.watcher.seen)

    def test_manual_check_offline_says_so(self):
        import urllib.error
        stub = FakeUrlopen({
            updates.LATEST_RELEASE_URL:
                urllib.error.URLError("no network"),
        })
        window = self.make_window(check_updates=False,
                                  update_urlopen=stub)
        self.assertTrue(self.wait_scan(window))
        self.assertTrue(self._trigger_menu(
            window, "Help", "Check for updates"))
        self.assertTrue(wait_until(
            lambda: any(title == "Couldn't check for updates"
                        for title, _text in self.watcher.seen),
            timeout_s=10.0),
            "no offline dialog appeared; seen: %s"
            % self.watcher.seen)


if __name__ == "__main__":
    unittest.main()
