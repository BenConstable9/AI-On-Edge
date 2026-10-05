import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from viewer.history import History


class _Device(BaseHTTPRequestHandler):
    events: list[dict] = []
    frames: dict[int, bytes] = {}
    session = "first"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/events?after="):
            after = int(self.path.split("=")[1])
            latest = max((e["seq"] for e in self.events), default=0)
            events = [e for e in self.events if e["seq"] > after]
            body = json.dumps({"session": self.session, "latest": latest, "events": events}).encode()
            content_type = "application/json"
        elif self.path.removeprefix("/events/").removesuffix(".jpg").isdigit():
            seq = int(self.path.removeprefix("/events/").removesuffix(".jpg"))
            if seq not in self.frames:
                self.send_error(404)
                return
            body, content_type = self.frames[seq], "image/jpeg"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _event(seq: int, label: str = "car") -> dict:
    return {
        "seq": seq,
        "at": 0.0,
        "kind": "seen",
        "track_id": seq,
        "label": label,
        "detector_label": label,
        "description": "",
    }


@pytest.fixture()
def device():
    _Device.events, _Device.frames, _Device.session = [], {}, "first"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Device)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_copies_new_events_and_their_frames(device):
    history = History(device, timeout_s=2)
    _Device.events = [_event(1), _event(2, "bus")]
    _Device.frames = {1: b"one"}
    history.poll()
    history.poll()
    assert [(e["id"], e["label"], e["frame"]) for e in history.after(0)] == [(1, "car", True), (2, "bus", False)]
    assert history.frame(1) == b"one"
    assert history.frame(2) is None

    _Device.events.append(_event(3))
    history.poll()
    assert [e["id"] for e in history.after(2)] == [3]


def test_a_device_restart_starts_the_history_again_and_numbers_the_new_events_after_the_old(device):
    history = History(device, timeout_s=2)
    _Device.events = [_event(1), _event(2)]
    _Device.frames = {1: b"one"}
    history.poll()
    _Device.events, _Device.frames, _Device.session = [_event(1, "dog")], {}, "second"
    history.poll()
    history.poll()
    assert [(e["id"], e["label"], e["session"]) for e in history.after(0)] == [(3, "dog", "second")]
    assert history.frame(1) is None


def test_a_restart_is_seen_even_when_the_device_has_more_events_than_before(device):
    history = History(device, timeout_s=2)
    _Device.events = [_event(1)]
    history.poll()
    _Device.events, _Device.session = [_event(n, "bus") for n in (1, 2, 3)], "second"
    history.poll()
    history.poll()
    assert [e["label"] for e in history.after(0)] == ["bus", "bus", "bus"]


def test_keeps_only_the_newest_events(device):
    history = History(device, timeout_s=2, capacity=2)
    _Device.events = [_event(n) for n in (1, 2, 3)]
    history.poll()
    assert [e["id"] for e in history.after(0)] == [2, 3]
    assert history.frame(1) is None


def test_clear_drops_the_history_and_the_device_events_do_not_come_back(device):
    history = History(device, timeout_s=2)
    _Device.events = [_event(1), _event(2)]
    _Device.frames = {1: b"one"}
    history.poll()
    assert history.clear() == 2
    history.poll()
    assert history.after(0) == [] and history.frame(1) is None

    _Device.events.append(_event(3, "bus"))
    history.poll()
    assert [(e["id"], e["label"]) for e in history.after(0)] == [(3, "bus")]
