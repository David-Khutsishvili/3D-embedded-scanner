"""Capturing one scan from protocol events, plus the ``PING`` readiness handshake."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from scanner_core.links import ScannerLink
from scanner_core.protocol import CMD_PING, Event, EventKind, Measurement, StopPoint, parse_line
from scanner_core.scan_files import ScanMeta, ScanStatus, now_iso, save_measurements, save_meta


class CaptureSession:
    """Accumulates one scan from protocol events and persists it (json + meta).

    New data is autosaved with status ``in_progress`` at most every ``autosave_interval_s``.
    """

    def __init__(self, scan_path: Path, source: str, autosave_interval_s: float = 5.0) -> None:
        self._scan_path = Path(scan_path)
        self._autosave_interval_s = autosave_interval_s
        self._meta = ScanMeta(name=self._scan_path.stem, source=source)
        self._measurements: list[Measurement] = []
        self._ring_heights: set[float] = set()
        self._stop_point: StopPoint | None = None
        self._started = False
        self._finished = False
        self._start_time: float | None = None
        self._end_time: float | None = None
        self._last_save_time = 0.0
        self._unsaved = False

    def handle_event(self, event: Event) -> None:
        """Update the scan from one event; events after ``SCAN_END`` are ignored."""
        if self._finished:
            return
        if event.kind is EventKind.SCAN_START:
            self._mark_started()
        elif event.kind is EventKind.MEASUREMENT and event.measurement is not None:
            self._mark_started()
            self._measurements.append(event.measurement)
            self._ring_heights.add(round(event.measurement.h, 3))
            self._unsaved = True
        elif event.kind is EventKind.SCAN_STOPPED:
            self._stop_point = event.stop_point
            self._unsaved = True
        elif event.kind is EventKind.SCAN_END:
            self._finished = True
            self._end_time = time.monotonic()
            return

        now = time.monotonic()
        autosave_due = now - self._last_save_time >= self._autosave_interval_s
        if self._started and self._unsaved and autosave_due:
            self._save(ScanStatus.IN_PROGRESS)
            self._last_save_time = now

    @property
    def measurements(self) -> list[Measurement]:
        """Measurements captured so far (do not modify)."""
        return self._measurements

    @property
    def started(self) -> bool:
        """Whether ``SCAN_START`` or a measurement was seen."""
        return self._started

    @property
    def finished(self) -> bool:
        """Whether ``SCAN_END`` was seen."""
        return self._finished

    @property
    def stop_point(self) -> StopPoint | None:
        """Stop point reported by ``SCAN_STOPPED``, if any."""
        return self._stop_point

    @property
    def last_h(self) -> float | None:
        """Height of the latest measurement, if any."""
        return self._measurements[-1].h if self._measurements else None

    @property
    def ring_count(self) -> int:
        """Number of distinct heights seen."""
        return len(self._ring_heights)

    @property
    def elapsed_s(self) -> float:
        """Seconds since the scan started (frozen once it finished); 0 before the start."""
        if self._start_time is None:
            return 0.0
        end = self._end_time if self._end_time is not None else time.monotonic()
        return end - self._start_time

    def finalize(self) -> ScanMeta:
        """Save the scan with its final status and return the meta (a second call rewrites)."""
        if self._finished and self._stop_point is not None:
            status = ScanStatus.STOPPED
        elif self._finished:
            status = ScanStatus.COMPLETE
        else:
            status = ScanStatus.INTERRUPTED
        if self._start_time is not None and self._end_time is None:
            self._end_time = time.monotonic()
        if self._meta.ended_at is None:
            self._meta.ended_at = now_iso()
        self._save(status)
        self._unsaved = False
        return self._meta

    def _mark_started(self) -> None:
        if not self._started:
            self._started = True
            self._start_time = time.monotonic()
            self._last_save_time = self._start_time
        if self._meta.started_at is None:
            self._meta.started_at = now_iso()

    def _save(self, status: ScanStatus) -> None:
        self._meta.status = status
        self._meta.measurement_count = len(self._measurements)
        self._meta.stop_point = self._stop_point
        save_measurements(self._scan_path, self._measurements)
        save_meta(self._scan_path, self._meta)


def wait_for_ready(
    link: ScannerLink,
    timeout_s: float,
    ping_interval_s: float = 1.0,
    on_line: Callable[[str], None] | None = None,
) -> bool:
    """Ping until the scanner answers ``PONG`` or ``READY`` (True) or ``timeout_s`` passes (False).

    Every received line is passed to ``on_line``. ``BUSY`` keeps waiting.
    ConnectionError from the link propagates.
    """
    deadline = time.monotonic() + timeout_s
    next_ping = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            return False
        if now >= next_ping:
            link.write_line(CMD_PING)
            next_ping = now + ping_interval_s
        line = link.read_line(min(next_ping, deadline) - now)
        if line is None:
            continue
        if on_line is not None:
            on_line(line)
        event = parse_line(line)
        if event is not None and event.kind in (EventKind.PONG, EventKind.READY):
            return True
