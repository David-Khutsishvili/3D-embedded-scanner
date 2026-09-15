"""Scan files: ``<name>.json`` measurement arrays and their ``<name>.meta.json`` sidecars."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from scanner_core.protocol import Measurement, StopPoint

META_SUFFIX = ".meta.json"
FORMAT_VERSION = 1
SCAN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ScanStatus(str, Enum):
    """How a scan ended (see the protocol's status semantics)."""

    COMPLETE = "complete"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


@dataclass
class ScanMeta:
    """Contents of a ``.meta.json`` sidecar."""

    name: str
    status: ScanStatus = ScanStatus.UNKNOWN
    started_at: str | None = None
    ended_at: str | None = None
    measurement_count: int | None = None
    source: str | None = None
    stop_point: StopPoint | None = None
    notes: str = ""
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form of the sidecar."""
        return {
            "format_version": self.format_version,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "measurement_count": self.measurement_count,
            "source": self.source,
            "stop_point": self.stop_point.to_dict() if self.stop_point else None,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScanMeta:
        """Build from a JSON dict; missing or invalid fields fall back to their defaults."""
        try:
            status = ScanStatus(data.get("status"))
        except (TypeError, ValueError):
            status = ScanStatus.UNKNOWN
        try:
            stop_point = StopPoint.from_dict(data["stop_point"])
        except (KeyError, TypeError, ValueError):
            stop_point = None
        count = data.get("measurement_count")
        version = data.get("format_version")
        return cls(
            name=_optional_str(data.get("name")) or "",
            status=status,
            started_at=_optional_str(data.get("started_at")),
            ended_at=_optional_str(data.get("ended_at")),
            measurement_count=count if type(count) is int else None,
            source=_optional_str(data.get("source")),
            stop_point=stop_point,
            notes=_optional_str(data.get("notes")) or "",
            format_version=version if type(version) is int else FORMAT_VERSION,
        )


@dataclass(frozen=True)
class ScanRecord:
    """A scan file found in a scans directory, with its metadata."""

    path: Path
    meta: ScanMeta

    @property
    def name(self) -> str:
        """Scan name (the file stem)."""
        return self.path.stem


def default_scans_dir() -> Path:
    """Return the repository's ``scans`` directory."""
    return Path(__file__).resolve().parents[3] / "scans"


def meta_path_for(scan_path: Path) -> Path:
    """Return the sidecar path for a scan file (``scans/x.json`` -> ``scans/x.meta.json``)."""
    scan_path = Path(scan_path)
    return scan_path.with_name(scan_path.stem + META_SUFFIX)


def is_valid_scan_name(name: str) -> bool:
    """Whether ``name`` may be used for a new scan file."""
    return SCAN_NAME_PATTERN.fullmatch(name) is not None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def _write_json_atomic(path: Path, data: Any) -> None:
    """Write ``data`` as indented JSON with LF newlines via a temp file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp_path, path)


def load_measurements(scan_path: Path) -> list[Measurement]:
    """Load a measurement array; raises ValueError with a clear message if it is malformed."""
    scan_path = Path(scan_path)
    data = _read_json(scan_path)
    if not isinstance(data, list):
        raise ValueError(f"{scan_path.name}: expected a JSON array of measurements")
    measurements = []
    for index, item in enumerate(data):
        try:
            measurements.append(Measurement.from_dict(item))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{scan_path.name}: invalid measurement #{index}: {exc!r}") from exc
    return measurements


def save_measurements(scan_path: Path, measurements: Sequence[Measurement]) -> None:
    """Atomically write measurements in the scan file format."""
    _write_json_atomic(Path(scan_path), [m.to_dict() for m in measurements])


def load_meta(scan_path: Path) -> ScanMeta | None:
    """Load the sidecar of ``scan_path``; None if there is none, ValueError if it is corrupt."""
    meta_path = meta_path_for(scan_path)
    if not meta_path.is_file():
        return None
    data = _read_json(meta_path)
    if not isinstance(data, dict):
        raise ValueError(f"{meta_path.name}: expected a JSON object")
    meta = ScanMeta.from_dict(data)
    meta.name = meta.name or Path(scan_path).stem
    return meta


def save_meta(scan_path: Path, meta: ScanMeta) -> None:
    """Atomically write the sidecar of ``scan_path``."""
    _write_json_atomic(meta_path_for(scan_path), meta.to_dict())


def list_scans(scans_dir: Path) -> list[ScanRecord]:
    """List the scans in ``scans_dir`` sorted by name; unreadable sidecars count as UNKNOWN."""
    scans_dir = Path(scans_dir)
    if not scans_dir.is_dir():
        return []
    records = []
    for path in scans_dir.glob("*.json"):
        if path.name.endswith(META_SUFFIX) or not path.is_file():
            continue
        try:
            meta = load_meta(path)
        except (OSError, ValueError):
            meta = None
        records.append(ScanRecord(path, meta or ScanMeta(name=path.stem)))
    return sorted(records, key=lambda record: (record.name.lower(), record.name))


def now_iso() -> str:
    """Return the local time as ISO-8601 without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()
