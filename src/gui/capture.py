"""Capture a scan from the scanner over serial and save it as JSON plus a ``.meta.json`` sidecar.

Usage:
    python src/gui/capture.py <output_path> --port PORT [--baud N] [--no-start] [--ready-timeout S]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Sequence

from scanner_core.capture_session import CaptureSession, wait_for_ready
from scanner_core.links import ScannerLink, SerialLink
from scanner_core.protocol import (
    BAUD_RATE,
    CMD_START,
    CMD_STOP,
    SCAN_END,
    SCAN_START,
    EventKind,
    parse_line,
)
from scanner_core.scan_files import ScanMeta

DEFAULT_BAUD = BAUD_RATE
DEFAULT_READY_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 15.0
READ_TIMEOUT_S = 0.5


def _print_log_line(line: str) -> None:
    """Print a scanner line that is not a protocol marker or measurement."""
    event = parse_line(line)
    if event is not None and event.kind is EventKind.LOG:
        print(f"(ignored) {line}")


def _read_scan(link: ScannerLink, session: CaptureSession, timeout_s: float | None = None) -> None:
    """Feed scanner lines into ``session`` until ``SCAN_END`` arrives or ``timeout_s`` passes."""
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while not session.finished:
        if deadline is not None and time.monotonic() >= deadline:
            print(f"No {SCAN_END} within {timeout_s:g} s.")
            return
        line = link.read_line(READ_TIMEOUT_S)
        event = parse_line(line) if line is not None else None
        if event is None:
            continue
        session.handle_event(event)
        if event.kind is EventKind.SCAN_START:
            print("Scan started.")
        elif event.kind is EventKind.SCAN_STOPPED and event.stop_point is not None:
            stop = event.stop_point
            print(
                f"Scan stopped at vertical angle {stop.vertical_angle}, "
                f"theta {stop.theta}, h {stop.h:.3f} mm."
            )
        elif event.kind is EventKind.SCAN_END:
            print(f"Scan finished, {len(session.measurements)} measurements captured.")
        elif event.kind is not EventKind.MEASUREMENT:
            print(f"(ignored) {event.raw}")


def _stop_scan(link: ScannerLink, session: CaptureSession) -> None:
    """Ask the scanner to stop and wait for ``SCAN_END``; a second Ctrl-C stops waiting."""
    print("\nStopping scan...")
    try:
        link.write_line(CMD_STOP)
        _read_scan(link, session, timeout_s=STOP_TIMEOUT_S)
    except KeyboardInterrupt:
        print("Not waiting any longer.")
    except ConnectionError as exc:
        print(f"Connection lost: {exc}")


def run_capture(
    link: ScannerLink,
    output_path: Path,
    send_start: bool = True,
    ready_timeout: float = DEFAULT_READY_TIMEOUT_S,
) -> ScanMeta:
    """Capture one scan from the open ``link`` into ``output_path`` and return its saved meta.

    Ctrl-C during the scan sends ``STOP`` and still saves the data. Raises TimeoutError if the
    scanner is not ready, and ConnectionError or KeyboardInterrupt if the link fails or the user
    cancels before the scan was requested; nothing is saved in those cases.
    """
    if send_start:
        print("Waiting for scanner READY...")
        if not wait_for_ready(link, ready_timeout, on_line=_print_log_line):
            raise TimeoutError(
                f"scanner on {link.description} not ready within {ready_timeout:g} s "
                "(check power and firmware)"
            )
        link.write_line(CMD_START)
    print(f"Listening on {link.description}... waiting for {SCAN_START}")

    session = CaptureSession(Path(output_path), source=link.description)
    try:
        _read_scan(link, session)
    except KeyboardInterrupt:
        _stop_scan(link, session)
    except ConnectionError as exc:
        print(f"Connection lost: {exc}")

    meta = session.finalize()
    print(f"Saved to {output_path}")
    print(f"Status: {meta.status.value} ({meta.measurement_count} measurements)")
    return meta


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture scan.ino's serial output and save it as JSON."
    )
    parser.add_argument("output_path", help="Path to write the captured measurements JSON to.")
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port the scanner is connected on (e.g. /dev/ttyACM0).",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Serial baud rate (default: {DEFAULT_BAUD}).",
    )
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Only listen: don't handshake or send START (legacy firmware that scans on boot).",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=DEFAULT_READY_TIMEOUT_S,
        metavar="S",
        help=f"Scanner readiness timeout in seconds (default: {DEFAULT_READY_TIMEOUT_S:g}).",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    link = SerialLink(args.port, args.baud)
    try:
        link.open()
        run_capture(
            link,
            Path(args.output_path),
            send_start=not args.no_start,
            ready_timeout=args.ready_timeout,
        )
    except OSError as exc:  # ConnectionError, TimeoutError, or a failed save
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled before the scan started, nothing saved.")
        return 130
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
