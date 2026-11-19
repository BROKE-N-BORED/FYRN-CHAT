"""The test bench window.

Left: the models Fyrn sees, with a tick box per model for the
smoke test. Click a model to chat with it. Right: two tabs.
Chat loads one model and talks to it, streaming, with live
speed and RAM. Smoke test loads each ticked model in turn,
asks it the two canned questions, and records pass or fail
with a report saved under the tool's own reports folder.

One model at a time, always: the chat side and the smoke side
share one controller, and the UI gates the other side while
one is busy. Nothing is selected by default, ever.

The engine is the installed Fyrn's: fyrn.core.ModelController
and fyrn.runner.LlamaRunner, imported from the Fyrn install
__main__ put on sys.path. Tests inject a runner factory that
builds fyrn.runner.FakeRunner instead, so the suite never
needs a real model.
"""

import os
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QRect, QSettings, Qt, QThread, QTimer,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QIcon,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractItemView, QApplication,
                               QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox,
                               QPushButton, QProgressBar,
                               QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget,
                               QTextEdit, QVBoxLayout, QWidget)

from fyrn.core import (ModelController, ModelNotFoundError,
                       ModelTooBigError, format_size, read_ram_bytes)
from fyrn.runner import (GenerationCancelled, LlamaRunner,
                         RunnerError, trim_history)

from . import __version__, updates
from .finder import ensure_home, fyrn_is_running, get_home
from .models import (format_model_line, is_embedding_model,
                      list_models)
from .reports import new_run_dir, reports_dir, write_report
from .smoke import PROMPTS, run_smoke_test, summarize
from .theme import DARK, build_stylesheet, verdict_badges

SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "fyrn-chat-test"

# Fyrn's settings, read only: the tool reads Fyrn's models
# folder from here and never writes to it.
FYRN_SETTINGS_ORG = "BrokeNBored"
FYRN_SETTINGS_APP = "fyrn"

STYLESHEET = build_stylesheet(DARK)

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / \
    "fyrn-chat-test.png"

HOW_TO_USE = """Fyrn Chat Test: chat with the models on this PC through Fyrn, and smoke test them.

WHAT IT TESTS
Fyrn Chat Test runs on the Fyrn installed on this PC. It finds Fyrn, loads models through Fyrn's own engine, and never touches Ollama. If Fyrn is not installed, it says so and stops.

THE MODEL LIST (left)
These are the models Fyrn sees: Fyrn's own models folder, plus models already in Ollama's store. Each row shows the size, the facts from the file's own header, whether it fits this PC, and where it came from.
- Click a model to pick it for a chat. Nothing is picked until you click.
- Tick a model to include it in a smoke test. Tick and click are separate: ticking does not pick, clicking does not tick.
- Refresh re-reads the folders. Ticks stay on the models you ticked.
- A model marked "Embedding only" turns text into numbers for search: it cannot write replies, so Load refuses it and the smoke test skips it instead of failing it.

CHAT (right, Chat tab)
1. Click a model, then press Load. Loading a big model takes a while: the bar shows what is happening.
2. Type in the box and press Enter to send. Shift and Enter together starts a new line without sending.
3. The reply streams in as the model writes it. Stop cuts a reply short. Unload frees the RAM.
4. The line under the chat shows the speed of the last reply in tokens per second, and the free RAM.

If a model fails to load, the message says which model and what went wrong. Some models need a newer Fyrn engine and fail with "unknown model architecture": that means Fyrn's engine does not know the model yet, not that the file is broken.

SMOKE TEST (right, Smoke test tab)
1. Tick one or more models in the list.
2. Press Run smoke test. Each model loads, answers two fixed questions, and unloads, one at a time.
3. The table fills in as each model finishes: PASS means it loaded and answered both questions. FAIL means it did not, and the detail box under the table says why.
4. Cancel stops after the current model. Models not yet started are marked NOT TESTED, not failed.
5. Every run saves a report (report.md and report.json) under the tool's reports folder. File menu, Open reports folder, shows them.

TIPS
- Close Fyrn itself before testing: two programs loading models at once share the same 8 GB of RAM.
- A smoke test takes a while: each model loads fully, and each question can take up to three minutes before it counts as a failure.
- File menu, Debug report, writes a report file with the log and the PC details, with any secrets removed. Send that file when asking for help.

UPDATES
Fyrn Chat Test checks for a newer version when it starts. If one is out, it asks before downloading anything, checks the download against its checksum, and asks again before restarting. Saying no to the restart finishes the update the next time the app starts. Help menu, Check for updates, checks now.
"""

ABOUT_TEXT = ("Fyrn Chat Test %s\n\n"
              "Chat with the models on this PC through Fyrn, and "
              "smoke test them.\n\n"
              "Testing Fyrn %s at:\n%s")


# ---------------------------------------------------------------- settings


def get_settings():
    """This tool's settings (window geometry only). Tests point
    FYRN_CHAT_TEST_SETTINGS_FILE at a temp file so they never
    touch the real settings."""
    override = os.environ.get("FYRN_CHAT_TEST_SETTINGS_FILE")
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def get_fyrn_models_folder():
    """Fyrn's models folder, read from Fyrn's settings.

    Read-only: this never writes to Fyrn's settings. Fyrn's own
    FYRN_SETTINGS_FILE override is respected, so tests can point
    Fyrn's settings at a temp file. Default is ~/Models.
    """
    override = os.environ.get("FYRN_SETTINGS_FILE")
    if override:
        settings = QSettings(override, QSettings.Format.IniFormat)
    else:
        settings = QSettings(FYRN_SETTINGS_ORG, FYRN_SETTINGS_APP)
    value = settings.value("models_folder", "")
    if value:
        return Path(value)
    return Path.home() / "Models"


def save_window_geometry(settings, prefix, widget):
    """Save a window's geometry under settings prefix/x, y,
    width, height (windows remember their size and position)."""
    rect = widget.geometry()
    settings.setValue("%s/x" % prefix, rect.x())
    settings.setValue("%s/y" % prefix, rect.y())
    settings.setValue("%s/width" % prefix, rect.width())
    settings.setValue("%s/height" % prefix, rect.height())
    settings.sync()


