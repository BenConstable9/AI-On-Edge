"""Serves the page, the relayed stream and the device state.

The browser talks only to this server, so the page and the device data share an
origin and the device needs no cross-origin headers. The server reads one fixed
device address and nothing else.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .history import History
from .relay import FrameRelay

logger = logging.getLogger(__name__)


class _Handler(BaseHTTPRequestHandler):
    _BOUNDARY = "viewerframe"
    _HISTORY_FRAME = re.compile(r"/history/(\d+)\.jpg")

    settings: Settings
    page: bytes
    relay: FrameRelay
    history: History
    session: str
    state_ok = True

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        url = urlsplit(self.path)
        path = url.path
        frame = self._HISTORY_FRAME.fullmatch(path)
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.page)
        elif path == "/state":
            self._relay_state()
        elif path == "/stream":
            self._relay_stream()
        elif path == "/history":
            try:
                after = int(parse_qs(url.query).get("after", ["0"])[0])
            except ValueError:
                self.send_error(400)
                return
            self._send(200, "application/json", json.dumps(self.history.after(after)).encode())
        elif frame:
            jpeg = self.history.frame(int(frame.group(1)))
            if jpeg is None:
                self.send_error(404)
            else:
                self._send(200, "image/jpeg", jpeg)
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if urlsplit(self.path).path != "/history/clear":
            self.send_error(404)
            return
        # Another site cannot send this header without the browser asking first, and this server never agrees.
        if self.headers.get("X-Viewer-Session") != self.session:
            self.send_error(403)
            return
        self._send(200, "application/json", json.dumps({"last": self.history.clear()}).encode())

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        # History numbers start again when the viewer restarts; an open page uses this to start again too.
        self.send_header("X-Viewer-Session", self.session)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # The browser gave up on the request, for example on a reload.

    def _relay_state(self) -> None:
        url = f"{self.settings.device_url}/state"
        try:
            with urllib.request.urlopen(url, timeout=self.settings.timeout_s) as response:
                body = response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            self._send(502, "application/json", b'{"error": "device unreachable"}')
            if type(self).state_ok:
                logger.warning("state from %s failed: %s", url, exc)
            type(self).state_ok = False
            return
        if not type(self).state_ok:
            logger.info("state from %s is back", url)
        type(self).state_ok = True
        self._send(200, "application/json", body)

    def _relay_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={self._BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        sequence = -1
        try:
            while True:
                sequence, jpeg = self.relay.wait(sequence)
                if jpeg is None:
                    continue
                self.wfile.write(
                    f"--{self._BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # The browser closed the tab.

    def log_message(self, *args: object) -> None:
        """Silence per-request logging."""


class ViewerServer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        page = files("viewer").joinpath("static/index.html").read_bytes()
        self._relay = FrameRelay(f"{settings.device_url}/stream", settings.timeout_s)
        self._history = History(settings.device_url, settings.timeout_s)
        handler = type(
            "BoundHandler",
            (_Handler,),
            {
                "settings": settings,
                "page": page,
                "relay": self._relay,
                "history": self._history,
                "session": uuid.uuid4().hex,
            },
        )
        self._server = ThreadingHTTPServer((settings.host, settings.port), handler)
        self._server.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://{self._settings.host}:{self._settings.port}"

    def serve(self) -> None:
        self._relay.start()
        self._history.start()
        logger.info("viewer on %s, reading %s", self.url, self._settings.device_url)
        self._server.serve_forever()

    def stop(self) -> None:
        self._relay.stop()
        self._history.stop()
        threading.Thread(target=self._server.shutdown, daemon=True).start()
        self._server.server_close()
