"""Keeps every detection event the device reports while the viewer runs.

The device holds only its last 100 events, so this thread copies each one, with
its frame, as it appears. The history is in memory only and goes when the viewer
stops. A device restart starts a new session: its counts start again at zero, so
the history starts again too. Events get the viewer's own numbers, which never
restart, so a page that asks for the events after a number gets only new ones.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


class History:
    def __init__(self, device_url: str, timeout_s: float, poll_s: float = 1.0, capacity: int = 1000) -> None:
        self._device_url = device_url
        self._timeout_s = timeout_s
        self._poll_s = poll_s
        self._capacity = capacity
        self._lock = threading.Lock()
        self._events: OrderedDict[int, tuple[dict[str, Any], bytes | None]] = OrderedDict()
        self._next = 1
        self._device_seq = 0
        self._device_session: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="history", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def after(self, number: int) -> list[dict[str, Any]]:
        """Events newer than `number`, oldest first."""
        with self._lock:
            return [event for n, (event, _) in self._events.items() if n > number]

    def frame(self, number: int) -> bytes | None:
        with self._lock:
            entry = self._events.get(number)
        return entry[1] if entry else None

    def clear(self) -> int:
        """Drop every event so far. Returns the last number dropped.

        The events the device still holds are not copied again: only new ones come.
        """
        with self._lock:
            self._events.clear()
            return self._next - 1

    def poll(self) -> None:
        """Copy the events the device has added since the last call."""
        with urllib.request.urlopen(
            f"{self._device_url}/events?after={self._device_seq}", timeout=self._timeout_s
        ) as response:
            body = json.load(response)
        if body["session"] != self._device_session:
            if self._device_session is not None:
                logger.info("the device restarted; starting the history again")
            with self._lock:
                self._events.clear()
            self._device_session = body["session"]
            if self._device_seq:
                self._device_seq = 0
                return
        for event in body["events"]:
            self._add(event, self._fetch_frame(event["seq"]))
            self._device_seq = event["seq"]

    def _fetch_frame(self, seq: int) -> bytes | None:
        try:
            with urllib.request.urlopen(f"{self._device_url}/events/{seq}.jpg", timeout=self._timeout_s) as response:
                return response.read()
        except urllib.error.HTTPError:
            return None  # The device dropped it before we asked.

    def _add(self, event: dict[str, Any], jpeg: bytes | None) -> None:
        with self._lock:
            number = self._next
            self._next += 1
            self._events[number] = (
                {**event, "id": number, "session": self._device_session, "frame": jpeg is not None},
                jpeg,
            )
            while len(self._events) > self._capacity:
                self._events.popitem(last=False)

    def _run(self) -> None:
        reachable = True
        while not self._stop.is_set():
            try:
                self.poll()
                if not reachable:
                    logger.info("events from %s are back", self._device_url)
                reachable = True
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError) as exc:
                if reachable:
                    logger.warning("events from %s failed: %s", self._device_url, exc)
                reachable = False
            self._stop.wait(self._poll_s)
