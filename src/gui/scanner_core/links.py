"""Line-based scanner connections: the abstract ``ScannerLink`` and the real ``SerialLink``."""

from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from types import TracebackType

import serial
from serial.tools import list_ports

from scanner_core.protocol import BAUD_RATE


class ScannerLink(ABC):
    """Line-based, bidirectional connection to a scanner (real or simulated).

    ``write_line`` may be called from another thread while ``read_line`` blocks in a reader thread.
    Usable as a context manager (opens on enter, closes on exit).
    """

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable source name, e.g. ``"COM5"`` or ``"simulator:pear_real.json"``."""

    @abstractmethod
    def open(self) -> None:
        """Open the connection; raises ConnectionError on failure."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection (idempotent)."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the connection is open."""

    @abstractmethod
    def write_line(self, text: str) -> None:
        """Send ``text`` followed by a newline; raises ConnectionError on failure."""

    @abstractmethod
    def read_line(self, timeout: float) -> str | None:
        """Return the next non-blank, stripped line, or None if none arrived within ``timeout`` s.

        Lines are decoded as UTF-8 (invalid bytes replaced). Raises ConnectionError if the link
        is closed or dropped.
        """

    def __enter__(self) -> ScannerLink:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class SerialLink(ScannerLink):
    """Scanner connected over a serial port (pyserial)."""

    _POLL_TIMEOUT_S = 0.05
    _WRITE_TIMEOUT_S = 2.0

    def __init__(self, port: str, baud: int = BAUD_RATE) -> None:
        self._port = port
        self._baud = baud
        self._serial: serial.Serial | None = None
        self._buffer = bytearray()
        self._write_lock = threading.Lock()

    @property
    def description(self) -> str:
        return self._port

    def open(self) -> None:
        if self.is_open:
            return
        try:
            self._serial = serial.Serial(
                self._port,
                self._baud,
                timeout=self._POLL_TIMEOUT_S,
                write_timeout=self._WRITE_TIMEOUT_S,
            )
        except (serial.SerialException, ValueError) as exc:
            raise ConnectionError(f"cannot open serial port {self._port}: {exc}") from exc
        self._buffer.clear()

    def close(self) -> None:
        port, self._serial = self._serial, None
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError):
                pass

    @property
    def is_open(self) -> bool:
        port = self._serial
        return port is not None and port.is_open

    def write_line(self, text: str) -> None:
        port = self._require_open()
        try:
            with self._write_lock:
                port.write((text + "\n").encode("utf-8"))
        except (serial.SerialException, OSError) as exc:
            raise ConnectionError(f"write to {self._port} failed: {exc}") from exc

    def read_line(self, timeout: float) -> str | None:
        port = self._require_open()
        deadline = time.monotonic() + timeout
        while True:
            line = self._pop_line()
            if line is not None:
                return line
            try:
                # Short pyserial timeout: keeps Ctrl-C and close() responsive.
                self._buffer += port.read(max(1, port.in_waiting))
            except (serial.SerialException, OSError, TypeError, AttributeError) as exc:
                # TypeError/AttributeError: pyserial internals after a concurrent close().
                raise ConnectionError(f"read from {self._port} failed: {exc}") from exc
            if time.monotonic() >= deadline:
                return self._pop_line()

    def _require_open(self) -> serial.Serial:
        port = self._serial
        if port is None or not port.is_open:
            raise ConnectionError(f"serial port {self._port} is not open")
        return port

    def _pop_line(self) -> str | None:
        """Remove and return the next non-blank complete line from the receive buffer."""
        while (end := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                return line
        return None


def _natural_key(text: str) -> list[int | str]:
    """Sort key that orders embedded numbers numerically (COM3 before COM10)."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def list_serial_ports() -> list[tuple[str, str]]:
    """Return the available serial ports as ``(device, description)``, naturally sorted by device."""
    ports = ((port.device, port.description) for port in list_ports.comports())
    return sorted(ports, key=lambda port: _natural_key(port[0]))
