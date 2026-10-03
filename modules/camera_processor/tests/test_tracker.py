import numpy as np
import supervision as sv

from camera_processor.tracker import Tracker

CAR, TRUCK, PERSON, COUCH = 3, 8, 1, 63


def _frame(tracker, boxes, class_ids=None, scores=None):
    boxes = np.array(boxes, np.float32).reshape(-1, 4)
    class_ids = np.array(class_ids if class_ids is not None else [CAR] * len(boxes), np.int64)
    scores = np.array(scores if scores is not None else [0.9] * len(boxes), np.float32)
    return tracker.update(sv.Detections(xyxy=boxes, confidence=scores, class_id=class_ids))


def test_a_track_is_confirmed_on_its_second_frame():
    tracker = Tracker(frame_rate=30)
    assert _frame(tracker, [[0, 0, 10, 10]]) == []
    assert [t.track_id for t in _frame(tracker, [[1, 0, 11, 10]])] == [0]


def test_a_detection_below_the_activation_threshold_starts_nothing():
    tracker = Tracker(frame_rate=30, track_activation_threshold=0.7)
    for _ in range(5):
        assert _frame(tracker, [[0, 0, 40, 40]], scores=[0.65]) == []


def test_a_track_that_stays_lost_is_reported_dropped():
    tracker = Tracker(frame_rate=12)  # the library keeps a lost track for ceil(12 / 30 * 30) = 12 frames
    _frame(tracker, [[0, 0, 20, 20]])
    _frame(tracker, [[0, 0, 20, 20]])
    for _ in range(12):
        _frame(tracker, [])
    assert tracker.dropped == []
    _frame(tracker, [])
    assert [t.track_id for t in tracker.dropped] == [0]


def test_a_one_frame_flicker_of_class_starts_nothing():
    tracker = Tracker(frame_rate=30)
    seen = set()
    for class_id in (CAR, CAR, TRUCK, CAR, TRUCK, CAR):
        seen.update((t.track_id, t.class_id) for t in _frame(tracker, [[0, 0, 40, 40]], class_ids=[class_id]))
    assert seen == {(0, CAR)}


def test_an_id_never_moves_to_an_object_of_another_class():
    # A person who walks out past a couch must not hand their id to the couch arm the person hid.
    tracker = Tracker(frame_rate=10)
    _frame(tracker, [[0, 0, 40, 100]], class_ids=[PERSON])
    (person,) = _frame(tracker, [[0, 0, 40, 100]], class_ids=[PERSON])
    _frame(tracker, [[0, 70, 20, 100]], class_ids=[COUCH])
    (couch,) = _frame(tracker, [[0, 70, 20, 100]], class_ids=[COUCH])
    assert couch.track_id != person.track_id and couch.class_id == COUCH


def test_a_class_flicker_of_several_frames_in_the_same_box_is_still_one_object():
    tracker = Tracker(frame_rate=10)
    seen = set()
    for class_id in (CAR, CAR, TRUCK, TRUCK, TRUCK, CAR):
        seen.update((t.track_id, t.class_id) for t in _frame(tracker, [[0, 0, 40, 40]], class_ids=[class_id]))
    assert seen == {(0, CAR)}


def test_ids_are_unique_across_classes():
    tracker = Tracker(frame_rate=30)
    boxes, classes = [[0, 0, 40, 40], [100, 0, 140, 40]], [CAR, TRUCK]
    _frame(tracker, boxes, class_ids=classes)
    tracks = _frame(tracker, boxes, class_ids=classes)
    assert sorted(t.track_id for t in tracks) == [0, 1]


def test_a_track_carries_the_box_and_score_of_its_matched_detection():
    tracker = Tracker(frame_rate=30)
    _frame(tracker, [[0, 0, 40, 40]], scores=[0.9])
    (track,) = _frame(tracker, [[2, 0, 42, 40]], scores=[0.8])
    assert abs(track.score - 0.8) < 1e-6 and track.box.tolist() == [2, 0, 42, 40]
