"""Scan tab: connect to the scanner or simulator, capture scans, show progress and live preview."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from scanner_core.capture_session import CaptureSession
from scanner_core.links import ScannerLink, SerialLink, list_serial_ports
from scanner_core.protocol import CMD_PING, CMD_START, CMD_STOP, Event, EventKind, parse_line
from scanner_core.reconstruction import measurements_to_points
from scanner_core.scan_files import ScanMeta, is_valid_scan_name, list_scans
from scanner_core.simulator import SimulatedLink
from scanner_ui.link_worker import LinkConnection
from scanner_ui.scene import CameraUpdate, SceneView

HANDSHAKE_PING_MS = 1000
NOT_READY_PING_MS = 3000
READY_TIMEOUT_MS = 30_000
START_CHECK_TIMEOUT_MS = 3000
STOP_WAIT_MS = 5000
TICK_MS = 1000
LOG_MAX_LINES = 5000
PREVIEW_POINT_SIZE = 4
DEFAULT_REPLAY_SCAN = "pear_real"
FIRMWARE_ERROR_PREFIX = "Error:"
SIMULATOR_DEVICE = "<simulator>"
EMPTY_VALUE = "–"

NOT_READY_HINT = "Check power/firmware; see log"
BUSY_HINT = "Scanner busy: a scan is already running"
RESETTING_HINT = "Scan finished; waiting for the scanner to reset…"
NAME_RULE = "Letters, digits, '_' and '-' only; must start with a letter or digit (max 64)"
MUTED_STYLE = "color: #5f6368;"


class ScannerState(Enum):
    """Connection and scan state shown in the Scan tab."""

    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting…"
    READY = "Ready"
    SCANNING = "Scanning"
    STOPPING = "Stopping…"
    NOT_READY = "Not ready"
    ERROR = "Error"


STATE_COLORS: dict[ScannerState, str] = {
    ScannerState.DISCONNECTED: "#9aa0a6",
    ScannerState.CONNECTING: "#f2a900",
    ScannerState.READY: "#1e8e3e",
    ScannerState.SCANNING: "#1a73e8",
    ScannerState.STOPPING: "#e37400",
    ScannerState.NOT_READY: "#e8710a",
    ScannerState.ERROR: "#d93025",
}


def format_duration(seconds: float) -> str:
    """Format seconds as ``hh:mm:ss``."""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class ScanController(QObject):
    """Scanner state machine: readiness handshake, START/STOP and capturing scans.

    Lives in the GUI thread; the link runs in a ``LinkConnection`` worker thread.
    ``auto_scan_path`` names the file of a scan the scanner starts by itself (legacy firmware).
    """

    state_changed = Signal()
    log_message = Signal(str)
    scan_saved = Signal(object)  # ScanMeta
    session_ended = Signal()
    alert = Signal(str, str)  # title, text

    def __init__(self, auto_scan_path: Callable[[], Path], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._auto_scan_path = auto_scan_path
        self._connection: LinkConnection | None = None
        self._link_opened = False
        self._state = ScannerState.DISCONNECTED
        self._hint = ""
        self._session: CaptureSession | None = None
        self._scan_name = ""
        self._pending_scan_path: Path | None = None  # START follows once the fresh PING is answered
        self._awaiting_reset = False  # SCAN_END seen, READY not yet
        self._autosave_failed = False
        self._ping_timer = self._make_timer(self._on_ping_timer)
        self._ready_timeout = self._make_timer(self._on_ready_timeout, READY_TIMEOUT_MS)
        self._start_check_timeout = self._make_timer(
            self._on_start_check_timeout, START_CHECK_TIMEOUT_MS
        )
        self._event_handlers: dict[EventKind, Callable[[Event], None]] = {
            EventKind.READY: self._on_ready,
            EventKind.PONG: self._on_pong,
            EventKind.BUSY: self._on_busy,
            EventKind.SCAN_START: self._on_scan_start,
            EventKind.SCAN_END: self._on_scan_end,
            EventKind.LOG: self._on_log,
        }

    @property
    def state(self) -> ScannerState:
        """Current state."""
        return self._state

    @property
    def hint(self) -> str:
        """Explanation shown next to the state (may be empty)."""
        return self._hint

    @property
    def session(self) -> CaptureSession | None:
        """The capture in progress, if any."""
        return self._session

    @property
    def scan_name(self) -> str:
        """Name of the current (or last) capture."""
        return self._scan_name

    @property
    def is_connected(self) -> bool:
        """Whether a link is open or being opened."""
        return self._connection is not None

    @property
    def can_start(self) -> bool:
        """Whether a scan may be started now."""
        return self._state is ScannerState.READY and self._pending_scan_path is None

    @property
    def can_stop(self) -> bool:
        """Whether a running scan may be stopped now."""
        return self._state is ScannerState.SCANNING and not self._awaiting_reset

    def connect_link(self, link: ScannerLink) -> None:
        """Open ``link`` in a worker thread and start the readiness handshake."""
        if self._connection is not None:
            return
        connection = LinkConnection(link)
        connection.opened.connect(self._on_link_opened)
        connection.line_received.connect(self._on_line)
        connection.error.connect(self._on_link_error)
        connection.closed.connect(self._on_link_closed)
        self._connection = connection
        self._link_opened = False
        self.log_message.emit(f"Connecting to {link.description}…")
        self._set_state(ScannerState.CONNECTING)
        connection.start()

    def disconnect_link(self) -> None:
        """Close the link; an unfinished capture is saved as interrupted."""
        connection, self._connection = self._connection, None
        if connection is None:
            return
        self._stop_timers()
        self._pending_scan_path = None
        self._awaiting_reset = False
        self._finish_session()
        connection.close()
        self.log_message.emit(f"Disconnected from {connection.description}")
        self._set_state(ScannerState.DISCONNECTED)

    def start_scan(self, scan_path: Path) -> None:
        """Check readiness with a fresh PING; START is sent only if PONG arrives within 3 s."""
        if not self.can_start:
            return
        self._pending_scan_path = scan_path
        self._send(CMD_PING)
        self._start_check_timeout.start()
        self._set_state(ScannerState.READY, "Checking that the scanner is ready…")

    def stop_scan(self) -> None:
        """Ask the scanner to stop; the capture is saved when SCAN_END arrives."""
        if not self.can_stop:
            return
        self._send(CMD_STOP)
        self._set_state(ScannerState.STOPPING, "Waiting for the scanner to stop…")

    def stop_and_disconnect(self) -> None:
        """Stop a running capture (waiting up to 5 s for SCAN_END), save it and disconnect."""
        if self._session is not None and self._connection is not None:
            if self._state is not ScannerState.STOPPING:
                self._send(CMD_STOP)
                self._set_state(ScannerState.STOPPING, "Waiting for the scanner to stop…")
            self._wait_for_session_end(STOP_WAIT_MS)
        self.disconnect_link()

    def _make_timer(self, slot: Callable[[], None], single_shot_ms: int | None = None) -> QTimer:
        """Create a timer; with ``single_shot_ms`` it fires once after that delay."""
        timer = QTimer(self)
        if single_shot_ms is not None:
            timer.setSingleShot(True)
            timer.setInterval(single_shot_ms)
        timer.timeout.connect(slot)
        return timer

    def _set_state(self, state: ScannerState, hint: str = "") -> None:
        self._state, self._hint = state, hint
        self.state_changed.emit()

    def _send(self, command: str, log: bool = True) -> None:
        if self._connection is None:
            return
        self._connection.send(command)
        if log:
            self.log_message.emit(f"> {command}")

    def _stop_handshake(self) -> None:
        self._ping_timer.stop()
        self._ready_timeout.stop()

    def _stop_timers(self) -> None:
        self._stop_handshake()
        self._start_check_timeout.stop()

    def _wait_for_session_end(self, timeout_ms: int) -> None:
        """Run a local event loop (the UI stays responsive) until the capture ends or times out."""
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        self.session_ended.connect(loop.quit)
        timer.start(timeout_ms)
        loop.exec()
        timer.stop()
        self.session_ended.disconnect(loop.quit)

    # ---- Link signals ----

    @Slot()
    def _on_link_opened(self) -> None:
        self._link_opened = True
        self.log_message.emit("Link open; waiting for the scanner (PING every 1 s)")
        self._send(CMD_PING, log=False)
        self._ping_timer.start(HANDSHAKE_PING_MS)
        self._ready_timeout.start()

    @Slot(str)
    def _on_link_error(self, message: str) -> None:
        self.log_message.emit(f"Connection error: {message}")
        interrupted = self._session is not None
        self._stop_timers()
        self._pending_scan_path = None
        self._finish_session()
        self._set_state(ScannerState.ERROR, message)
        if not self._link_opened:
            self.alert.emit("Cannot connect", message)
        elif interrupted:
            self.alert.emit(
                "Connection lost",
                f"{message}\n\nThe data captured so far was saved as interrupted.",
            )

    @Slot()
    def _on_link_closed(self) -> None:
        """The worker ended by itself (the link failed): release it."""
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()
        if self._state is ScannerState.ERROR:
            self.state_changed.emit()
        else:
            self._set_state(ScannerState.DISCONNECTED)

    @Slot(str)
    def _on_line(self, line: str) -> None:
        event = parse_line(line)
        if event is None:
            return
        if event.kind is not EventKind.MEASUREMENT:
            self.log_message.emit(f"< {event.raw}")
        if event.kind is EventKind.SCAN_START and self._session is None:
            self._begin_unrequested_session()
        if self._session is not None:
            self._feed_session(self._session, event)
        handler = self._event_handlers.get(event.kind)
        if handler is not None:
            handler(event)

    # ---- Timers ----

    @Slot()
    def _on_ping_timer(self) -> None:
        self._send(CMD_PING, log=False)

    @Slot()
    def _on_ready_timeout(self) -> None:
        if self._state is ScannerState.CONNECTING:
            self.log_message.emit("No answer within 30 s; still pinging every 3 s")
            self._ping_timer.start(NOT_READY_PING_MS)
            self._set_state(ScannerState.NOT_READY, NOT_READY_HINT)

    @Slot()
    def _on_start_check_timeout(self) -> None:
        self._pending_scan_path = None
        self.log_message.emit("No PONG within 3 s; the scan was not started")
        self._ping_timer.start(NOT_READY_PING_MS)
        self._set_state(ScannerState.NOT_READY, NOT_READY_HINT)
        self.alert.emit(
            "Scanner not ready",
            "The scanner did not answer within 3 s, so the scan was not started.\n"
            f"{NOT_READY_HINT}.",
        )

    # ---- Protocol events ----

    def _on_ready(self, event: Event) -> None:
        if self._session is not None:
            self.log_message.emit("READY without SCAN_END: the scanner restarted during the scan")
            self._finish_session()
        self._stop_handshake()
        self._awaiting_reset = False
        self._set_state(ScannerState.READY)

    def _on_pong(self, event: Event) -> None:
        if self._pending_scan_path is not None:
            self._send_start(self._pending_scan_path)
        elif self._state in (ScannerState.CONNECTING, ScannerState.NOT_READY):
            self._stop_handshake()
            self._set_state(ScannerState.READY)

    def _on_busy(self, event: Event) -> None:
        if self._pending_scan_path is not None:
            self._pending_scan_path = None
            self._start_check_timeout.stop()
            self.alert.emit("Scanner busy", f"{BUSY_HINT}. The scan was not started.")
        if self._session is None and not self._awaiting_reset:
            self._stop_handshake()
            self._set_state(ScannerState.SCANNING, BUSY_HINT)

    def _on_scan_start(self, event: Event) -> None:
        self._stop_handshake()
        self._awaiting_reset = False
        if self._state is not ScannerState.STOPPING:
            self._set_state(ScannerState.SCANNING, f"Capturing {self._scan_name}")

    def _on_scan_end(self, event: Event) -> None:
        self._finish_session()
        self._awaiting_reset = True
        stopping = self._state is ScannerState.STOPPING
        busy_state = ScannerState.STOPPING if stopping else ScannerState.SCANNING
        self._set_state(busy_state, RESETTING_HINT)

    def _on_log(self, event: Event) -> None:
        if event.raw.startswith(FIRMWARE_ERROR_PREFIX):
            self._stop_timers()
            self._pending_scan_path = None
            self._finish_session()
            self._set_state(ScannerState.ERROR, "The scanner halted with an error; see log")

    # ---- Capture session ----

    def _send_start(self, scan_path: Path) -> None:
        self._pending_scan_path = None
        self._start_check_timeout.stop()
        self._begin_session(scan_path)
        self._send(CMD_START)
        self._set_state(ScannerState.SCANNING, f"Starting {scan_path.stem}…")

    def _begin_unrequested_session(self) -> None:
        """Capture a scan the firmware started without START (legacy auto-start firmware)."""
        scan_path = self._auto_scan_path()
        self._begin_session(scan_path)
        self.log_message.emit(
            f"Scan started without START (legacy firmware); saving as {scan_path.stem}"
        )

    def _begin_session(self, scan_path: Path) -> None:
        source = self._connection.description if self._connection else ""
        self._session = CaptureSession(scan_path, source)
        self._scan_name = scan_path.stem
        self._autosave_failed = False

    def _feed_session(self, session: CaptureSession, event: Event) -> None:
        try:
            session.handle_event(event)
        except OSError as exc:
            if not self._autosave_failed:  # report once, not on every line
                self._autosave_failed = True
                self.log_message.emit(f"Autosave failed: {exc}")

    def _finish_session(self) -> None:
        """Finalize and save the current capture, if any, and announce it."""
        session, self._session = self._session, None
        if session is None:
            return
        try:
            if not session.started:
                self.log_message.emit(f"{self._scan_name} never started; nothing saved")
                return
            try:
                meta = session.finalize()
            except OSError as exc:
                self.log_message.emit(f"Saving {self._scan_name} failed: {exc}")
                self.alert.emit("Save failed", f"Could not save {self._scan_name}:\n{exc}")
                return
            self.log_message.emit(
                f"Saved {meta.name} ({meta.status.value}, {meta.measurement_count} measurements)"
            )
            self.scan_saved.emit(meta)
        finally:
            self.session_ended.emit()


class StatusIndicator(QWidget):
    """Coloured dot with the scanner state, and a hint below it."""

    DOT_SIZE = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dot = QLabel()
        self._dot.setFixedSize(self.DOT_SIZE, self.DOT_SIZE)
        self._label = QLabel()
        font = self._label.font()
        font.setBold(True)
        self._label.setFont(font)
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(MUTED_STYLE)

        top = QHBoxLayout()
        top.addWidget(self._dot)
        top.addWidget(self._label)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._hint)

    def set_state(self, state: ScannerState, hint: str) -> None:
        """Show ``state`` and ``hint``."""
        color = STATE_COLORS[state]
        radius = self.DOT_SIZE // 2
        self._dot.setStyleSheet(f"background-color: {color}; border-radius: {radius}px;")
        self._label.setText(state.value)
        self._label.setStyleSheet(f"color: {color};")
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))


class ProgressPanel(QWidget):
    """Height, rings, measurements and elapsed time of a capture."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._height = QLabel()
        self._rings = QLabel()
        self._measurements = QLabel()
        self._elapsed = QLabel()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        fields = (
            ("Height", self._height),
            ("Rings", self._rings),
            ("Measurements", self._measurements),
            ("Elapsed", self._elapsed),
        )
        for column, (title, value) in enumerate(fields):
            caption = QLabel(title)
            caption.setStyleSheet(MUTED_STYLE)
            font = value.font()
            font.setPointSizeF(font.pointSizeF() * 1.3)
            font.setBold(True)
            value.setFont(font)
            layout.addWidget(caption, 0, column)
            layout.addWidget(value, 1, column)
        self.clear()

    def clear(self) -> None:
        """Show empty values."""
        for value in (self._height, self._rings, self._measurements, self._elapsed):
            value.setText(EMPTY_VALUE)

    def show_session(self, session: CaptureSession) -> None:
        """Show the progress of ``session``."""
        height = session.last_h
        self._height.setText(EMPTY_VALUE if height is None else f"{height:.2f} mm")
        self._rings.setText(str(session.ring_count))
        self._measurements.setText(str(len(session.measurements)))
        self._elapsed.setText(format_duration(session.elapsed_s))


