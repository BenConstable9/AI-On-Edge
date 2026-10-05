import numpy as np

from camera_processor.counting import Census
from camera_processor.tracker import Track

NAMES = {3: "car", 6: "bus"}


def _track(track_id, cx, cy, class_id=3):
    box = np.array([cx - 5, cy - 5, cx + 5, cy + 5], np.float32)
    return Track(track_id, class_id, box, 0.9)


def _labels(tracks):
    return {t.track_id: NAMES[t.class_id] for t in tracks}


def _update(counter, tracks):
    return counter.update(tracks, _labels(tracks))


def test_census_counts_in_view_now_and_each_track_seen_once():
    census = Census()
    _update(census, [_track(1, 10, 10), _track(2, 50, 50), _track(3, 80, 80, class_id=6)])
    _update(census, [_track(1, 12, 10), _track(4, 30, 30)])
    assert census.snapshot() == {"in_view": {"car": 2}, "seen": {"car": 3, "bus": 1}}
    _update(census, [])
    assert census.snapshot()["in_view"] == {}


def test_census_moves_a_relabelled_track_to_its_new_class():
    census = Census()
    track = _track(22, 10, 10)
    census.update([track], {22: "chair"})
    census.update([track], {22: "shoe"})
    assert census.snapshot() == {"in_view": {"shoe": 1}, "seen": {"shoe": 1}}
