"""Preview on the local network: the MJPEG stream and the pipeline state.

The only place the picture leaves the module. It goes to a browser or the viewer
on the same network as the device, never to the cloud.

  /stream            annotated frames, multipart JPEG
  /state             counts, timings and VLM answers, JSON
  /events?after=N    recent detection events newer than N, JSON
  /events/N.jpg      the preview frame of event N
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .events import EventLog

logger = logging.getLogger(__name__)


class _Latest:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._updated = threading.Condition(self._lock)

    def set(self, jpeg: bytes) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._updated.notify_all()

    def wait(self, timeout: float = 5.0) -> bytes | None:
        with self._lock:
            self._updated.wait(timeout)
            return self._jpeg


class _State:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._body = b"{}"

    def set(self, state: dict[str, Any]) -> None:
        body = json.dumps(state).encode()
        with self._lock:
            self._body = body

    def json(self) -> bytes:
        with self._lock:
            return self._body


class _Handler(BaseHTTPRequestHandler):
    _BOUNDARY = "frameboundary"
    _EVENT_FRAME = re.compile(r"/events/(\d+)\.jpg")

    latest: _Latest
    state: _State
    events: EventLog

    def _send(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The client gave up on the request, for example the viewer on a restart.

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        url = urlsplit(self.path)
        if url.path == "/state":
            self._send("application/json", self.state.json())
            return
        if url.path == "/events":
            try:
                after = int(parse_qs(url.query).get("after", ["0"])[0])
            except ValueError:
                self.send_error(400)
                return
            body = {"session": self.events.session, "latest": self.events.latest, "events": self.events.after(after)}
            self._send("application/json", json.dumps(body).encode())
            return
        match = self._EVENT_FRAME.fullmatch(url.path)
        if match:
            jpeg = self.events.frame(int(match.group(1)))
            if jpeg is None:
                self.send_error(404)
            else:
                self._send("image/jpeg", jpeg)
            return
        if url.path not in ("/", "/stream"):
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={self._BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            while True:
                jpeg = self.latest.wait()
                if jpeg is None:
                    continue
                self.wfile.write(f"--{self._BOUNDARY}\r\n".encode())
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args: object) -> None:
        """Silence per-request logging — it drowns the pipeline output."""


class MjpegServer:
    def __init__(self, port: int, events: EventLog) -> None:
        self._port = port
        self._latest = _Latest()
        self._state = _State()
        self._events = events
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        handler = type(
            "BoundHandler", (_Handler,), {"latest": self._latest, "state": self._state, "events": self._events}
        )
        self._server = ThreadingHTTPServer(("0.0.0.0", self._port), handler)
        threading.Thread(target=self._server.serve_forever, name="mjpeg", daemon=True).start()
        logger.info("preview on http://0.0.0.0:%d/stream and /state", self._port)

    def update(self, jpeg: bytes) -> None:
        self._latest.set(jpeg)

    def update_state(self, state: dict[str, Any]) -> None:
        self._state.set(state)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
