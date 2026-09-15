"""3D Embedded Scanner desktop app: capture scans (scanner or simulator) and view them in 3D.

Usage:
    python src/gui/scanner_app.py [--scans-dir PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtWidgets import QApplication

from scanner_core.scan_files import default_scans_dir
from scanner_ui.main_window import WINDOW_TITLE, MainWindow


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture scans from the 3D scanner (or its simulator) and view them in 3D."
    )
    parser.add_argument(
        "--scans-dir",
        type=Path,
        default=default_scans_dir(),
        metavar="PATH",
        help="Folder of the scan files (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(WINDOW_TITLE)
    window = MainWindow(args.scans_dir.resolve())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
