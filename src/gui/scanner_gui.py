"""Reconstruct a captured scan and show it as a point cloud or surface mesh in a PyVista window.

Usage:
    python src/gui/scanner_gui.py <measurements_path> [--color HEX] [--no-plot] [--mesh] [--mirror]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import numpy as np
import pyvista as pv

from scanner_core.reconstruction import build_surface_mesh, measurements_to_points
from scanner_core.scan_files import load_measurements
from scanner_core.visual import point_cloud_to_polydata, surface_mesh_to_polydata

POINT_SIZE = 5


def coords(measurements_path: str | Path, mirror: bool = False) -> list[np.ndarray]:
    """Return the 3D points of a scan file, two per measurement (sensor A, then B)."""
    cloud = measurements_to_points(load_measurements(Path(measurements_path)), mirror=mirror)
    return list(cloud.points)


def show(polydata: pv.PolyData, color: str, as_points: bool) -> None:
    """Open an interactive PyVista window with ``polydata`` and axes."""
    plotter = pv.Plotter()
    if as_points:
        plotter.add_mesh(
            polydata, color=color, point_size=POINT_SIZE, render_points_as_spheres=True
        )
    else:
        plotter.add_mesh(polydata, color=color, smooth_shading=True)
    plotter.add_axes()
    plotter.show()


def hex_color(value: str) -> str:
    """Validate a ``#RRGGBB`` (or ``RRGGBB``) color for argparse."""
    value = value if value.startswith("#") else f"#{value}"
    if len(value) != 7:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")
    try:
        int(value[1:], 16)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid hex color: {value!r}")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load scanner measurements and plot the reconstructed object."
    )
    parser.add_argument("measurements_path", help="Path to the JSON measurements file.")
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip plotting; just print the coords (or the mesh size).",
    )
    parser.add_argument(
        "--color",
        type=hex_color,
        default="#FFFFFF",
        help="Color as a hex code (e.g. #FF8800).",
    )
    parser.add_argument(
        "--mesh", action="store_true", help="Show the surface mesh instead of the point cloud."
    )
    parser.add_argument(
        "--mirror", action="store_true", help="Legacy mirror-image reconstruction (+theta)."
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        measurements = load_measurements(Path(args.measurements_path))
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.mesh:
        mesh = build_surface_mesh(measurements, mirror=args.mirror)
        if mesh is None:
            print("Error: not enough rings to build a surface mesh.", file=sys.stderr)
            return 1
        if args.no_plot:
            print(f"Surface mesh: {len(mesh.vertices)} vertices, {len(mesh.faces)} triangles")
        else:
            show(surface_mesh_to_polydata(mesh), args.color, as_points=False)
    else:
        cloud = measurements_to_points(measurements, mirror=args.mirror)
        if args.no_plot:
            print(list(cloud.points))
        else:
            show(point_cloud_to_polydata(cloud), args.color, as_points=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
