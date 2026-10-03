"""Counts tracked objects: those in view now, and every object that has appeared."""

from __future__ import annotations

from collections import defaultdict

from .tracker import Track


class Census:
    """Objects in view now, and distinct objects seen since start, by class.

    A track is counted as seen once. If its label changes later, because the VLM
    corrected the detector, its count moves to the new label.
    """

    def __init__(self) -> None:
        self.in_view: dict[str, int] = {}
        self.seen: dict[str, int] = defaultdict(int)
        self._label_of: dict[int, str] = {}

    def update(self, tracks: list[Track], labels: dict[int, str]) -> None:
        """labels: the current label of each track, by track id."""
        in_view: dict[str, int] = defaultdict(int)
        for track in tracks:
            label = labels[track.track_id]
            in_view[label] += 1
            previous = self._label_of.get(track.track_id)
            if previous == label:
                continue
            if previous is not None:
                self.seen[previous] -= 1
                if self.seen[previous] <= 0:
                    del self.seen[previous]
            self.seen[label] += 1
            self._label_of[track.track_id] = label
        self.in_view = dict(in_view)
        if len(self._label_of) > 10_000:
            self._label_of = {t.track_id: labels[t.track_id] for t in tracks}

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {"in_view": dict(self.in_view), "seen": dict(self.seen)}
