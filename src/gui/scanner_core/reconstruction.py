"""Reconstruction of scan measurements into a point cloud or a closed surface mesh (numpy only).

Geometry: the sensors sit on the x-axis at +-R (R = sensor_dist / 2), facing the z axis.
Sensor A sees radius ``R - dist_a`` along +x, sensor B radius ``R - dist_b`` along -x. The plate
turns counter-clockwise (seen from above) as theta grows, so a world point maps to the object
frame by ``R_z(-theta)``: A lands at angle ``-theta`` and B at ``180 - theta``. ``mirror=True``
uses ``+theta`` (the mirror image produced by the original ``scanner_gui.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from scanner_core.protocol import Measurement

SENSOR_DIST_MM = 200.0  # distance between the sensors; the rotation axis is halfway between them

DEFAULT_THETA_STEP_DEG = 2.0
MIN_FILLED_BINS = 3
HEIGHT_DECIMALS = 3


@dataclass(frozen=True)
class PointCloud:
    """Reconstructed points (mm): A then B for each measurement."""

    points: np.ndarray  # (N, 3) float64
    heights: np.ndarray  # (N,) = points[:, 2]
    sensor: np.ndarray  # (N,) int8: 0 = sensor A, 1 = sensor B


@dataclass(frozen=True)
class SurfaceMesh:
    """Closed triangle mesh (mm) with outward winding."""

    vertices: np.ndarray  # (V, 3) float64
    faces: np.ndarray  # (F, 3) int64
    heights: np.ndarray  # (V,)


@dataclass(frozen=True)
class ScanStats:
    """Summary numbers of a scan."""

    measurement_count: int
    ring_count: int
    height_min: float
    height_max: float
    theta_min: float
    theta_max: float
    theta_step: float | None


def _to_array(measurements: Sequence[Measurement]) -> np.ndarray:
    """Return an (N, 4) float array of ``theta, h, dist_a, dist_b``."""
    rows = [(m.theta, m.h, m.dist_a, m.dist_b) for m in measurements]
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


def _sensor_angles_deg(theta: np.ndarray, mirror: bool) -> tuple[np.ndarray, np.ndarray]:
    """Return the object-frame angles (deg) of the sensor A and B samples."""
    phi_a = theta if mirror else -theta
    return phi_a, phi_a + 180.0


def measurements_to_points(
    measurements: Sequence[Measurement],
    sensor_dist: float = SENSOR_DIST_MM,
    mirror: bool = False,
) -> PointCloud:
    """Convert measurements to 3D points, two per measurement (A first, then B)."""
    theta, h, dist_a, dist_b = _to_array(measurements).T
    half_dist = sensor_dist / 2.0
    phi_a, phi_b = np.deg2rad(_sensor_angles_deg(theta, mirror))
    r_a = half_dist - dist_a
    r_b = half_dist - dist_b

    point_a = np.column_stack([r_a * np.cos(phi_a), r_a * np.sin(phi_a), h])
    point_b = np.column_stack([r_b * np.cos(phi_b), r_b * np.sin(phi_b), h])
    points = np.stack([point_a, point_b], axis=1).reshape(-1, 3)
    sensor = np.tile(np.array([0, 1], dtype=np.int8), len(theta))
    return PointCloud(points=points, heights=points[:, 2].copy(), sensor=sensor)


def estimate_theta_step(measurements: Sequence[Measurement]) -> float | None:
    """Return the smallest positive gap between distinct theta values, or None if there is none."""
    thetas = np.unique(np.round(_to_array(measurements)[:, 0], 6))
    if thetas.size < 2:
        return None
    return float(np.diff(thetas).min())


def build_surface_mesh(
    measurements: Sequence[Measurement],
    sensor_dist: float = SENSOR_DIST_MM,
    mirror: bool = False,
    close_caps: bool = True,
) -> SurfaceMesh | None:
    """Build a closed surface over the rings of a scan; None if fewer than two usable rings."""
    data = _to_array(measurements)
    if data.shape[0] == 0:
        return None
    theta, h, dist_a, dist_b = data.T
    step = estimate_theta_step(measurements) or DEFAULT_THETA_STEP_DEG
    n_bins = max(round(360.0 / step), MIN_FILLED_BINS)

    # Average the radius of every sample (both sensors) per (ring, angle bin).
    half_dist = sensor_dist / 2.0
    phi = np.concatenate(_sensor_angles_deg(theta, mirror))
    radius = np.concatenate([half_dist - dist_a, half_dist - dist_b])
    # A negative radius (surface seen beyond the axis) is the same point on the opposite side.
    phi = np.where(radius < 0, phi + 180.0, phi)
    radius = np.abs(radius)
    heights = np.tile(np.round(h, HEIGHT_DECIMALS), 2)
    ring_heights, ring_index = np.unique(heights, return_inverse=True)
    bin_index = np.round(np.mod(phi, 360.0) / step).astype(np.int64) % n_bins
    flat_index = ring_index * n_bins + bin_index
    grid_size = ring_heights.size * n_bins
    counts = np.bincount(flat_index, minlength=grid_size).reshape(-1, n_bins)
    sums = np.bincount(flat_index, weights=radius, minlength=grid_size).reshape(-1, n_bins)
    filled = counts > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        grid = sums / counts  # NaN where empty

    usable = filled.sum(axis=1) >= MIN_FILLED_BINS
    grid, filled, ring_heights = grid[usable], filled[usable], ring_heights[usable]
    n_rings = ring_heights.size
    if n_rings < 2:
        return None

    # Fill empty bins by linear interpolation over angle, wrapping around 360 degrees.
    bin_angles = np.arange(n_bins) * step
    for row, row_filled in zip(grid, filled):
        if not row_filled.all():
            row[~row_filled] = np.interp(
                bin_angles[~row_filled], bin_angles[row_filled], row[row_filled], period=360.0
            )

    angles = np.deg2rad(bin_angles)
    vertices = np.column_stack([
        (grid * np.cos(angles)).ravel(),
        (grid * np.sin(angles)).ravel(),
        np.repeat(ring_heights, n_bins),
    ])

    # Side quads (i, j) -> (i, j+1) -> (i+1, j+1) -> (i+1, j): counter-clockwise seen from outside,
    # because j runs counter-clockwise around z and i runs upwards.
    ring = np.arange(n_rings - 1)[:, None] * n_bins
    j = np.arange(n_bins)
    j_next = (j + 1) % n_bins
    a, b = ring + j, ring + j_next
    c, d = b + n_bins, a + n_bins
    faces = [
        np.stack([a, b, c], axis=-1).reshape(-1, 3),
        np.stack([a, c, d], axis=-1).reshape(-1, 3),
    ]

    if close_caps:
        # Fan the end rings around the rotation axis, not their centroid: each ring is a radial
        # function r(phi) around the axis, so no fan triangle can flip on a non-convex ring.
        bottom_center, top_center = len(vertices), len(vertices) + 1
        top_ring = (n_rings - 1) * n_bins
        centers = [[0.0, 0.0, ring_heights[0]], [0.0, 0.0, ring_heights[-1]]]
        vertices = np.vstack([vertices, centers])
        # Bottom fan is reversed (clockwise from above) so its normals point down.
        faces.append(np.column_stack([np.full(n_bins, bottom_center), j_next, j]))
        faces.append(
            np.column_stack([np.full(n_bins, top_center), top_ring + j, top_ring + j_next])
        )

    return SurfaceMesh(
        vertices=vertices,
        faces=np.concatenate(faces).astype(np.int64),
        heights=vertices[:, 2].copy(),
    )


def scan_stats(measurements: Sequence[Measurement]) -> ScanStats:
    """Return summary numbers of a scan (ranges are 0 for an empty scan)."""
    data = _to_array(measurements)
    if data.shape[0] == 0:
        return ScanStats(0, 0, 0.0, 0.0, 0.0, 0.0, None)
    theta, h = data[:, 0], data[:, 1]
    return ScanStats(
        measurement_count=data.shape[0],
        ring_count=np.unique(np.round(h, HEIGHT_DECIMALS)).size,
        height_min=float(h.min()),
        height_max=float(h.max()),
        theta_min=float(theta.min()),
        theta_max=float(theta.max()),
        theta_step=estimate_theta_step(measurements),
    )
