"""PySide6 user interface of the 3D scanner app.

Modules:
    main_window  ``MainWindow``: the Scan and Viewer tabs and the close sequence
    link_worker  ``LinkWorker`` / ``LinkConnection``: scanner I/O in a ``QThread``
    scan_tab     ``ScanTab``: connect, start/stop a capture, progress, live preview, log
    viewer_tab   ``ViewerTab``: browse saved scans and view them as points or mesh
    scene        ``SceneView``: height-coloured PyVista rendering shared by both tabs
"""

import os

# pyvistaqt loads Qt through qtpy; pin it to PySide6 so another installed binding is never mixed in.
os.environ.setdefault("QT_API", "pyside6")
