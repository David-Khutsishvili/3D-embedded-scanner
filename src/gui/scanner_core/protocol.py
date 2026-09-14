"""Serial line protocol between ``scan.ino`` and the PC (one ASCII line per message)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Mapping

BAUD_RATE = 115200

# Firmware -> PC markers.
SCAN_START = "SCAN_START"
SCAN_END = "SCAN_END"
SCAN_STOPPED = "SCAN_STOPPED"
READY = "READY"
PONG = "PONG"
BUSY = "BUSY"

# PC -> firmware commands.
CMD_PING = "PING"
CMD_START = "START"
CMD_STOP = "STOP"


def _number(value: Any) -> float | int:
    """Return ``value`` if it is a finite JSON number, else raise TypeError/ValueError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"expected a finite number, got {value!r}")
    return value


def _integer(value: Any) -> int:
    """Return an integral JSON number as int, else raise TypeError/ValueError."""
    number = _number(value)
    if isinstance(number, float) and not number.is_integer():
        raise ValueError(f"expected an integer, got {value!r}")
    return int(number)


@dataclass(frozen=True)
class Measurement:
    """One scan sample: plate angle ``theta`` (deg), height ``h`` (mm), sensor distances (mm)."""

    theta: float
    h: float
    dist_a: int
    dist_b: int

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form used in scan files (``theta`` is an int when integral)."""
        theta = int(self.theta) if float(self.theta).is_integer() else float(self.theta)
        return {"theta": theta, "h": float(self.h), "dist_a": self.dist_a, "dist_b": self.dist_b}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Measurement:
        """Build from a JSON dict; raises KeyError/ValueError/TypeError on bad input."""
        return cls(
            theta=float(_number(data["theta"])),
            h=float(_number(data["h"])),
            dist_a=_integer(data["dist_a"]),
            dist_b=_integer(data["dist_b"]),
        )


@dataclass(frozen=True)
class StopPoint:
    """Where ``STOP`` aborted a scan: servo ``vertical_angle`` (ring), ``theta`` and ``h``."""

    vertical_angle: int
    theta: int
    h: float

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form used in the meta sidecar."""
        return {"vertical_angle": self.vertical_angle, "theta": self.theta, "h": round(self.h, 3)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StopPoint:
        """Build from a JSON dict; raises KeyError/ValueError/TypeError on bad input."""
        return cls(
            vertical_angle=_integer(data["vertical_angle"]),
            theta=_integer(data["theta"]),
            h=float(_number(data["h"])),
        )


class EventKind(Enum):
    """Kind of a line received from the scanner."""

    MEASUREMENT = auto()
    SCAN_START = auto()
    SCAN_END = auto()
    SCAN_STOPPED = auto()
    READY = auto()
    PONG = auto()
    BUSY = auto()
    LOG = auto()


@dataclass(frozen=True)
class Event:
    """A parsed scanner line; ``measurement``/``stop_point`` are set for the matching kinds."""

    kind: EventKind
    raw: str
    measurement: Measurement | None = None
    stop_point: StopPoint | None = None


_MARKER_KINDS = {
    SCAN_START: EventKind.SCAN_START,
    SCAN_END: EventKind.SCAN_END,
    READY: EventKind.READY,
    PONG: EventKind.PONG,
    BUSY: EventKind.BUSY,
}


def parse_line(raw: str) -> Event | None:
    """Parse one scanner line; returns None for blank lines and LOG for anything unrecognised."""
    line = raw.strip()
    if not line:
        return None

    if line in _MARKER_KINDS:
        return Event(_MARKER_KINDS[line], line)

    if line.startswith(SCAN_STOPPED + " "):
        try:
            stop_point = StopPoint.from_dict(json.loads(line[len(SCAN_STOPPED):]))
        except (ValueError, KeyError, TypeError):
            return Event(EventKind.LOG, line)
        return Event(EventKind.SCAN_STOPPED, line, stop_point=stop_point)

    if line.startswith("{"):
        try:
            measurement = Measurement.from_dict(json.loads(line))
        except (ValueError, KeyError, TypeError):
            return Event(EventKind.LOG, line)
        return Event(EventKind.MEASUREMENT, line, measurement=measurement)

    return Event(EventKind.LOG, line)
