"""Scanner I/O off the GUI thread: a ``LinkWorker`` in a ``QThread`` run by ``LinkConnection``."""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from scanner_core.links import ScannerLink

READ_TIMEOUT_S = 0.1
THREAD_STOP_TIMEOUT_MS = 5000


class LinkWorker(QObject):
    """Opens a ``ScannerLink``, emits every received line and writes queued commands.

    Runs in a worker thread and never touches widgets: it communicates only through its
    signals and the thread-safe ``send`` / ``request_stop`` methods. ``closed`` is always
    emitted last, also after a failed open.
    """

    opened = Signal()
    closed = Signal()
    error = Signal(str)
    line_received = Signal(str)

    def __init__(self, link: ScannerLink) -> None:
        super().__init__()
        self._link = link
        self._outgoing: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._stop_requested = threading.Event()

    def send(self, text: str) -> None:
        """Queue a line to be written by the worker loop (thread-safe)."""
        self._outgoing.put(text)

    def request_stop(self) -> None:
        """Ask the worker loop to close the link and return (thread-safe)."""
        self._stop_requested.set()

    @Slot()
    def run(self) -> None:
        """Open the link, then read and write lines until stopped or the link fails."""
        try:
            self._link.open()
            self.opened.emit()
            while not self._stop_requested.is_set():
                self._write_pending()
                line = self._link.read_line(READ_TIMEOUT_S)
                if line is not None:
                    self.line_received.emit(line)
        except OSError as exc:  # ConnectionError from the link
            if not self._stop_requested.is_set():
                self.error.emit(str(exc))
        finally:
            self._link.close()
            self.closed.emit()

    def _write_pending(self) -> None:
        """Write all queued lines."""
        while True:
            try:
                text = self._outgoing.get_nowait()
            except queue.Empty:
                return
            self._link.write_line(text)


class LinkConnection(QObject):
    """One scanner connection: a ``LinkWorker`` in its own ``QThread``.

    Lives in the GUI thread and re-emits the worker's signals there. After ``close()`` no
    further signals are emitted, even for lines still queued from the worker.
    """

    opened = Signal()
    closed = Signal()
    error = Signal(str)
    line_received = Signal(str)

    def __init__(self, link: ScannerLink) -> None:
        super().__init__()
        self._description = link.description
        self._active = True
        self._thread = QThread()
        self._worker = LinkWorker(link)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # quit() is thread-safe; a direct call ends the thread as soon as the loop returns.
        self._worker.closed.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        # Receivers live in the GUI thread, so these calls are queued there.
        self._worker.opened.connect(self._forward_opened)
        self._worker.closed.connect(self._forward_closed)
        self._worker.error.connect(self._forward_error)
        self._worker.line_received.connect(self._forward_line)

    @property
    def description(self) -> str:
        """Description of the underlying link (e.g. ``COM5``)."""
        return self._description

    def start(self) -> None:
        """Start the worker thread (the link is opened there)."""
        self._thread.start()

    def send(self, text: str) -> None:
        """Send a line to the scanner (non-blocking)."""
        self._worker.send(text)

    def close(self) -> bool:
        """Stop forwarding signals, stop the worker and wait for its thread; True if it finished."""
        self._active = False
        self._worker.request_stop()
        return self._thread.wait(THREAD_STOP_TIMEOUT_MS)

    @Slot()
    def _forward_opened(self) -> None:
        if self._active:
            self.opened.emit()

    @Slot()
    def _forward_closed(self) -> None:
        if self._active:
            self.closed.emit()

    @Slot(str)
    def _forward_error(self, message: str) -> None:
        if self._active:
            self.error.emit(message)

    @Slot(str)
    def _forward_line(self, line: str) -> None:
        if self._active:
            self.line_received.emit(line)