def restore_window_geometry(settings, prefix, widget):
    """Restore a window's geometry saved by
    save_window_geometry. Does nothing when nothing is saved,
    or when the saved rect is off every screen."""
    values = [settings.value("%s/%s" % (prefix, key), type=int)
              for key in ("x", "y", "width", "height")]
    if any(value is None for value in values):
        return
    rect = QRect(*values)
    screens = QGuiApplication.screens()
    if screens and not any(
            screen.availableGeometry().intersects(rect)
            for screen in screens):
        return
    widget.setGeometry(rect)


def explain_load_error(message):
    """A load failure in plainer words. Fyrn's own message says
    the file may be broken, which misleads when the engine
    simply does not know the model's architecture: that case
    gets the honest explanation appended."""
    if "unknown model architecture" in message:
        return (message + "\n\nThat means Fyrn's engine does not "
                "know this model yet, not that the file is "
                "broken. The model needs a newer Fyrn engine.")
    return message


def explain_reply_error(message):
    """A failed reply in plainer words. "Memory is not
    initialized" is the engine's way of saying the model has
    no reply memory: that is how embedding models fail, because
    they have no decoder and cannot write replies at all."""
    if "Memory is not initialized" in message:
        return ("The model couldn't start a reply (the engine's "
                "reply memory isn't initialized). Embedding "
                "models fail this way. They can't write "
                "replies.")
    return message


# ---------------------------------------------------------------- workers


class ScanWorker(QThread):
    """Scan the models folder and Ollama's store off the UI
    thread: the disk scan is quick, but it runs on a spinning
    hard drive, so it never blocks the window."""

    finished = Signal(list, list, list, object)
    # models, skipped, warnings, free_bytes

    def __init__(self, folder, include_ollama=True):
        super().__init__()
        self.folder = Path(folder)
        self.include_ollama = include_ollama

    def run(self):
        import fyrn as fyrn_pkg
        models, skipped, warnings = list_models(
            self.folder, include_ollama=self.include_ollama,
            fyrn_pkg=fyrn_pkg)
        _total, free = read_ram_bytes()
        self.finished.emit(models, skipped, warnings, free)


class LoadWorker(QThread):
    """Load a model off the UI thread."""

    progress = Signal(int, str)  # percent (-1: indeterminate), message
    finished = Signal(dict)      # {"load_time_s": float, ...}
    failed = Signal(str)         # plain-words message

    def __init__(self, controller, path, display_name=None):
        super().__init__()
        self.controller = controller
        self.path = path
        self.display_name = display_name
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.controller.load(
                self.path,
                progress_cb=lambda pct, msg: self.progress.emit(
                    pct, msg),
                cancel_event=self.cancel_event,
                display_name=self.display_name)
        except (ModelTooBigError, ModelNotFoundError) as exc:
            self.failed.emit(str(exc))
        except RunnerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never crash the thread silently
            self.failed.emit("The model could not be loaded (%s)."
                             % exc)
        else:
            self.finished.emit(result)

    def cancel(self):
        self.cancel_event.set()


class ChatWorker(QThread):
    """Stream one reply off the UI thread."""

    token = Signal(str)
    finished = Signal(dict)  # generation stats
    failed = Signal(str)

    def __init__(self, runner, prompt, history=None):
        super().__init__()
        self.runner = runner
        self.prompt = prompt
        self.history = list(history or [])
        self.cancel_event = threading.Event()

    def run(self):
        try:
            stats = self.runner.generate(
                self.prompt,
                on_token=lambda text: self.token.emit(text),
                cancel_event=self.cancel_event,
                history=self.history)
        except GenerationCancelled:
            self.finished.emit({"stopped": True})
        except RunnerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit("The reply stopped with an error (%s)."
                             % exc)
        else:
            stats["stopped"] = False
            self.finished.emit(stats)

    def cancel(self):
        self.cancel_event.set()


class SmokeWorker(QThread):
    """Run a smoke test off the UI thread: each model loads
    fully, so a run takes minutes, and the window must stay
    alive, with progress and a cancel, while it runs."""

    progress = Signal(str, int, str, str)
    # kind, model index, model name, detail
    model_done = Signal(int, dict)  # model index, result
    finished = Signal(list)         # all results

    def __init__(self, models, controller, prompt_timeout_s=None):
        super().__init__()
        self.models = list(models)
        self.controller = controller
        self.prompt_timeout_s = prompt_timeout_s
        self.cancel_event = threading.Event()

    def run(self):
        results = run_smoke_test(
            self.models, self.controller,
            on_progress=lambda k, i, n, d: self.progress.emit(
                k, i, n, d),
            on_model_done=lambda i, r: self.model_done.emit(i, r),
            user_cancel=self.cancel_event,
            prompt_timeout_s=self.prompt_timeout_s)
        self.finished.emit(results)

    def cancel(self):
        self.cancel_event.set()


class UpdateCheckWorker(QThread):
    """Check GitHub for a newer release off the UI thread: a
    slow or absent network must never delay startup."""

    finished = Signal(dict)  # check_for_update's result

    def __init__(self, current_version, urlopen=None):
        super().__init__()
        self.current_version = current_version
        self.urlopen = urlopen

    def run(self):
        self.finished.emit(updates.check_for_update(
            self.current_version, urlopen=self.urlopen))


class UpdateDownloadWorker(QThread):
    """Download and verify a release off the UI thread."""

    progress = Signal(int, object)  # bytes downloaded, total or None
    finished = Signal(object)       # the verified zip path
    cancelled = Signal()
    failed = Signal(str)            # plain-words message

    def __init__(self, release, dest_dir, urlopen=None):
        super().__init__()
        self.release = release
        self.dest_dir = dest_dir
        self.urlopen = urlopen
        self.cancel_event = threading.Event()

    def run(self):
        try:
            path = updates.download_and_verify(
                self.release, self.dest_dir,
                urlopen=self.urlopen,
                progress_cb=lambda done, total:
                    self.progress.emit(done, total),
                cancel_event=self.cancel_event)
        except updates.UpdateCancelled:
            self.cancelled.emit()
        except updates.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never crash the thread silently
            self.failed.emit("The update could not be downloaded "
                             "(%s)." % exc)
        else:
            self.finished.emit(path)

    def cancel(self):
        self.cancel_event.set()


