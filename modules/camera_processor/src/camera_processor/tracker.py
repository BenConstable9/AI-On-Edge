"""ByteTrack from Roboflow's `trackers`, one tracker for each class.

ByteTrack (Zhang et al., ECCV 2022) gives each object a stable id across frames.
The library matches boxes by overlap only, whatever their class: a person who
walked out past a couch handed their id to the couch. So each class gets its own
tracker, as BoxMOT's per_class option does, and an id never changes class.

The detector also flickers: a car is a "truck" for a few frames, in the same
box. With a tracker for each class, that would be a second object. So a box
that overlaps an object matched in the last frame by IoU 0.7 or more takes that
object's class first. It is the detector's own rule for one object: two boxes
at IoU 0.7 in one frame are one object, and duplicate removal keeps one. An id
that moves to another object moves to another box.

The module also needs ids unique across classes, and the confirmed tracks that
the last update forgot, to report objects that left.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import supervision as sv
from trackers import ByteTrackTracker


@dataclass
class Track:
    track_id: int
    class_id: int
    box: np.ndarray  # the matched detection, in frame pixels
    score: float  # the matched detection's score


class Tracker:
    _SAME_OBJECT_IOU = 0.7  # as Detector._SAME_BOX_IOU

    def __init__(self, frame_rate: float, *, track_activation_threshold: float = 0.7) -> None:
        """Every other setting is the library's published default."""
        self._frame_rate = frame_rate
        self._track_activation_threshold = track_activation_threshold
        self._byte_tracks: dict[int, ByteTrackTracker] = {}
        self._tracks: dict[tuple[int, int], Track] = {}  # by class and the class tracker's own id
        self._next_id = 0
        self._last: list[Track] = []  # the tracks matched in the last frame
        self.dropped: list[Track] = []  # confirmed tracks forgotten by the last update

    def update(self, detections: sv.Detections) -> list[Track]:
        """Match this frame's detections. Returns the confirmed tracks matched in this frame."""
        detections = self._keep_classes(detections)
        matched: list[Track] = []
        alive: set[tuple[int, int]] = set()
        # Every class tracker sees every frame, so the tracks of a class that is not in this one still age.
        for class_id in set(self._byte_tracks) | {int(c) for c in detections.class_id}:
            byte_track = self._byte_tracks.get(class_id)
            if byte_track is None:
                byte_track = self._byte_tracks[class_id] = ByteTrackTracker(
                    frame_rate=self._frame_rate, track_activation_threshold=self._track_activation_threshold
                )
            out = byte_track.update(detections[detections.class_id == class_id])
            for i in range(len(out)):
                if out.tracker_id[i] < 0:
                    continue
                key = (class_id, int(out.tracker_id[i]))
                track = self._tracks.get(key)
                if track is None:
                    track = self._tracks[key] = Track(self._next_id, class_id, out.xyxy[i], 0.0)
                    self._next_id += 1
                track.box = out.xyxy[i].astype(np.float32)
                track.score = float(out.confidence[i])
                matched.append(track)
            alive.update((class_id, t.tracker_id) for t in byte_track.tracks)
            if not byte_track.tracks:
                del self._byte_tracks[class_id]
        self.dropped = [self._tracks.pop(key) for key in list(self._tracks) if key not in alive]
        self._last = matched
        return matched

    def _keep_classes(self, detections: sv.Detections) -> sv.Detections:
        """A copy in which each box on an object matched in the last frame has that object's class."""
        if not self._last or len(detections) == 0:
            return detections
        overlap = sv.box_iou_batch(detections.xyxy, np.array([t.box for t in self._last]))
        best = overlap.argmax(axis=1)
        same = overlap[np.arange(len(detections)), best] >= self._SAME_OBJECT_IOU
        class_id = np.where(same, np.array([t.class_id for t in self._last])[best], detections.class_id)
        return sv.Detections(xyxy=detections.xyxy, confidence=detections.confidence, class_id=class_id)
