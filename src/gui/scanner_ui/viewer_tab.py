"""Viewer tab: browse saved scans and show them in 3D as a point cloud or a surface mesh."""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from scanner_core.protocol import Measurement
from scanner_core.reconstruction import (
    ScanStats,
    build_surface_mesh,
    measurements_to_points,
    scan_stats,
)
from scanner_core.scan_files import (
    ScanRecord,
    ScanStatus,
    default_scans_dir,
    list_scans,
    load_measurements,
)
from scanner_ui.scene import CameraUpdate, SceneView

FIGURES_DIR = default_scans_dir().parent / "figures"
DEFAULT_POINT_SIZE = 5
MAX_POINT_SIZE = 20
EMPTY_VALUE = "–"

STATUS_BADGES: dict[ScanStatus, str] = {
    ScanStatus.COMPLETE: "✓",
    ScanStatus.STOPPED: "■",
    ScanStatus.INTERRUPTED: "!",
    ScanStatus.IN_PROGRESS: "…",
    ScanStatus.UNKNOWN: "–",
}


class ViewMode(Enum):
    """How a scan is shown in 3D."""

    POINTS = "points"
    MESH = "mesh"


@contextmanager
def wait_cursor() -> Iterator[None]:
    """Show a busy cursor while loading or reconstructing a scan."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def status_text(status: ScanStatus) -> str:
    """Return a status with its badge, e.g. ``"✓ complete"``."""
    return f"{STATUS_BADGES[status]} {status.value.replace('_', ' ')}"


class ScanBrowser(QWidget):
    """Lists the scans of a folder with status badges and emits the selected record."""

    scan_selected = Signal(object)  # ScanRecord | None
    scans_dir_changed = Signal(object)  # Path

    def __init__(self, scans_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scans_dir = scans_dir
        self._records: dict[str, ScanRecord] = {}

        self._folder_label = QLabel()
        self._folder_label.setWordWrap(True)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._empty_hint = QLabel(
            "No scans in this folder yet. Capture one in the Scan tab or choose another folder."
        )
        self._empty_hint.setWordWrap(True)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(lambda: self.refresh())
        open_button = QPushButton("Open folder…")
        open_button.clicked.connect(self._on_open_folder_clicked)

        buttons = QHBoxLayout()
        buttons.addWidget(refresh_button)
        buttons.addWidget(open_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._folder_label)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._empty_hint)
        layout.addLayout(buttons)

    @property
    def scans_dir(self) -> Path:
        """The folder being listed."""
        return self._scans_dir

    def current_record(self) -> ScanRecord | None:
        """The selected scan, if any."""
        item = self._list.currentItem()
        return self._records.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self, select_name: str | None = None) -> None:
        """Re-read the folder, select ``select_name`` (default: keep the selection) and emit it."""
        current = self.current_record()
        wanted = select_name or (current.name if current else None)
        records = list_scans(self._scans_dir)
        self._records = {record.name: record for record in records}

        self._list.blockSignals(True)
        self._list.clear()
        for record in records:
            item = QListWidgetItem(f"{STATUS_BADGES[record.meta.status]}  {record.name}")
            item.setData(Qt.ItemDataRole.UserRole, record.name)
            item.setToolTip(status_text(record.meta.status))
            self._list.addItem(item)
            if record.name == wanted:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)

        self._folder_label.setText(f"Folder: {self._scans_dir}")
        self._empty_hint.setVisible(not records)
        self.scan_selected.emit(self.current_record())

    @Slot()
    def _on_open_folder_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose scans folder", str(self._scans_dir))
        if folder:
            self._scans_dir = Path(folder)
            self.refresh()
            self.scans_dir_changed.emit(self._scans_dir)

    @Slot(QListWidgetItem, QListWidgetItem)
    def _on_current_item_changed(self, current: QListWidgetItem | None, _: object) -> None:
        self.scan_selected.emit(self.current_record())


class ScanInfoBox(QGroupBox):
    """Metadata and statistics of the selected scan."""

    _FIELDS = (
        "Status",
        "Started",
        "Ended",
        "Measurements",
        "Rings",
        "Height",
        "θ range",
        "θ step",
        "Stop point",
        "Source",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Scan info", parent)
        layout = QFormLayout(self)
        self._values: dict[str, QLabel] = {}
        for field in self._FIELDS:
            value = QLabel(EMPTY_VALUE)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addRow(f"{field}:", value)
            self._values[field] = value

    def clear(self) -> None:
        """Show empty values."""
        for value in self._values.values():
            value.setText(EMPTY_VALUE)

    def show_scan(self, record: ScanRecord, stats: ScanStats | None) -> None:
        """Show a scan's metadata and, if it could be loaded, its statistics."""
        self.clear()
        meta = record.meta
        stop = meta.stop_point
        texts = {
            "Status": status_text(meta.status),
            "Started": _format_timestamp(meta.started_at),
            "Ended": _format_timestamp(meta.ended_at),
            "Stop point": (
                f"ring {stop.vertical_angle}°, θ {stop.theta}°, h {stop.h:.2f} mm"
                if stop
                else EMPTY_VALUE
            ),
            "Source": meta.source or EMPTY_VALUE,
        }
        if stats is not None and stats.measurement_count:
            texts.update({
                "Measurements": str(stats.measurement_count),
                "Rings": str(stats.ring_count),
                "Height": f"{stats.height_min:.1f} – {stats.height_max:.1f} mm",
                "θ range": f"{stats.theta_min:g}° – {stats.theta_max:g}°",
                "θ step": f"{stats.theta_step:g}°" if stats.theta_step else EMPTY_VALUE,
            })
        for field, text in texts.items():
            self._values[field].setText(text)


