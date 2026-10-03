"""Holds one connection to the device's MJPEG stream and keeps the newest frame.

The browser reads from this relay, not from the device. When the device stalls
or restarts, this thread reconnects, and the browser's own connection stays
open. Without it a stalled device leaves a frozen picture until a page reload.
"""

from __future__ import annotations

import logging
import threading
import urllib.error
import urllib.request
from http.client import HTTPResponse

logger = logging.getLogger(__name__)


class StreamError(RuntimeError):
    """The device sent something that is not the expected multipart JPEG."""


class FrameRelay:
    def __init__(self, url: str, timeout_s: float, retry_s: float = 2.0) -> None:
        self._url = url
        self._timeout_s = timeout_s
        self._retry_s = retry_s
        self._stop = threading.Event()
        self._changed = threading.Condition()
        self._jpeg: bytes | None = None
        self._sequence = 0
        self.connected = False
        self._thread = threading.Thread(target=self._run, name="relay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._changed:
            self._changed.notify_all()

    def wait(self, after: int, timeout: float = 5.0) -> tuple[int, bytes | None]:
        """The newest frame once it is newer than `after`, or the current one on timeout."""
        with self._changed:
            self._changed.wait_for(lambda: self._sequence != after or self._stop.is_set(), timeout)
            return self._sequence, self._jpeg

    def _publish(self, jpeg: bytes) -> None:
        with self._changed:
            self._jpeg = jpeg
            self._sequence += 1
            self._changed.notify_all()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(self._url, timeout=self._timeout_s) as response:
                    self.connected = True
                    logger.info("connected to %s", self._url)
                    self._read(response)
            except (urllib.error.URLError, TimeoutError, ConnectionError, StreamError) as exc:
                if self.connected:
                    logger.warning("lost %s: %s; reconnecting", self._url, exc)
                self.connected = False
            self._stop.wait(self._retry_s)

    def _read(self, response: HTTPResponse) -> None:
        while not self._stop.is_set():
            line = response.readline()
            if not line:
                raise StreamError("the device closed the stream")
            if not line.startswith(b"--"):
                continue
            length = None
            while True:
                header = response.readline()
                if not header:
                    raise StreamError("the stream ended inside a part header")
                if header in (b"\r\n", b"\n"):
                    break
                name, _, value = header.decode("latin-1").partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            if length is None:
                raise StreamError("a part has no Content-Length")
            jpeg = response.read(length)
            if len(jpeg) != length:
                raise StreamError("the stream ended inside a frame")
            self._publish(jpeg)
