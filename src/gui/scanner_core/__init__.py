"""Shared, Qt-free core of the 3D scanner tools.

Modules:
    protocol        serial line protocol of ``scan.ino`` (markers, commands, parsing)
    links           line-based scanner connections (``ScannerLink``, ``SerialLink``)
    simulator       ``SimulatedLink``: replays a scan file, for demos without hardware
    scan_files      scan measurement files and their ``.meta.json`` sidecars
    capture_session accumulating and saving one scan from protocol events
    reconstruction  measurements to point cloud / closed surface mesh (numpy only)
    visual          conversion to PyVista data sets (the only module importing pyvista)
"""
