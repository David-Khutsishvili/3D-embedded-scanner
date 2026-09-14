"""Main window: the Scan and Viewer tabs, a status bar and the orderly close sequence."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from scanner_core.scan_files import ScanMeta
from scanner_ui.scan_tab import ScanTab
from scanner_ui.viewer_tab import ViewerTab

WINDOW_TITLE = "3D Embedded Scanner"
DEFAULT_SIZE = (1280, 800)
STATUS_TIMEOUT_MS = 10_000


class MainWindow(QMainWindow):
    """Top-level window with the "Scan" and "Viewer" tabs."""

    def __init__(self, scans_dir: Path) -> None:
        super().__init__()
        self._closing = False
        self._scan_tab = ScanTab(scans_dir)
        self._viewer_tab = ViewerTab(scans_dir)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._scan_tab, "Scan")
        self._tabs.addTab(self._viewer_tab, "Viewer")
        self.setCentralWidget(self._tabs)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.statusBar().showMessage(f"Scans folder: {scans_dir}", STATUS_TIMEOUT_MS)

        self._scan_tab.scan_saved.connect(self._on_scan_saved)
        self._scan_tab.status_message.connect(self._show_status)
        self._viewer_tab.status_message.connect(self._show_status)
        self._viewer_tab.scans_dir_changed.connect(self._scan_tab.set_scans_dir)

    @property
    def tabs(self) -> QTabWidget:
        """The tab widget."""
        return self._tabs

    @property
    def scan_tab(self) -> ScanTab:
        """The Scan tab."""
        return self._scan_tab

    @property
    def viewer_tab(self) -> ViewerTab:
        """The Viewer tab."""
        return self._viewer_tab

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Confirm closing during a scan, then stop and save it and release threads and plotters."""
        if self._closing:  # a second close request while the scan is being stopped
            event.ignore()
            return
        if self._scan_tab.is_capturing and not self._confirm_close():
            event.ignore()
            return
        self._closing = True
        self._tabs.setEnabled(False)
        self.statusBar().showMessage("Closing…")
        self._scan_tab.shutdown()
        self._viewer_tab.shutdown()
        event.accept()

    def _confirm_close(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Scan in progress",
            "A scan is running. Stop it, save the data captured so far and quit?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    @Slot(object)
    def _on_scan_saved(self, meta: ScanMeta) -> None:
        if not self._closing:
            self._viewer_tab.refresh_scans(select_name=meta.name)

    @Slot(str)
    def _show_status(self, text: str) -> None:
        self.statusBar().showMessage(text, STATUS_TIMEOUT_MS)
