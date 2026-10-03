"""IoT Hub module client for telemetry.

Degrades to a no-op when the IoT Edge runtime is absent, so the pipeline can be
run and debugged on a laptop with nothing but a webcam.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

try:
    from azure.iot.device import IoTHubModuleClient, Message

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False


class TelemetryClient:
    def __init__(self, first_retry_s: float = 5.0, max_retry_s: float = 60.0) -> None:
        self._client = None
        self._enabled = False
        self._failing = False
        self._stop = threading.Event()
        self._first_retry_s = first_retry_s
        self._max_retry_s = max_retry_s

    def start(self) -> None:
        """Connect in the background. Detection does not wait for IoT Hub."""
        if not _AVAILABLE:
            logger.info("azure-iot-device not installed — telemetry disabled")
            return
        threading.Thread(target=self._connect, name="iot-connect", daemon=True).start()

    def _connect(self) -> None:
        try:
            client = IoTHubModuleClient.create_from_edge_environment()
        except Exception as exc:
            logger.warning("no IoT Edge environment (%s) — telemetry disabled", exc)
            return
        # edgeHub can start after this module, or restart during an update.
        delay = self._first_retry_s
        while True:
            try:
                client.connect()
                break
            except Exception as exc:
                logger.warning("edgeHub not reachable (%s); retrying in %.0f s", type(exc).__name__, delay)
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, self._max_retry_s)
        self._client = client
        self._enabled = True
        logger.info("IoT Hub module client connected")

    def send_telemetry(self, payload: dict[str, Any]) -> None:
        if not self._enabled or self._client is None:
            return

        # Fire and forget: telemetry must never delay the inference loop.
        def _send() -> None:
            try:
                message = Message(json.dumps(payload))
                message.content_encoding = "utf-8"
                message.content_type = "application/json"
                self._client.send_message_to_output(message, "telemetry")
            except Exception as exc:
                # One line per outage: edgeHub restarts at each IoT Edge update.
                if not self._failing:
                    logger.warning("telemetry not sent (%s); the client reconnects by itself", type(exc).__name__)
                self._failing = True
                return
            if self._failing:
                logger.info("telemetry sent again")
            self._failing = False

        threading.Thread(target=_send, name="telemetry", daemon=True).start()

    def stop(self) -> None:
        """Disconnect, but never hold up a restart: Docker kills the module after 10 s."""
        self._stop.set()
        client = self._client
        if client is None:
            return
        closer = threading.Thread(target=client.shutdown, name="iot-shutdown", daemon=True)
        closer.start()
        closer.join(timeout=3)
        if closer.is_alive():
            logger.warning("IoT client shutdown did not finish in 3 s; exiting anyway")
