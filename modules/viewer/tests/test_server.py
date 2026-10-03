import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from viewer.config import Settings
from viewer.server import ViewerServer


@pytest.fixture()
def viewer_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Nothing listens on the device address; the relay and the history only log that.
    server = ViewerServer(Settings(device_url="http://127.0.0.1:9", host="127.0.0.1", port=port, timeout_s=0.2))
    threading.Thread(target=server.serve, daemon=True).start()
    yield server.url
    server.stop()


def _post(url, headers):
    request = urllib.request.Request(url, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status, json.load(response)


def test_clearing_the_history_needs_the_viewer_session_header(viewer_url):
    with urllib.request.urlopen(viewer_url, timeout=2) as response:
        session = response.headers["X-Viewer-Session"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(f"{viewer_url}/history/clear", {})
    assert refused.value.code == 403
    assert _post(f"{viewer_url}/history/clear", {"X-Viewer-Session": session}) == (200, {"last": 0})
