from __future__ import annotations

import threading

from camera_processor import telemetry


class _FlakyClient:
    """Refuses the first connects, as edgeHub does while it restarts."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.connects = 0
        self.connected = threading.Event()

    def connect(self) -> None:
        self.connects += 1
        if self.connects <= self.failures:
            raise ConnectionError("edgeHub restarting")
        self.connected.set()

    def shutdown(self) -> None:
        pass


def _client_factory(monkeypatch, client):
    class Factory:
        @staticmethod
        def create_from_edge_environment():
            return client

    monkeypatch.setattr(telemetry, "IoTHubModuleClient", Factory, raising=False)
    monkeypatch.setattr(telemetry, "_AVAILABLE", True)


def test_the_client_retries_until_edgehub_answers(monkeypatch):
    client = _FlakyClient(failures=2)
    _client_factory(monkeypatch, client)
    sender = telemetry.TelemetryClient(first_retry_s=0.01, max_retry_s=0.02)
    sender.start()
    assert client.connected.wait(2)
    assert client.connects == 3
    sender.stop()


def test_stop_ends_the_retries(monkeypatch):
    client = _FlakyClient(failures=10**6)
    _client_factory(monkeypatch, client)
    sender = telemetry.TelemetryClient(first_retry_s=0.01, max_retry_s=0.01)
    sender.start()
    sender.stop()
    attempts = client.connects
    threading.Event().wait(0.1)
    assert client.connects <= attempts + 1