def _format_timestamp(value: str | None) -> str:
    return value.replace("T", " ") if value else EMPTY_VALUE


class ViewerTab(QWidget):
    """Scans list and info on the left; a 3D view with display options on the right."""

    scans_dir_changed = Signal(object)  # Path
    status_message = Signal(str)

    def __init__(self, scans_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record: ScanRecord | None = None
        self._measurements: list[Measurement] | None = None
        self._mode = ViewMode.POINTS

        self._browser = ScanBrowser(scans_dir)
        self._info = ScanInfoBox()
        self._scene = SceneView()
        self._create_controls()
        self._build_layout()

        self._browser.scan_selected.connect(self._on_scan_selected)
        self._browser.scans_dir_changed.connect(self.scans_dir_changed)
        self._browser.refresh()

    @property
    def scene(self) -> SceneView:
        """The 3D view."""
        return self._scene

    @property
    def browser(self) -> ScanBrowser:
        """The scans list."""
        return self._browser

    def refresh_scans(self, select_name: str | None = None) -> None:
        """Reload the scans list, optionally selecting ``select_name``."""
        self._browser.refresh(select_name)

    def save_screenshot(self, path: Path) -> None:
        """Save the 3D view to ``path`` (PNG) and report it in the status bar."""
        try:
            self._scene.save_screenshot(path)
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Screenshot failed", f"Could not save {path}:\n{exc}")
            return
        self.status_message.emit(f"Screenshot saved to {path}")

    def shutdown(self) -> None:
        """Release the 3D view (on app close)."""
        self._scene.shutdown()

    def _create_controls(self) -> None:
        self._mode_button = QPushButton()
        self._mode_button.setCheckable(True)
        self._mode_button.setToolTip("Switch between the point cloud and the surface mesh")
        self._mode_button.toggled.connect(self._on_mode_toggled)
        self._mirror_button = QPushButton("Mirror")
        self._mirror_button.setCheckable(True)
        self._mirror_button.setToolTip("Show the mirror image (legacy +θ reconstruction)")
        self._mirror_button.toggled.connect(lambda: self._render(CameraUpdate.KEEP))
        self._smooth_check = QCheckBox("Smooth")
        self._smooth_check.setToolTip("Smooth the noisy surface mesh")
        self._smooth_check.toggled.connect(lambda: self._render(CameraUpdate.KEEP))
        self._point_size = QSpinBox()
        self._point_size.setRange(1, MAX_POINT_SIZE)
        self._point_size.setValue(DEFAULT_POINT_SIZE)
        self._point_size.setSuffix(" px")
        self._point_size.valueChanged.connect(self._scene.set_point_size)
        self._point_size_label = QLabel("Point size")
        self._reset_button = QPushButton("Reset view")
        self._reset_button.clicked.connect(self._scene.reset_camera)
        self._screenshot_button = QPushButton("Screenshot…")
        self._screenshot_button.clicked.connect(self._on_screenshot_clicked)
        self._update_mode_button()

    def _build_layout(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._browser, 1)
        left_layout.addWidget(self._info)

        toolbar = QHBoxLayout()
        for widget in (self._mode_button, self._mirror_button, self._smooth_check):
            toolbar.addWidget(widget)
        toolbar.addSpacing(12)
        toolbar.addWidget(self._point_size_label)
        toolbar.addWidget(self._point_size)
        toolbar.addStretch(1)
        toolbar.addWidget(self._reset_button)
        toolbar.addWidget(self._screenshot_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(toolbar)
        right_layout.addWidget(self._scene, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 960])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self._update_controls()

    @Slot(object)
    def _on_scan_selected(self, record: ScanRecord | None) -> None:
        if record is None:
            self._record, self._measurements = None, None
            self._info.clear()
            self._scene.show_message("Select a scan in the list")
            self._update_controls()
            return

        same_scan = self._record is not None and self._record.name == record.name
        try:
            with wait_cursor():
                measurements = load_measurements(record.path)
        except (OSError, ValueError) as exc:
            self._record, self._measurements = None, None
            self._info.show_scan(record, None)
            self._scene.show_message(f"Could not load {record.name}")
            self._update_controls()
            QMessageBox.warning(
                self, "Cannot open scan", f"{record.name} could not be loaded:\n{exc}"
            )
            return

        self._record, self._measurements = record, measurements
        self._info.show_scan(record, scan_stats(measurements))
        self._render(CameraUpdate.KEEP if same_scan else CameraUpdate.RESET)
        self._update_controls()

    @Slot(bool)
    def _on_mode_toggled(self, mesh: bool) -> None:
        self._mode = ViewMode.MESH if mesh else ViewMode.POINTS
        self._update_mode_button()
        self._update_controls()
        self._render(CameraUpdate.KEEP)

    @Slot()
    def _on_screenshot_clicked(self) -> None:
        if self._record is None:
            return
        default_path = FIGURES_DIR / f"{self._record.name}_{self._mode.value}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save screenshot", str(default_path), "PNG image (*.png)"
        )
        if path:
            self.save_screenshot(Path(path).with_suffix(".png"))

    def _render(self, camera: CameraUpdate) -> None:
        """Reconstruct the loaded scan in the current mode and show it."""
        if self._record is None or self._measurements is None:
            return
        mirror = self._mirror_button.isChecked()
        with wait_cursor():
            if self._mode is ViewMode.POINTS:
                cloud = measurements_to_points(self._measurements, mirror=mirror)
                self._scene.show_points(cloud, self._point_size.value(), self._caption(), camera)
                return
            mesh = build_surface_mesh(self._measurements, mirror=mirror)
            if mesh is None:
                self._scene.show_message(f"{self._record.name} - not enough rings for a mesh")
            else:
                self._scene.show_mesh(mesh, self._smooth_check.isChecked(), self._caption(), camera)

    def _caption(self) -> str:
        parts = [self._record.name if self._record else "", self._mode.value]
        if self._mirror_button.isChecked():
            parts.append("mirrored")
        if self._mode is ViewMode.MESH and self._smooth_check.isChecked():
            parts.append("smoothed")
        return " - ".join(parts)

    def _update_mode_button(self) -> None:
        self._mode_button.setText(f"View: {self._mode.value.capitalize()}")

    def _update_controls(self) -> None:
        loaded = self._measurements is not None
        points_mode = self._mode is ViewMode.POINTS
        self._point_size.setEnabled(points_mode)
        self._point_size_label.setEnabled(points_mode)
        self._smooth_check.setEnabled(not points_mode)
        self._reset_button.setEnabled(loaded)
        self._screenshot_button.setEnabled(loaded)
