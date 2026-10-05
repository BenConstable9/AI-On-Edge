from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from camera_processor.events import EventLog
from camera_processor.mjpeg import MjpegServer


def test_the_log_returns_events_after_a_sequence_and_keeps_only_the_newest():
    log = EventLog(capacity=3)
    for n in range(5):
        log.add("seen", n, "car", "car", "", f"frame{n}".encode())
    assert log.latest == 5
    assert [e["seq"] for e in log.after(0)] == [3, 4, 5]
    assert [e["track_id"] for e in log.after(4)] == [4]
    assert log.frame(5) == b"frame4"
    assert log.frame(1) is None


def test_an_answer_for_an_object_that_left_has_no_frame():
    log = EventLog()
    log.add("verdict", 3, "van", "truck", "white van", None)
    assert log.after(0)[0]["description"] == "white van"
    assert log.frame(1) is None


@pytest.fixture()
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log = EventLog()
    log.add("verdict", 7, "shoe", "chair", "white trainer", b"\xff\xd8jpeg", (0.1, 0.2, 0.3, 0.4))
    mjpeg = MjpegServer(port, log)
    mjpeg.start()
    yield f"http://127.0.0.1:{port}"
    mjpeg.stop()


def test_the_server_lists_events_and_serves_their_frames(server):
    with urllib.request.urlopen(f"{server}/events?after=0", timeout=5) as response:
        body = json.load(response)
    assert body["latest"] == 1 and len(body["session"]) == 32
    assert body["events"][0] | {"at": 0} == {
        "seq": 1,
        "at": 0,
        "kind": "verdict",
        "track_id": 7,
        "label": "shoe",
        "detector_label": "chair",
        "description": "white trainer",
        "box": [0.1, 0.2, 0.3, 0.4],
    }
    with urllib.request.urlopen(f"{server}/events/1.jpg", timeout=5) as response:
        assert (response.headers["Content-Type"], response.read()) == ("image/jpeg", b"\xff\xd8jpeg")


@pytest.mark.parametrize("path", ["/events/2.jpg", "/events?after=x", "/events/../state"])
def test_the_server_refuses_unknown_events(server, path):
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{server}{path}", timeout=5)