class LogView(QPlainTextEdit):
    """Read-only, timestamped log capped at ``LOG_MAX_LINES`` lines."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(LOG_MAX_LINES)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

    @Slot(str)
    def append_message(self, text: str) -> None:
        """Append ``text`` with the current time."""
        self.appendPlainText(f"{datetime.now():%H:%M:%S}  {text}")


class ScanTab(QWidget):
    """Scanner connection and scan controls, with progress, live 3D preview and log."""

    scan_saved = Signal(object)  # ScanMeta
    status_message = Signal(str)

    def __init__(self, scans_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scans_dir = scans_dir
        self._name_edited = False
        self._tracked_session: CaptureSession | None = None
        self._preview_count = -1  # measurements drawn in the preview; -1 forces a redraw
        self._controller = ScanController(self._auto_scan_path, self)

        self._create_widgets()
        self._build_layout()
        self._connect_signals()
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(TICK_MS)

        self.refresh_devices()
        self._reset_scan_name()
        self._on_state_changed()
        self._preview.show_message("The live preview appears here while scanning")

    @property
    def controller(self) -> ScanController:
        """The scanner state machine."""
        return self._controller

    @property
    def preview(self) -> SceneView:
        """The live 3D preview."""
        return self._preview

    @property
    def is_capturing(self) -> bool:
        """Whether a capture is in progress (closing would interrupt it)."""
        return self._controller.session is not None

    def set_scans_dir(self, scans_dir: Path) -> None:
        """Save new scans to ``scans_dir`` and replay scans from it."""
        self._scans_dir = scans_dir
        self._refresh_replay_scans()
        if not self._name_edited:
            self._reset_scan_name()

    def refresh_devices(self) -> None:
        """Re-list the serial ports (keeping the selection) and the simulator's replay scans."""
        current = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        for device, description in list_serial_ports():
            label = device if description in ("", device) else f"{device}: {description}"
            self._device_combo.addItem(label, device)
        self._device_combo.addItem("Simulator (demo)", SIMULATOR_DEVICE)
        index = self._device_combo.findData(current)
        self._device_combo.setCurrentIndex(max(index, 0))
        self._device_combo.blockSignals(False)
        self._refresh_replay_scans()
        self._on_device_changed()

    def shutdown(self) -> None:
        """Stop and save a running capture, disconnect and release the preview (on app close)."""
        self._tick_timer.stop()
        self._controller.stop_and_disconnect()
        self._preview.shutdown()

    # ---- Construction ----

    def _create_widgets(self) -> None:
        self._device_combo = QComboBox()
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setToolTip("Re-list serial ports and replay scans")
        self._connect_button = QPushButton("Connect")
        self._replay_label = QLabel("Replay scan")
        self._replay_combo = QComboBox()
        self._replay_combo.setToolTip("Scan file the simulator plays back")
        self._status = StatusIndicator()

        self._name_edit = QLineEdit()
        self._name_edit.setToolTip(NAME_RULE)
        self._name_hint = QLabel(NAME_RULE)
        self._name_hint.setWordWrap(True)
        self._name_hint.setStyleSheet("color: #d93025;")
        self._start_button = QPushButton("Start scan")
        self._stop_button = QPushButton("Stop")
        self._progress = ProgressPanel()

        self._preview_check = QCheckBox("Live 3D preview")
        self._preview_check.setChecked(True)
        self._preview_check.setToolTip("Redraw the captured points at most once per second")
        self._preview = SceneView()
        self._log = LogView()

    def _build_layout(self) -> None:
        scanner_group = QGroupBox("Scanner")
        scanner_grid = QGridLayout(scanner_group)
        scanner_grid.addWidget(QLabel("Device"), 0, 0)
        scanner_grid.addWidget(self._device_combo, 0, 1)
        scanner_grid.addWidget(self._refresh_button, 0, 2)
        scanner_grid.addWidget(self._connect_button, 0, 3)
        scanner_grid.addWidget(self._replay_label, 1, 0)
        scanner_grid.addWidget(self._replay_combo, 1, 1, 1, 3)
        scanner_grid.addWidget(self._status, 2, 0, 1, 4)
        scanner_grid.setColumnStretch(1, 1)

        scan_group = QGroupBox("Scan")
        scan_grid = QGridLayout(scan_group)
        scan_grid.addWidget(QLabel("Name"), 0, 0)
        scan_grid.addWidget(self._name_edit, 0, 1, 1, 2)
        scan_grid.addWidget(self._name_hint, 1, 1, 1, 2)
        scan_grid.addWidget(self._start_button, 2, 1)
        scan_grid.addWidget(self._stop_button, 2, 2)
        scan_grid.addWidget(self._progress, 3, 0, 1, 3)
        scan_grid.setColumnStretch(1, 1)
        scan_grid.setColumnStretch(2, 1)

        log_group = QGroupBox("Log")
        QVBoxLayout(log_group).addWidget(self._log)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(scanner_group)
        left_layout.addWidget(scan_group)
        left_layout.addWidget(log_group, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._preview_check)
        right_layout.addWidget(self._preview, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 820])
        QVBoxLayout(self).addWidget(splitter)

    def _connect_signals(self) -> None:
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._refresh_button.clicked.connect(self.refresh_devices)
        self._connect_button.clicked.connect(self._on_connect_clicked)
        self._name_edit.textEdited.connect(self._on_name_edited)
        self._name_edit.textChanged.connect(self._update_controls)
        self._start_button.clicked.connect(self._on_start_clicked)
        self._stop_button.clicked.connect(self._controller.stop_scan)
        self._preview_check.toggled.connect(self._on_preview_toggled)

        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.log_message.connect(self._log.append_message)
        self._controller.scan_saved.connect(self._on_scan_saved)
        self._controller.alert.connect(self._on_alert)

    # ---- User actions ----

    @Slot()
    def _on_device_changed(self) -> None:
        simulator = self._is_simulator_selected()
        self._replay_label.setVisible(simulator)
        self._replay_combo.setVisible(simulator)
        if not self._name_edited:
            self._reset_scan_name()

    @Slot()
    def _on_connect_clicked(self) -> None:
        if not self._controller.is_connected:
            link = self._create_link()
            if link is not None:
                self._controller.connect_link(link)
            return
        question = "A scan is running. Stop it, save the data captured so far and disconnect?"
        if self.is_capturing and not self._confirm("Disconnect", question):
            return
        self._controller.stop_and_disconnect()

    @Slot()
    def _on_start_clicked(self) -> None:
        name = self._name_edit.text()
        if not is_valid_scan_name(name):
            return
        scan_path = self._scan_path(name)
        if scan_path.exists() and not self._confirm(
            "Overwrite scan", f"A scan named {name} already exists. Overwrite it?"
        ):
            return
        self._controller.start_scan(scan_path)

    @Slot(str)
    def _on_name_edited(self, _: str) -> None:
        self._name_edited = True

    @Slot(bool)
    def _on_preview_toggled(self, enabled: bool) -> None:
        self._preview_count = -1
        if enabled:
            self._on_tick()
        else:
            self._preview.show_message("Live preview is off")

    # ---- Controller signals ----

    @Slot()
    def _on_state_changed(self) -> None:
        session = self._controller.session
        if session is not None and session is not self._tracked_session:
            self._tracked_session = session
            self._preview_count = -1
            self._progress.clear()
            if self._preview_check.isChecked():
                self._preview.show_message(f"Live: {self._controller.scan_name} - waiting for data")
        self._status.set_state(self._controller.state, self._controller.hint)
        self._update_controls()

    @Slot(object)
    def _on_scan_saved(self, meta: ScanMeta) -> None:
        self._on_tick()  # final progress and preview
        self._refresh_replay_scans()
        self._reset_scan_name()
        self.status_message.emit(
            f"Saved {meta.name} ({meta.status.value}, {meta.measurement_count} measurements)"
        )
        self.scan_saved.emit(meta)

    @Slot(str, str)
    def _on_alert(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    @Slot()
    def _on_tick(self) -> None:
        """Once per second: update the progress and, if enabled and visible, the live preview."""
        session = self._tracked_session
        if session is None:
            return
        self._progress.show_session(session)
        count = len(session.measurements)
        preview_due = count > 0 and count != self._preview_count
        if preview_due and self._preview_check.isChecked() and self._preview.isVisible():
            self._preview_count = count
            cloud = measurements_to_points(session.measurements)
            caption = f"Live: {self._controller.scan_name} - {count} measurements"
            self._preview.show_points(cloud, PREVIEW_POINT_SIZE, caption, CameraUpdate.FIT)

    # ---- Helpers ----

    def _update_controls(self) -> None:
        controller = self._controller
        connected = controller.is_connected
        name_valid = is_valid_scan_name(self._name_edit.text())
        for widget in (self._device_combo, self._refresh_button, self._replay_combo):
            widget.setEnabled(not connected)
        self._connect_button.setText("Disconnect" if connected else "Connect")
        self._name_edit.setEnabled(controller.session is None)
        self._name_hint.setVisible(not name_valid)
        self._start_button.setEnabled(controller.can_start and name_valid)
        self._stop_button.setEnabled(controller.can_stop)

    def _create_link(self) -> ScannerLink | None:
        if not self._is_simulator_selected():
            return SerialLink(self._device_combo.currentData())
        replay_path = self._replay_combo.currentData()
        if replay_path is None:
            QMessageBox.information(
                self, "Simulator", f"There are no scans to replay in {self._scans_dir}."
            )
            return None
        return SimulatedLink(replay_path)

    def _refresh_replay_scans(self) -> None:
        current = self._replay_combo.currentText() or DEFAULT_REPLAY_SCAN
        self._replay_combo.clear()
        for record in list_scans(self._scans_dir):
            self._replay_combo.addItem(record.name, record.path)
        index = self._replay_combo.findText(current)
        self._replay_combo.setCurrentIndex(max(index, 0))

    def _is_simulator_selected(self) -> bool:
        return self._device_combo.currentData() == SIMULATOR_DEVICE

    def _scan_path(self, name: str) -> Path:
        return self._scans_dir / f"{name}.json"

    def _default_scan_name(self) -> str:
        """``scan_YYYY-MM-DD_HH-MM`` (``demo_`` for the simulator), suffixed if already taken."""
        prefix = "demo" if self._is_simulator_selected() else "scan"
        base = f"{prefix}_{datetime.now():%Y-%m-%d_%H-%M}"
        name, suffix = base, 2
        while self._scan_path(name).exists():
            name, suffix = f"{base}_{suffix}", suffix + 1
        return name

    def _auto_scan_path(self) -> Path:
        return self._scan_path(self._default_scan_name())

    def _reset_scan_name(self) -> None:
        self._name_edit.setText(self._default_scan_name())
        self._name_edited = False

    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
