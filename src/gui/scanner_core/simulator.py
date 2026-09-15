"""Simulated scanner that speaks the ``scan.ino`` protocol by replaying an existing scan file."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from scanner_core.links import ScannerLink
from scanner_core.protocol import (
    BUSY,
    CMD_PING,
    CMD_START,
    CMD_STOP,
    PONG,
    READY,
    SCAN_END,
    SCAN_START,
    SCAN_STOPPED,
    Measurement,
)
from scanner_core.scan_files import load_measurements

# Firmware constants used to derive the stop ring from a height (scan.ino: MAX_ANGLE, dh).
MAX_VERTICAL_ANGLE = 176
HEIGHT_PER_DEGREE_MM = 0.36

_COMMAND_POLL_S = 0.05
_JOIN_TIMEOUT_S = 1.0


def _measurement_line(measurement: Measurement) -> str:
    """Format a measurement exactly like the firmware's ``send_data()``."""
    theta = measurement.to_dict()["theta"]
    return (
        f'{{"theta":{theta},"h":{measurement.h:.3f},'
        f'"dist_a":{measurement.dist_a},"dist_b":{measurement.dist_b}}}'
    )


def _stop_line(last: Measurement | None) -> str:
    """Format ``SCAN_STOPPED`` for the last emitted measurement (or the start position)."""
    if last is None:
        vertical_angle, theta, h = MAX_VERTICAL_ANGLE, 0, 0.0
    else:
        # Older scans used a larger elevation range, so clamp to the servo's lowest angle.
        vertical_angle = max(0, round(MAX_VERTICAL_ANGLE - last.h / HEIGHT_PER_DEGREE_MM))
        theta, h = round(last.theta), last.h
    return f'{SCAN_STOPPED} {{"vertical_angle":{vertical_angle},"theta":{theta},"h":{h:.3f}}}'


class SimulatedLink(ScannerLink):
    """Emulates the scan.ino protocol by replaying an existing scan file.

    For demos and testing without hardware. Thread-safe: a replay thread answers the commands
    and fills an outgoing line queue that ``read_line`` consumes.
    """

    def __init__(
        self,
        replay_path: Path,
        line_interval_s: float = 0.003,
        boot_delay_s: float = 1.0,
        reset_delay_s: float = 1.0,
    ) -> None:
        self._replay_path = Path(replay_path)
        self._line_interval_s = line_interval_s
        self._boot_delay_s = boot_delay_s
        self._reset_delay_s = reset_delay_s
        self._measurements: list[Measurement] = []
        self._outgoing: queue.Queue[str] = queue.Queue()
        self._commands: queue.Queue[str] = queue.Queue()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def description(self) -> str:
        return f"simulator:{self._replay_path.name}"

    def open(self) -> None:
        if self.is_open:
            return
        try:
            self._measurements = load_measurements(self._replay_path)
        except (OSError, ValueError) as exc:
            raise ConnectionError(f"cannot load replay scan {self._replay_path}: {exc}") from exc
        self._outgoing = queue.Queue()
        self._commands = queue.Queue()
        self._shutdown = threading.Event()
        self._thread = threading.Thread(target=self._run, name="SimulatedLink", daemon=True)
        self._thread.start()

    def close(self) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            self._shutdown.set()
            thread.join(_JOIN_TIMEOUT_S)

    @property
    def is_open(self) -> bool:
        return self._thread is not None

    def write_line(self, text: str) -> None:
        if not self.is_open:
            raise ConnectionError("simulator is not open")
        self._commands.put(text.strip())

    def read_line(self, timeout: float) -> str | None:
        if not self.is_open:
            raise ConnectionError("simulator is not open")
        try:
            return self._outgoing.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def _emit(self, line: str) -> None:
        self._outgoing.put(line)

    def _run(self) -> None:
        """Firmware main loop: boot, then answer commands until closed."""
        if self._shutdown.wait(self._boot_delay_s):
            return
        self._emit(READY)
        while not self._shutdown.is_set():
            try:
                command = self._commands.get(timeout=_COMMAND_POLL_S)
            except queue.Empty:
                continue
            if command == CMD_START:
                self._replay_scan()
            elif command == CMD_PING:
                self._emit(PONG)
            elif command == CMD_STOP:
                self._emit(READY)
            elif command:
                self._emit(f"WARN: unknown command {command}")

    def _replay_scan(self) -> None:
        """Emit one scan (or its stopped prefix), then reset and report READY."""
        self._emit(SCAN_START)
        last: Measurement | None = None
        stopped = False
        # Pace against an absolute schedule so coarse OS sleep granularity doesn't slow the replay.
        next_emit = time.monotonic()
        for measurement in self._measurements:
            delay = next_emit - time.monotonic()
            if delay > 0 and self._shutdown.wait(delay):
                return
            if self._shutdown.is_set():
                return
            if self._stop_requested():
                stopped = True
                break
            self._emit(_measurement_line(measurement))
            last = measurement
            next_emit += self._line_interval_s

        if stopped:
            self._emit(_stop_line(last))
        self._emit(SCAN_END)
        if self._shutdown.wait(self._reset_delay_s):
            return
        self._emit(READY)

    def _stop_requested(self) -> bool:
        """Answer commands received mid-scan; return True as soon as STOP is found."""
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return False
            if command == CMD_STOP:
                return True
            if command in (CMD_PING, CMD_START):
                self._emit(BUSY)
            elif command:
                self._emit(f"WARN: unknown command {command}")
