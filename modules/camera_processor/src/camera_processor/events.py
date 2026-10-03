"""Recent detection events, each with the preview frame from that moment.

Held in memory only, and bounded. The frames are the pixelated preview, the
same picture the stream shows, so nothing here is more private than /stream.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Event:
    seq: int
    at: float
    kind: str  # "seen": a new object; "verdict": the VLM named and described it; "gone": it left undescribed
    track_id: int
    label: str
    detector_label: str
    description: str  # for "gone", why: small, edge, busy, left or loading
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 as fractions of the frame


class EventLog:
    def __init__(self, capacity: int = 100) -> None:
        self._lock = threading.Lock()
        self._events: deque[tuple[Event, bytes | None]] = deque(maxlen=capacity)
        self._seq = 0
        # Sequence numbers start again at each module start; this tells a reader.
        self.session = uuid.uuid4().hex

    def add(
        self,
        kind: str,
        track_id: int,
        label: str,
        detector_label: str,
        description: str,
        jpeg: bytes | None,
        box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    ) -> None:
        with self._lock:
            self._seq += 1
            event = Event(self._seq, time.time(), kind, track_id, label, detector_label, description, box)
            self._events.append((event, jpeg))

    def after(self, seq: int) -> list[dict[str, object]]:
        """Events newer than `seq`, oldest first."""
        with self._lock:
            return [asdict(event) for event, _ in self._events if event.seq > seq]

    @property
    def latest(self) -> int:
        """The newest sequence number. It drops to 0 when the module restarts."""
        with self._lock:
            return self._seq

    def frame(self, seq: int) -> bytes | None:
        with self._lock:
            for event, jpeg in self._events:
                if event.seq == seq:
                    return jpeg
        return None
