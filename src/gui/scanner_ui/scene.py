"""Height-coloured PyVista rendering of scans in a ``QtInteractor``, shared by both tabs."""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from typing import Any, Iterator

import pyvista as pv
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget
from pyvistaqt import QtInteractor

from scanner_core.reconstruction import PointCloud, SurfaceMesh
from scanner_core.visual import point_cloud_to_polydata, surface_mesh_to_polydata

HEIGHT_ARRAY = "height"
HEIGHT_CMAP = "viridis"
SMOOTH_ITERATIONS = 50
SMOOTH_RELAXATION = 0.1  # pyvista's default 0.01 barely changes the noisy ToF mesh
TEXT_COLOR = "#2b2f36"
BACKGROUND_BOTTOM = "#fbfcfd"
BACKGROUND_TOP = "#d6dee9"

# Vertical bar at the right edge; the camera centres the model, so the bar never covers it.
SCALAR_BAR_ARGS: dict[str, Any] = {
    "title": "Height (mm)",
    "vertical": True,
    "position_x": 0.86,
    "position_y": 0.2,
    "width": 0.05,
    "height": 0.6,
    "n_labels": 5,
    "fmt": "%.0f",
    "color": TEXT_COLOR,
    "title_font_size": 14,
    "label_font_size": 12,
}


class CameraUpdate(Enum):
    """What happens to the camera when new data is shown."""

    KEEP = auto()  # keep the user's view (only the mode, mirror or smoothing changed)
    FIT = auto()  # keep the view direction, zoom to fit the data (live preview)
    RESET = auto()  # default isometric view of the whole object


class SceneView(QWidget):
    """A ``QtInteractor`` showing one scan coloured by height, with axes and a scalar bar.

    Renders only while visible: changes made while hidden (e.g. in an inactive tab) are
    drawn when the widget is shown again.
    """

    _SCAN_ACTOR = "scan"
    _CAPTION_ACTOR = "caption"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dirty = False
        # No auto-update timer: renders happen only on changes and user interaction.
        self._plotter = QtInteractor(self, auto_update=False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plotter.interactor)
        with self._batch():
            self._plotter.set_background(BACKGROUND_BOTTOM, top=BACKGROUND_TOP)
            self._plotter.add_axes(color=TEXT_COLOR)
            # pyvista binds 'q' to "prepare for close", which is meaningless in an embedded view.
            self._plotter.clear_events_for_key("q")

    @property
    def plotter(self) -> QtInteractor:
        """The underlying PyVista interactor."""
        return self._plotter

    def show_points(
        self, cloud: PointCloud, point_size: float, caption: str, camera: CameraUpdate
    ) -> None:
        """Show a point cloud as height-coloured spheres."""
        if len(cloud.points) == 0:
            self.show_message(f"{caption} - no points")
            return
        self._show(
            point_cloud_to_polydata(cloud),
            caption,
            camera,
            style="points",
            point_size=point_size,
            render_points_as_spheres=True,
        )

    def show_mesh(
        self, mesh: SurfaceMesh, smooth: bool, caption: str, camera: CameraUpdate
    ) -> None:
        """Show a surface mesh, optionally smoothed, with smooth shading."""
        polydata = surface_mesh_to_polydata(mesh)
        if smooth:
            polydata = smoothed(polydata)
        self._show(polydata, caption, camera, smooth_shading=True, specular=0.2)

    def show_message(self, text: str) -> None:
        """Remove the scan (and its scalar bar) and show ``text`` instead."""
        with self._batch():
            self._plotter.remove_actor(self._SCAN_ACTOR, render=False)
            self._set_caption(text)

    def set_point_size(self, size: float) -> None:
        """Change the point size of the shown scan without rebuilding it."""
        actor = self._plotter.actors.get(self._SCAN_ACTOR)
        if actor is not None:
            with self._batch():
                actor.prop.point_size = size

    def reset_camera(self) -> None:
        """Return to the default isometric view of the whole object."""
        with self._batch():
            self._apply_camera(CameraUpdate.RESET)

    def save_screenshot(self, path: Path) -> None:
        """Save the current view as an image file (PNG)."""
        if self._dirty:
            self._render_now()
        self._plotter.screenshot(str(path))

    def shutdown(self) -> None:
        """Release the render window; call once before the widget is destroyed."""
        self._plotter.close()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        """Draw changes that were made while the view was hidden."""
        super().showEvent(event)
        if self._dirty:
            self._render_now()

    def _show(
        self, polydata: pv.PolyData, caption: str, camera: CameraUpdate, **style: Any
    ) -> None:
        """Replace the scan actor with ``polydata`` coloured by height."""
        with self._batch():
            self._plotter.add_mesh(
                polydata,
                scalars=HEIGHT_ARRAY,
                cmap=HEIGHT_CMAP,
                show_scalar_bar=True,
                scalar_bar_args=dict(SCALAR_BAR_ARGS),
                name=self._SCAN_ACTOR,
                reset_camera=False,
                render=False,
                **style,
            )
            self._set_caption(caption)
            self._apply_camera(camera)

    def _apply_camera(self, camera: CameraUpdate) -> None:
        if camera is CameraUpdate.RESET:
            self._plotter.view_isometric(render=False)
        elif camera is CameraUpdate.FIT:
            self._plotter.reset_camera(render=False)

    def _set_caption(self, text: str) -> None:
        self._plotter.add_text(
            text,
            position="upper_left",
            font_size=11,
            color=TEXT_COLOR,
            name=self._CAPTION_ACTOR,
            render=False,
        )

    @contextmanager
    def _batch(self) -> Iterator[None]:
        """Apply changes without intermediate renders, then render once (now or when shown)."""
        self._plotter.suppress_rendering = True
        try:
            yield
        finally:
            self._plotter.suppress_rendering = False
        if self.isVisible():
            self._render_now()
        else:
            self._dirty = True

    def _render_now(self) -> None:
        self._dirty = False
        self._plotter.render()


def smoothed(polydata: pv.PolyData) -> pv.PolyData:
    """Return a Laplacian-smoothed copy (ToF data is noisy) with heights and normals recomputed."""
    result = polydata.smooth(n_iter=SMOOTH_ITERATIONS, relaxation_factor=SMOOTH_RELAXATION)
    result.point_data[HEIGHT_ARRAY] = result.points[:, 2]
    return result.compute_normals(split_vertices=False, consistent_normals=False)
