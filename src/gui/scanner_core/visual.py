"""Conversion of reconstruction results to PyVista data sets.

This is the only ``scanner_core`` module that imports pyvista.
"""

from __future__ import annotations

import numpy as np
import pyvista as pv

from scanner_core.reconstruction import PointCloud, SurfaceMesh


def point_cloud_to_polydata(cloud: PointCloud) -> pv.PolyData:
    """Return the cloud as vertex-only PolyData with a ``height`` point array."""
    polydata = pv.PolyData(cloud.points)
    polydata.point_data["height"] = cloud.heights
    return polydata


def surface_mesh_to_polydata(mesh: SurfaceMesh) -> pv.PolyData:
    """Return the mesh as triangle PolyData with a ``height`` point array and normals."""
    # VTK cell array layout: [3, i0, i1, i2, 3, ...].
    cells = np.column_stack([np.full(len(mesh.faces), 3, dtype=np.int64), mesh.faces]).ravel()
    polydata = pv.PolyData(mesh.vertices, cells)
    polydata.point_data["height"] = mesh.heights
    # Keep the mesh's own (outward) winding and vertex order.
    return polydata.compute_normals(split_vertices=False, consistent_normals=False)
