"""Picks the best view of each object for the VLM, and which object goes next.

A track is seen many times before the VLM is free. The first view is often the
worst: small, at the frame edge, or behind another object. So every frame each
waiting track's view is scored, and the best view so far is kept. When the VLM
is free it gets the track that has waited longest, with that track's best view,
once the view is ready: a whole view after `collect_s`, or the best part view
after `max_wait_s`. This is the "best shot" step of face and vehicle
recognition pipelines.

An object that leaves before its turn keeps its best view, which is now final,
and its preview is kept as a JPEG of the event frame, not as a raw picture.

When the VLM is free it takes the ready view with the lowest key (left the view,
first seen): objects in view first, then objects that left, and within each the
one that has waited longest.

Score, higher is better, compared as a tuple in this order:
1. In full view: not cut by the frame edge. A whole object beats any part.
2. Visible pixels: box area times the share not covered by other boxes,
   capped at the area the crop keeps (crop_px squared).
3. Detector score.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

import cv2
import numpy as np


@dataclass
class Shot:
    track_id: int
    label: str  # the detector's label at that moment
    crop: np.ndarray  # BGR, unpixelated: it goes to the VLM only
    box: np.ndarray  # frame pixels
    canvas: np.ndarray | None  # the pixelated, labelled preview of that moment; None once the object left
    scale: float  # preview pixels per frame pixel
    score: tuple[bool, float, float]
    first_seen: float
    frame: bytes | None = None  # the event frame as a JPEG, once the object left

    @property
    def departed(self) -> bool:
        return self.frame is not None


class BestShots:
    # A box this close to the frame edge is taken to be cut by it.
    _EDGE_PX = 4.0

    def __init__(
        self,
        min_box_px: int = 64,
        crop_px: int = 448,
        collect_s: float = 1.0,
        max_wait_s: float = 2.0,
        max_departed: int = 100,
    ) -> None:
        """crop_px: the longest side of a crop for the VLM.
        collect_s: how long to look for a better view of an object in full view.
        max_wait_s: how long to wait for an object at the edge to come into full view.
        max_departed: the most objects that left and still wait. The device's event
        log holds 100 events, so an answer for an older object reaches no new viewer."""
        self._min_box_px = min_box_px
        self._crop_px = crop_px
        self._collect_s = collect_s
        self._max_wait_s = max_wait_s
        self._max_departed = max_departed
        self._lock = threading.Lock()
        self._shots: dict[int, Shot] = {}

    def big_enough(self, box: np.ndarray) -> bool:
        return min(float(box[2] - box[0]), float(box[3] - box[1])) >= self._min_box_px

    def score(
        self, box: np.ndarray, others: np.ndarray, detector_score: float, width: int, height: int
    ) -> tuple[bool, float, float]:
        """The view's score; see the module docstring."""
        x1, y1, x2, y2 = (float(v) for v in box)
        edge = self._EDGE_PX
        whole = x1 > edge and y1 > edge and x2 < width - edge and y2 < height - edge
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        covered = 0.0
        if len(others) and area > 0:
            ix = np.clip(np.minimum(others[:, 2], x2) - np.maximum(others[:, 0], x1), 0, None)
            iy = np.clip(np.minimum(others[:, 3], y2) - np.maximum(others[:, 1], y1), 0, None)
            # The union of overlaps can exceed the box when others overlap each other; cap at 1.
            covered = min(1.0, float((ix * iy).sum()) / area)
        visible = min(area * (1.0 - covered), float(self._crop_px * self._crop_px))
        return whole, visible, float(detector_score)

    def crop(self, image: np.ndarray, box: np.ndarray, pad: float = 0.1) -> np.ndarray:
        """The box with a margin, scaled so its longer side is at most crop_px pixels.

        The VLM's cost grows with pixel count, so a bounded crop keeps each answer
        near a second whatever the size of the vehicle in frame.
        """
        height, width = image.shape[:2]
        x1, y1, x2, y2 = box
        mx, my = (x2 - x1) * pad, (y2 - y1) * pad
        x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
        x2, y2 = int(min(width, x2 + mx)), int(min(height, y2 + my))
        crop = image[y1:y2, x1:x2]
        scale = self._crop_px / max(crop.shape[:2])
        if scale < 1:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(crop)

    def better(self, track_id: int, score: tuple[bool, float, float]) -> bool:
        """True when this view beats the one kept, so the caller should build a Shot."""
        with self._lock:
            kept = self._shots.get(track_id)
            return kept is None or (not kept.departed and score > kept.score)

    def keep(self, shot: Shot) -> None:
        with self._lock:
            kept = self._shots.get(shot.track_id)
            if kept is not None:
                shot.first_seen = kept.first_seen
            self._shots[shot.track_id] = shot

    def next(self, now: float | None = None) -> Shot | None:
        """The next shot for the VLM, removed from the pool; see the module docstring."""
        now = time.monotonic() if now is None else now
        with self._lock:
            ready = [
                s
                for s in self._shots.values()
                if s.departed or now - s.first_seen >= (self._collect_s if s.score[0] else self._max_wait_s)
            ]
            if not ready:
                return None
            shot = min(ready, key=lambda s: (s.departed, s.first_seen))
            del self._shots[shot.track_id]
            return shot

    def depart(self, track_id: int, frame: bytes) -> Shot | None:
        """The object left: keep its best view with its event frame. Returns the
        departed shot that waited longest if there are too many and it had to go."""
        with self._lock:
            shot = self._shots.get(track_id)
            if shot is None or shot.departed:
                return None
            self._shots[track_id] = replace(shot, canvas=None, frame=frame)
            departed = [s for s in self._shots.values() if s.departed]
            if len(departed) <= self._max_departed:
                return None
            oldest = min(departed, key=lambda s: s.first_seen)
            del self._shots[oldest.track_id]
            return oldest

    def put_back(self, shot: Shot) -> None:
        """Return a shot whose VLM call failed, so it is asked again."""
        with self._lock:
            self._shots.setdefault(shot.track_id, shot)

    def view(self, track_id: int) -> Shot | None:
        with self._lock:
            return self._shots.get(track_id)

    def departed(self) -> list[int]:
        """The objects that left and wait for the VLM."""
        with self._lock:
            return [s.track_id for s in self._shots.values() if s.departed]