class RaiseListener(QThread):
    """Listens on ~/.fyrn-chat-test/raise.sock for the pre-Qt
    fast path (fyrnchattest/__main__.py): a second launch sends
    b"raise" here, and this thread emits raise_requested, which
    the main window connects to bring_to_front. Only the first
    instance runs one: it holds the fcntl lock, so a stale
    socket file from a crashed run is safe to remove here."""

    raise_requested = Signal()

    def __init__(self, sock_path, parent=None):
        super().__init__(parent)
        self._sock_path = Path(sock_path)
        self._server = None

    def run(self):
        import socket
        try:
            self._sock_path.unlink()
        except OSError:
            pass  # no stale socket, or it went away on its own
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(str(self._sock_path))
        except OSError:
            return  # another listener won the race; nothing to do
        server.listen(1)
        server.settimeout(0.5)
        while not self.isInterruptionRequested():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                if conn.recv(16):
                    self.raise_requested.emit()
            except OSError:
                pass
            finally:
                conn.close()
        try:
            server.close()
            self._sock_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- widgets


class ChatInput(QTextEdit):
    """The chat input: Enter sends, Shift and Enter together
    starts a new line. Grows from 1 to 4 lines as it fills."""

    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not event.modifiers() & Qt.ShiftModifier:
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class HowToDialog(QDialog):
    """The How to Use index, from the Help menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("How to Use Fyrn Chat Test")
        self.resize(620, 520)
        layout = QVBoxLayout(self)
        text = QTextEdit(self)
        text.setReadOnly(True)
        text.setPlainText(HOW_TO_USE)
        layout.addWidget(text)
        close_button = QPushButton("Close", self)
        close_button.setToolTip("Close this window")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)


# ---------------------------------------------------------------- main window


# Workers still running when their close wait gives up are
# parked here. A QThread object must never be destroyed
# while its thread is running: Qt aborts the whole process
# ("QThread: Destroyed while thread is still running"). The
# bounded waits in closeEvent are normally plenty, but on a
# slow or loaded machine a worker can outlive its wait; the
# park keeps the object alive until its thread ends on its
# own, so closing the window can never take the app down.
_PARKED_WORKERS = []


def _wait_for_worker(worker, timeout_ms):
    """Wait up to timeout_ms for a worker thread to finish,
    and park it if it is still running after that."""
    if worker is None:
        return
    worker.wait(timeout_ms)
    if worker.isRunning():
        _PARKED_WORKERS.append(worker)


class MainWindow(QMainWindow):
    """The test bench: model list on the left, Chat and Smoke
    test tabs on the right."""

    def __init__(self, fyrn_install, runner_factory=None,
                 models_folder=None, parent=None, log=None,
                 include_ollama=True, check_updates=True,
                 update_urlopen=None):
        super().__init__(parent)
        self.fyrn_install = fyrn_install
        self.runner_factory = runner_factory or LlamaRunner
        self.models_folder = (Path(models_folder) if models_folder
                              else get_fyrn_models_folder())
        self.include_ollama = include_ollama
        # The update check runs on launch by default; tests turn
        # it off (or hand it a stub opener) so the suite never
        # touches the network.
        self._check_updates = check_updates
        self._update_urlopen = update_urlopen
        self._log = log
        self._home = ensure_home()
        self._settings = get_settings()

        # One controller, one model at a time: the chat side and
        # the smoke side share it, and the UI gates the other
        # side while one is busy.
        self.controller = ModelController(self.runner_factory())
        self._history = []
        self._models = []
        self._free_bytes = None
        self._selected_path = None
        self._ticked_paths = set()
        self._scan_worker = None
        self._load_worker = None
        self._chat_worker = None
        self._smoke_worker = None
        self._update_check_worker = None
        self._update_download_worker = None
        self._update_release = None
        self._update_manual = False
        self._smoke_models = []
        self._smoke_results = []
        self._last_report_dir = None
        self._chat_streaming = False

        self.setWindowTitle("Fyrn Chat Test")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1100, 680)
        restore_window_geometry(self._settings, "main", self)

        self._build_menus()
        self._build_widgets()
        self._update_buttons()
        self._start_scan()
        self._ram_timer = QTimer(self)
        self._ram_timer.timeout.connect(self._update_ram_label)
        self._ram_timer.start(2000)
        self._update_ram_label()
        if self._check_updates:
            # The update check starts after the window shows: a
            # slow or absent network must never delay startup.
            QTimer.singleShot(0, self._start_update_check)

    # ---------------------------------------------------------------- build

    def _build_menus(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")
        refresh_action = file_menu.addAction(
            "Refresh model list\tF5")
        refresh_action.setToolTip("Re-read the models folder and "
                                  "Ollama's store")
        refresh_action.triggered.connect(self._start_scan)
        reports_action = file_menu.addAction("Open reports folder")
        reports_action.setToolTip("Show the saved smoke test "
                                  "reports")
        reports_action.triggered.connect(self._open_reports_folder)
        debug_action = file_menu.addAction("Debug report")
        debug_action.setToolTip("Write a report file with the log "
                                "and the PC details")
        debug_action.triggered.connect(self._write_debug_report)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Quit\tCtrl+Q")
        quit_action.setToolTip("Close Fyrn Chat Test")
        quit_action.triggered.connect(self.close)
        help_menu = menu_bar.addMenu("Help")
        howto_action = help_menu.addAction("How to Use\tF1")
        howto_action.setToolTip("How to chat and smoke test")
        howto_action.triggered.connect(self._show_how_to_use)
        check_action = help_menu.addAction("Check for updates")
        check_action.setToolTip(
            "Check GitHub for a newer version")
        check_action.triggered.connect(
            self._on_check_updates_activated)
        about_action = help_menu.addAction("About Fyrn Chat Test")
        about_action.setToolTip("Version, and which Fyrn this "
                                "tests")
        about_action.triggered.connect(self._show_about)

    def _build_widgets(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Fyrn itself running is worth a plain note, not a block:
        # two programs loading models share the same RAM.
        self.fyrn_running_label = QLabel(
            "Fyrn is running too. Close it first to free RAM for "
            "testing.", self)
        self.fyrn_running_label.setWordWrap(True)
        self.fyrn_running_label.setVisible(fyrn_is_running())
        root.addWidget(self.fyrn_running_label)

        splitter = QSplitter(Qt.Horizontal, self)
        root.addWidget(splitter, 1)

        # Left: the model list.
        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        list_title = QLabel("Models Fyrn sees", self)
        list_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(list_title)
        list_help = QLabel(
            "Tick models for the smoke test. Click a model to "
            "chat with it.", self)
        list_help.setWordWrap(True)
        left_layout.addWidget(list_help)
        self.model_list = QListWidget(self)
        self.model_list.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self.model_list.setToolTip(
            "The models Fyrn sees. Tick for the smoke test, "
            "click to chat.")
        self.model_list.itemSelectionChanged.connect(
            self._on_selection_changed)
        self.model_list.itemChanged.connect(self._on_item_changed)
        left_layout.addWidget(self.model_list, 1)
        self.skipped_label = QLabel(self)
        self.skipped_label.setWordWrap(True)
        self.skipped_label.setVisible(False)
        left_layout.addWidget(self.skipped_label)
        tick_row = QHBoxLayout()
        self.select_all_button = QPushButton("Tick all", self)
        self.select_all_button.setToolTip(
            "Tick every model for the smoke test")
        self.select_all_button.clicked.connect(
            lambda: self._set_all_ticks(True))
        tick_row.addWidget(self.select_all_button)
        self.clear_ticks_button = QPushButton("Untick all", self)
        self.clear_ticks_button.setToolTip(
            "Untick every model")
        self.clear_ticks_button.clicked.connect(
            lambda: self._set_all_ticks(False))
        tick_row.addWidget(self.clear_ticks_button)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setToolTip(
            "Re-read the models folder and Ollama's store (F5)")
        self.refresh_button.clicked.connect(self._start_scan)
        tick_row.addWidget(self.refresh_button)
        left_layout.addLayout(tick_row)
        splitter.addWidget(left)

        # Right: Chat and Smoke test tabs.
        self.tabs = QTabWidget(self)
        splitter.addWidget(self.tabs)
        splitter.setSizes([380, 720])

        self._build_chat_tab()
        self._build_smoke_tab()

        self.statusBar().showMessage(
            "Pick a model and press Load, or tick models and run "
            "a smoke test.")
        self.ram_label = QLabel(self)
        self.statusBar().addPermanentWidget(self.ram_label)

    def _build_chat_tab(self):
        chat_tab = QWidget(self)
        layout = QVBoxLayout(chat_tab)
        load_row = QHBoxLayout()
        self.load_button = QPushButton("Load", self)
        self.load_button.setToolTip(
            "Load the clicked model for a chat (Ctrl+L)")
        self.load_button.clicked.connect(self._on_load_clicked)
        load_row.addWidget(self.load_button)
        self.unload_button = QPushButton("Unload", self)
        self.unload_button.setToolTip(
            "Unload the model and free its RAM (Ctrl+U)")
        self.unload_button.clicked.connect(self._on_unload_clicked)
        load_row.addWidget(self.unload_button)
        self.loaded_label = QLabel("Nothing loaded.", self)
        self.loaded_label.setWordWrap(True)
        load_row.addWidget(self.loaded_label, 1)
        layout.addLayout(load_row)
        self.chat_view = QTextEdit(self)
        self.chat_view.setReadOnly(True)
        self.chat_view.setToolTip("The chat with the loaded model")
        layout.addWidget(self.chat_view, 1)
        self.stats_label = QLabel(self)
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)
        input_row = QHBoxLayout()
        self.chat_input = ChatInput(self)
        self.chat_input.setPlaceholderText(
            "Type a message. Enter sends, Shift and Enter starts "
            "a new line.")
        self.chat_input.setToolTip(
            "Type a message. Enter sends, Shift and Enter starts "
            "a new line.")
        self.chat_input.setMaximumHeight(96)
        self.chat_input.send_requested.connect(self._on_send_clicked)
        input_row.addWidget(self.chat_input, 1)
        self.send_button = QPushButton("Send", self)
        self.send_button.setToolTip("Send this message (Enter)")
        self.send_button.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.send_button)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setToolTip("Stop the reply")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        input_row.addWidget(self.stop_button)
        layout.addLayout(input_row)
        self.load_progress = QProgressBar(self)
        self.load_progress.setVisible(False)
        layout.addWidget(self.load_progress)
        self.tabs.addTab(chat_tab, "Chat")

    def _build_smoke_tab(self):
        smoke_tab = QWidget(self)
        layout = QVBoxLayout(smoke_tab)
        smoke_help = QLabel(
            "Each ticked model loads, answers two fixed "
            "questions, and unloads. A report is saved for "
            "every run.", self)
        smoke_help.setWordWrap(True)
        layout.addWidget(smoke_help)
        run_row = QHBoxLayout()
        self.run_button = QPushButton("Run smoke test", self)
        self.run_button.setToolTip(
            "Test every ticked model, one at a time")
        self.run_button.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.run_button)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setToolTip(
            "Stop after the current model")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        run_row.addWidget(self.cancel_button)
        self.smoke_hint_label = QLabel(self)
        self.smoke_hint_label.setWordWrap(True)
        run_row.addWidget(self.smoke_hint_label, 1)
        layout.addLayout(run_row)
        self.smoke_progress = QProgressBar(self)
        self.smoke_progress.setVisible(False)
        layout.addWidget(self.smoke_progress)
        self.smoke_status_label = QLabel(self)
        self.smoke_status_label.setWordWrap(True)
        layout.addWidget(self.smoke_status_label)
        self.results_table = QTableWidget(0, 7, self)
        self.results_table.setHorizontalHeaderLabels(
            ["Model", "Load", "Prompt 1", "Prompt 2",
             "First token", "Speed", "Verdict"])
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectRows)
        self.results_table.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self.results_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self.results_table.setToolTip(
            "One row per tested model. Click a row to see what "
            "it said.")
        self.results_table.itemSelectionChanged.connect(
            self._on_result_selected)
        layout.addWidget(self.results_table, 1)
        self.detail_box = QTextEdit(self)
        self.detail_box.setReadOnly(True)
        self.detail_box.setMaximumHeight(140)
        self.detail_box.setToolTip(
            "The clicked model's prompts, replies, and errors")
        layout.addWidget(self.detail_box)
        report_row = QHBoxLayout()
        self.open_reports_button = QPushButton(
            "Open reports folder", self)
        self.open_reports_button.setToolTip(
            "Show the saved smoke test reports")
        self.open_reports_button.clicked.connect(
            self._open_reports_folder)
        report_row.addWidget(self.open_reports_button)
        self.report_label = QLabel(self)
        self.report_label.setWordWrap(True)
        report_row.addWidget(self.report_label, 1)
        layout.addLayout(report_row)
        self.tabs.addTab(smoke_tab, "Smoke test")

    # ---------------------------------------------------------------- state

    def _log_action(self, text):
        if self._log is not None:
            self._log.action(text)

    def _smoke_running(self):
        return self._smoke_worker is not None

    def _loading(self):
        return self._load_worker is not None

    def _update_buttons(self):
        selected = self._selected_path is not None
        loaded = self.controller.is_loaded
        busy = self._smoke_running() or self._loading()
        self.load_button.setEnabled(
            selected and not loaded and not busy)
        self.unload_button.setEnabled(loaded and not busy)
        self.chat_input.setEnabled(loaded and not busy)
        self.send_button.setEnabled(
            loaded and not busy and not self._chat_streaming)
        self.stop_button.setEnabled(self._chat_streaming)
        ticked = len(self._ticked_paths) > 0
        self.run_button.setEnabled(
            ticked and not loaded and not busy
            and not self._chat_streaming)
        self.cancel_button.setEnabled(self._smoke_running())
        if loaded:
            self.smoke_hint_label.setText(
                "Unload the chat model to run a smoke test.")
        elif not ticked:
            self.smoke_hint_label.setText(
                "Tick one or more models in the list first.")
        else:
            self.smoke_hint_label.setText("")

    def _update_ram_label(self):
        _total, free = read_ram_bytes()
        if free is None:
            self.ram_label.setText("Free RAM: unknown")
        else:
            self.ram_label.setText("Free RAM: %s"
                                   % format_size(free))

    # ---------------------------------------------------------------- scan

    def _start_scan(self):
        if self._scan_worker is not None:
            return
        self.statusBar().showMessage("Reading the models...")
        self.model_list.clear()
        loading = QListWidgetItem("Loading models...")
        loading.setFlags(Qt.NoItemFlags)
        self.model_list.addItem(loading)
        worker = ScanWorker(self.models_folder,
                            include_ollama=self.include_ollama)
        self._scan_worker = worker
        worker.finished.connect(self._on_scan_finished)
        worker.start()

    def _on_scan_finished(self, models, skipped, warnings, free):
        self._scan_worker = None
        self._models = models
        self._free_bytes = free
        self.model_list.clear()
        # Ticks and the chat selection survive a refresh: they
        # are keyed by path, and re-applied to the new rows.
        for model in models:
            item = QListWidgetItem(
                format_model_line(model, free))
            item.setData(Qt.UserRole, model)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            path = str(model["path"])
            item.setCheckState(
                Qt.Checked if path in self._ticked_paths
                else Qt.Unchecked)
            self.model_list.addItem(item)
            if path == self._selected_path:
                item.setSelected(True)
        problems = ([("%s (%s)" % item) for item in skipped]
                    + [("%s (%s)" % item) for item in warnings])
        if problems:
            self.skipped_label.setText(
                "Could not read: %s" % "; ".join(problems))
            self.skipped_label.setToolTip(
                "\n".join(problems))
            self.skipped_label.setVisible(True)
        else:
            self.skipped_label.setVisible(False)
        self.statusBar().showMessage(
            "%d model%s found." % (len(models),
                                  "" if len(models) == 1 else "s"))
        self._update_buttons()

    def _on_selection_changed(self):
        selected = self.model_list.selectedItems()
        if not selected:
            self._selected_path = None
        else:
            model = selected[0].data(Qt.UserRole)
            self._selected_path = str(model["path"]) if model else None
        self._update_buttons()

    def _on_item_changed(self, item):
        model = item.data(Qt.UserRole)
        if not model:
            return
        path = str(model["path"])
        if item.checkState() == Qt.Checked:
            self._ticked_paths.add(path)
        else:
            self._ticked_paths.discard(path)
        self._update_buttons()

    def _set_all_ticks(self, ticked):
        self.model_list.blockSignals(True)
        for row in range(self.model_list.count()):
            item = self.model_list.item(row)
            model = item.data(Qt.UserRole)
            if not model:
                continue
            item.setCheckState(Qt.Checked if ticked
                               else Qt.Unchecked)
        self.model_list.blockSignals(False)
        if ticked:
            self._ticked_paths = {
                str(m["path"]) for m in self._models}
        else:
            self._ticked_paths = set()
        self._update_buttons()

    # ---------------------------------------------------------------- chat

    def _selected_model(self):
        for model in self._models:
            if str(model["path"]) == self._selected_path:
                return model
        return None

    def _on_load_clicked(self):
        model = self._selected_model()
        if model is None or self._loading() or self._smoke_running():
            return
        name = model["name"]
        if is_embedding_model(model):
            # An embedding model only turns text into numbers
            # for search: it cannot write replies, so loading
            # it for a chat would only fail at the first reply.
            # Refuse it here, in plain words, instead.
            self._log_action("load refused %s: embedding only"
                             % name)
            QMessageBox.warning(
                self, "Can't chat with %s" % name,
                "%s only turns text into numbers for search. "
                "It can't write replies, so it can't chat. "
                "Pick a chat model instead." % name)
            self.statusBar().showMessage(
                "%s can't chat: it is an embedding model." % name)
            return
        self._log_action("load %s" % name)
        self.statusBar().showMessage("Loading %s..." % name)
        self.load_progress.setVisible(True)
        self.load_progress.setRange(0, 0)  # indeterminate
        worker = LoadWorker(self.controller, model["path"],
                            display_name=name)
        self._load_worker = worker
        worker.progress.connect(self._on_load_progress)
        worker.finished.connect(self._on_load_finished)
        worker.failed.connect(self._on_load_failed)
        worker.start()
        self._update_buttons()

    def _on_load_progress(self, pct, message):
        if pct < 0:
            self.load_progress.setRange(0, 0)
        else:
            self.load_progress.setRange(0, 100)
            self.load_progress.setValue(pct)
        self.statusBar().showMessage(message)

    def _on_load_finished(self, result):
        self._load_worker = None
        self.load_progress.setVisible(False)
        self._history = []
        self.chat_view.clear()
        name = self.controller.current_name or ""
        took = result.get("load_time_s")
        if took is not None:
            self.loaded_label.setText(
                "Loaded: %s (took %.1f s)" % (name, took))
        else:
            self.loaded_label.setText("Loaded: %s" % name)
        warning = result.get("warning") or ""
        vision_warning = result.get("vision_warning") or ""
        if warning:
            self.stats_label.setText(warning)
        elif vision_warning:
            self.stats_label.setText(vision_warning)
        else:
            self.stats_label.setText("")
        self.statusBar().showMessage("%s loaded." % name)
        self._log_action("loaded %s" % name)
        self._update_buttons()

    def _on_load_failed(self, message):
        self._load_worker = None
        self.load_progress.setVisible(False)
        model = self._selected_model()
        name = model["name"] if model else "That model"
        full = explain_load_error(message)
        self._log_action("load failed %s: %s" % (name, message))
        QMessageBox.warning(
            self, "Could not load %s" % name,
            "%s could not be loaded.\n\n%s" % (name, full))
        self.statusBar().showMessage(
            "%s could not be loaded." % name)
        self._update_buttons()

    def _on_unload_clicked(self):
        if not self.controller.is_loaded:
            return
        name = self.controller.current_name or ""
        self.controller.unload()
        self._history = []
        self._append_chat("\n--- %s unloaded ---\n" % name)
        self.loaded_label.setText("Nothing loaded.")
        self.stats_label.setText("")
        self.statusBar().showMessage("%s unloaded." % name)
        self._log_action("unloaded %s" % name)
        self._update_buttons()

    def _append_chat(self, text):
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.ensureCursorVisible()

    def _on_send_clicked(self):
        if not self.controller.is_loaded or self._chat_streaming:
            return
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        self.chat_input.clear()
        self._append_chat("You: %s\n\n" % text)
        self._append_chat("Model: ")
        self._chat_streaming = True
        worker = ChatWorker(self.controller.runner, text,
                            history=self._history)
        self._chat_worker = worker
        worker.token.connect(self._on_chat_token)
        worker.finished.connect(
            lambda stats: self._on_chat_finished(stats, text))
        worker.failed.connect(self._on_chat_failed)
        worker.start()
        self.statusBar().showMessage("Replying...")
        self._update_buttons()

    def _on_chat_token(self, text):
        self._append_chat(text)

    def _on_chat_finished(self, stats, prompt):
        self._chat_worker = None
        self._chat_streaming = False
        reply = self._last_reply_text()
        if not stats.get("stopped"):
            self._history.append({"role": "user", "content": prompt})
            self._history.append({"role": "assistant",
                                  "content": reply})
            self._history = trim_history(self._history)
            _total, free = read_ram_bytes()
            ram = ("Free RAM: %s" % format_size(free)
                   if free is not None else "Free RAM: unknown")
            self.stats_label.setText(
                "%d tokens in %.1f s, %.1f tokens per second. %s"
                % (stats.get("tokens", 0),
                   stats.get("elapsed_s", 0.0),
                   stats.get("tokens_per_sec", 0.0), ram))
            self.statusBar().showMessage("Reply done.")
        else:
            self._append_chat("\n[Reply stopped.]\n")
            self.statusBar().showMessage("Reply stopped.")
        self._append_chat("\n\n")
        self._update_buttons()

    def _last_reply_text(self):
        """The text of the last reply in the chat view: everything
        after the last "Model: " marker."""
        text = self.chat_view.toPlainText()
        marker = "Model: "
        index = text.rfind(marker)
        if index < 0:
            return ""
        return text[index + len(marker):].strip()

    def _on_chat_failed(self, message):
        self._chat_worker = None
        self._chat_streaming = False
        message = explain_reply_error(message)
        self._append_chat("\n[The reply stopped: %s]\n\n" % message)
        self.statusBar().showMessage("The reply stopped.")
        self._update_buttons()

    def _on_stop_clicked(self):
        if self._chat_worker is not None:
            self._chat_worker.cancel()

    # ---------------------------------------------------------------- smoke

    def _on_run_clicked(self):
        if self._smoke_running() or self.controller.is_loaded:
            return
        chosen = [m for m in self._models
                  if str(m["path"]) in self._ticked_paths]
        if not chosen:
            return
        self._log_action("smoke test started: %d models"
                         % len(chosen))
        self._smoke_models = chosen
        self._smoke_results = [None] * len(chosen)
        self.results_table.setRowCount(0)
        self.detail_box.clear()
        self.report_label.setText("")
        self.smoke_progress.setVisible(True)
        self.smoke_progress.setRange(0, len(chosen))
        self.smoke_progress.setValue(0)
        self.smoke_status_label.setText(
            "Starting the smoke test...")
        worker = SmokeWorker(chosen, self.controller)
        self._smoke_worker = worker
        worker.progress.connect(self._on_smoke_progress)
        worker.model_done.connect(self._on_smoke_model_done)
        worker.finished.connect(self._on_smoke_finished)
        worker.start()
        self._update_buttons()

    def _on_smoke_progress(self, kind, index, name, detail):
        if kind == "model_started":
            self.smoke_status_label.setText(
                "Model %d of %d: %s, loading..."
                % (index + 1, len(self._smoke_models), name))
        elif kind == "prompt_started":
            self.smoke_status_label.setText(
                "Model %d of %d: %s, %s"
                % (index + 1, len(self._smoke_models), name,
                   detail))
        elif kind == "model_done":
            self.smoke_progress.setValue(index + 1)

    def _on_smoke_model_done(self, index, result):
        if 0 <= index < len(self._smoke_results):
            self._smoke_results[index] = result
        self._fill_result_row(index, result)

    def _fill_result_row(self, index, result):
        table = self.results_table
        if table.rowCount() <= index:
            table.setRowCount(index + 1)
        prompts = result.get("prompts") or []
        first = prompts[0] if len(prompts) > 0 else None
        second = prompts[1] if len(prompts) > 1 else None
        if result.get("load_ok"):
            load = "ok"
            if result.get("load_time_s"):
                load = "ok (%.1f s)" % result["load_time_s"]
        elif result.get("load_error"):
            load = "failed"
        else:
            # No load was attempted: the run stopped before
            # this model, or it is an embedding model the test
            # skips. Not a failure.
            load = "-"
        cells = [
            result["name"],
            load,
            self._prompt_cell(first),
            self._prompt_cell(second),
            self._fmt_seconds(first.get("ttft_s")
                              if first else None),
            self._fmt_speed(first.get("tokens_per_sec")
                            if first else None),
            result["verdict"].upper(),
        ]
        badges = verdict_badges(DARK)
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if column == 6:
                # The verdict cell carries the colour: green for
                # PASS, red for FAIL, grey for NOT TESTED.
                _label, color = badges.get(
                    result["verdict"], ("", DARK["untested"]))
                item.setBackground(QBrush(QColor(color)))
                item.setForeground(QBrush(QColor(DARK["badge_text"])))
            table.setItem(index, column, item)
        table.resizeColumnsToContents()

    @staticmethod
    def _prompt_cell(entry):
        if entry is None:
            return "-"
        if entry.get("error"):
            return "failed"
        if entry.get("replied"):
            return "replied"
        return "no reply"

    @staticmethod
    def _fmt_seconds(value):
        if value is None:
            return "-"
        if value < 1:
            return "%d ms" % round(value * 1000)
        return "%.1f s" % value

    @staticmethod
    def _fmt_speed(value):
        if value is None:
            return "-"
        return "%.1f tok/s" % value

    def _on_result_selected(self):
        selected = self.results_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        if row >= len(self._smoke_results):
            return
        result = self._smoke_results[row]
        if result is None:
            return
        lines = ["%s (%s)" % (result["name"], result["verdict"]),
                 ""]
        if result.get("load_error"):
            lines.append("Load failed: %s"
                         % result["load_error"])
            lines.append("")
        if not result.get("prompts") and result.get("error") \
                and not result.get("load_error"):
            # Nothing ran: say why, when the run recorded a
            # reason (an embedding model the test skips, or a
            # run that stopped before this model).
            lines.append(result["error"])
            lines.append("")
        for entry in result.get("prompts") or []:
            lines.append("Prompt: %s" % entry["prompt"])
            if entry.get("error"):
                lines.append("Error: %s" % entry["error"])
            elif entry.get("excerpt"):
                lines.append("Reply: %s" % entry["excerpt"])
            else:
                lines.append("No reply.")
            lines.append("")
        self.detail_box.setPlainText("\n".join(lines))

    def _on_cancel_clicked(self):
        if self._smoke_worker is not None:
            self._smoke_worker.cancel()
            self.smoke_status_label.setText(
                "Stopping after the current model...")
            self.cancel_button.setEnabled(False)

    def _on_smoke_finished(self, results):
        self._smoke_worker = None
        self.smoke_progress.setVisible(False)
        passed, failed, untested = summarize(results)
        run_dir = new_run_dir(self._home)
        meta = {"when": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"),
                "fyrn_dir": str(self.fyrn_install.folder),
                "fyrn_version": self.fyrn_install.version}
        try:
            md_path, _json_path = write_report(results, run_dir,
                                               meta)
            self._last_report_dir = run_dir
            self.report_label.setText(
                "Report saved: %s" % md_path)
        except OSError as exc:
            self.report_label.setText(
                "The report could not be saved (%s)." % exc)
        self.smoke_status_label.setText(
            "Done: %d passed, %d failed, %d not tested."
            % (passed, failed, untested))
        self.statusBar().showMessage(
            "Smoke test done: %d passed, %d failed, %d not "
            "tested." % (passed, failed, untested))
        self._log_action("smoke test finished: %d passed, %d "
                         "failed, %d not tested"
                         % (passed, failed, untested))
        self._update_buttons()

    # ---------------------------------------------------------------- updates

    def _on_check_updates_activated(self):
        """Help menu, Check for updates: the same check as on
        launch, except the user asked, so an answer is owed
        even when nothing is out."""
        if self._update_check_worker is not None \
                or self._update_download_worker is not None:
            return
        self._update_manual = True
        self._start_update_check()

    def _start_update_check(self):
        if self._update_check_worker is not None \
                or self._update_download_worker is not None:
            return
        worker = UpdateCheckWorker(__version__,
                                   urlopen=self._update_urlopen)
        self._update_check_worker = worker
        worker.finished.connect(self._on_update_check_finished)
        worker.start()

    def _on_update_check_finished(self, result):
        self._update_check_worker = None
        manual = self._update_manual
        self._update_manual = False
        state = result.get("state")
        if state == "newer":
            # Ask first, never a silent download.
            self._update_release = result
            latest = result["latest"]
            self._log_action("update available: %s" % latest)
            answer = QMessageBox.question(
                self, "Update available",
                "Fyrn Chat Test %s is out (you have %s).\n\n"
                "Download it now? The download is checked "
                "against its checksum before anything is "
                "installed." % (latest, __version__),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes)
            if answer == QMessageBox.Yes:
                self._start_update_download()
            else:
                self.statusBar().showMessage(
                    "Update skipped. It will be offered again "
                    "next time.")
            return
        if not manual:
            # Up to date, or the check could not run: the
            # launch check stays silent either way.
            return
        if state == "current":
            QMessageBox.information(
                self, "Up to date",
                "Fyrn Chat Test %s is the newest version."
                % __version__)
        else:
            QMessageBox.warning(
                self, "Couldn't check for updates",
                "Fyrn Chat Test could not check for updates.\n\n"
                "%s" % result.get("reason", ""))

    def _start_update_download(self):
        release = self._update_release
        if release is None:
            return
        self.statusBar().showMessage(
            "Downloading update %s..." % release["latest"])
        worker = UpdateDownloadWorker(
            release, updates.staged_dir(self._home),
            urlopen=self._update_urlopen)
        self._update_download_worker = worker
        worker.progress.connect(self._on_update_download_progress)
        worker.finished.connect(self._on_update_download_finished)
        worker.cancelled.connect(self._on_update_download_cancelled)
        worker.failed.connect(self._on_update_download_failed)
        worker.start()

    def _on_update_download_progress(self, done, total):
        release = self._update_release or {}
        version = release.get("latest", "")
        if total:
            percent = round(done * 100 / total)
            self.statusBar().showMessage(
                "Downloading update %s... %d%%"
                % (version, percent))
        else:
            self.statusBar().showMessage(
                "Downloading update %s... %s"
                % (version, format_size(done)))

    def _on_update_download_finished(self, zip_path):
        self._update_download_worker = None
        release = self._update_release or {}
        version = release.get("latest", "")
        self._log_action("update %s downloaded and verified"
                         % version)
        answer = QMessageBox.question(
            self, "Update downloaded",
            "Fyrn Chat Test %s is downloaded and checked.\n\n"
            "Restart now to complete the update?"
            % version,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            # The verified zip stays staged: the next launch
            # installs it before the window shows.
            self.statusBar().showMessage(
                "Update %s will finish the next time Fyrn Chat "
                "Test starts." % version)
            return
        try:
            updates.install_update(zip_path)
        except updates.UpdateError as exc:
            # The staged zip stays put: the next launch tries
            # the install again.
            reason = str(exc)
            QMessageBox.warning(
                self, "Update failed",
                "%s\n\nIt will be tried again the next time "
                "Fyrn Chat Test starts." % reason)
            self.statusBar().showMessage(
                "The update could not be installed.")
            return
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            # install_update speaks UpdateError; anything else
            # is unexpected, and still must not crash the window.
            QMessageBox.warning(
                self, "Update failed",
                "The update could not be installed (%s).\n\n"
                "It will be tried again the next time Fyrn Chat "
                "Test starts." % exc)
            self.statusBar().showMessage(
                "The update could not be installed.")
            return
        updates.clear_staged(self._home)
        self._log_action("update %s installed, restarting"
                         % version)
        updates.schedule_restart()
        self.close()

    def _on_update_download_cancelled(self):
        self._update_download_worker = None
        self.statusBar().showMessage("Update download stopped.")

    def _on_update_download_failed(self, message):
        self._update_download_worker = None
        self._log_action("update download failed: %s" % message)
        QMessageBox.warning(
            self, "Update failed",
            "The update could not be downloaded.\n\n%s"
            % message)
        self.statusBar().showMessage(
            "The update could not be downloaded.")

    # ---------------------------------------------------------------- menus

    def _show_how_to_use(self):
        dialog = HowToDialog(self)
        dialog.exec()

    def _show_about(self):
        QMessageBox.about(
            self, "About Fyrn Chat Test",
            ABOUT_TEXT % (__version__,
                          self.fyrn_install.version,
                          self.fyrn_install.folder))

    def _open_reports_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder = reports_dir(self._home)
        Path(folder).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(folder))

    def _write_debug_report(self):
        if self._log is None:
            self._init_logger()
        if self._log is None:
            QMessageBox.warning(
                self, "Debug report",
                "The debug log is not available.")
            return
        try:
            path = self._log.generate_report()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            QMessageBox.warning(
                self, "Debug report",
                "The report could not be written (%s)." % exc)
            return
        QMessageBox.information(
            self, "Debug report",
            "Report written to:\n%s" % path)

    def _init_logger(self):
        """Create the debug logger once the window is visible
        (its import costs cold-start time, so it is not paid
        during construction)."""
        if self._log is not None:
            return
        try:
            from fyrn.debug_log import DebugLogger
            self._log = DebugLogger(
                "fyrn-chat-test", __version__,
                log_dir=str(self._home / "logs"))
            self._log.set_setting(
                "fyrn_dir", str(self.fyrn_install.folder))
            self._log.set_setting(
                "fyrn_version", self.fyrn_install.version)
            self._log.set_setting(
                "models_folder", str(self.models_folder))
            self._log.install_crash_handler()
        except Exception:  # noqa: BLE001 - the log is optional
            self._log = None

    # ---------------------------------------------------------------- window

    def bring_to_front(self):
        """Raise the window (a second launch asked for it)."""
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        # A worker that is still running when the window is
        # destroyed aborts the process ("QThread: Destroyed
        # while thread is still running"), so every worker is
        # cancelled and waited for before the window closes;
        # one that outlives its wait is parked, never dropped
        # (see _wait_for_worker).
        if self._update_download_worker is not None:
            self._update_download_worker.cancel()
        _wait_for_worker(self._update_download_worker, 15000)
        # The check is one short request with an 8 second
        # timeout: the wait covers it.
        _wait_for_worker(self._update_check_worker, 10000)
        if self._chat_worker is not None:
            self._chat_worker.cancel()
        _wait_for_worker(self._chat_worker, 5000)
        if self._smoke_worker is not None:
            self._smoke_worker.cancel()
        _wait_for_worker(self._smoke_worker, 15000)
        if self._load_worker is not None:
            self._load_worker.cancel()
        _wait_for_worker(self._load_worker, 5000)
        _wait_for_worker(self._scan_worker, 5000)
        # A load in progress cannot be stopped (the backend
        # loads in one opaque step): the wait above gives it a
        # chance, and unloading here frees what it loaded.
        try:
            self.controller.unload()
        except Exception:  # noqa: BLE001 - closing anyway
            pass
        save_window_geometry(self._settings, "main", self)
        super().closeEvent(event)


# ---------------------------------------------------------------- entry


def main(install, argv=None):
    """Open the Fyrn Chat Test window for one Fyrn install."""
    app = QApplication(list(argv) if argv is not None else [])
    # Lowercase, no spaces: this is the X11 WM_CLASS, and it
    # must match StartupWMClass in Fyrn-Chat-Test.desktop
    # exactly. The window title stays "Fyrn Chat Test".
    app.setApplicationName("fyrn-chat-test")
    app.setOrganizationName("BrokeNBored")
    app.setApplicationVersion(__version__)
    app.setStyleSheet(STYLESHEET)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    home = ensure_home()
    window = MainWindow(install)
    # The pre-Qt fast path's listener: a second launch sends
    # b"raise" to this socket without importing Qt. Started
    # after the window exists, so a raise always has a window
    # to raise.
    listener = RaiseListener(home / "raise.sock")
    listener.raise_requested.connect(window.bring_to_front)
    listener.start()
    window.show()
    # Warm the debug logger now that the window is visible: its
    # import costs cold-start time, which is why it is not paid
    # during construction. It is ready before the user can do
    # anything.
    QTimer.singleShot(0, window._init_logger)
    try:
        return app.exec()
    finally:
        listener.requestInterruption()
        listener.wait(2000)
