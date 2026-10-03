import numpy as np

from camera_processor.best_shot import BestShots, Shot

W, H = 1280, 720
POOL = BestShots(crop_px=448)


def _shot(track_id, score, first_seen=0.0):
    return Shot(
        track_id,
        "car",
        np.zeros((4, 4, 3), np.uint8),
        np.zeros(4, np.float32),
        np.zeros((4, 4, 3), np.uint8),
        1.0,
        score,
        first_seen,
    )


def test_a_whole_view_beats_a_larger_view_cut_by_the_edge():
    none = np.zeros((0, 4), np.float32)
    whole = POOL.score(np.array([100, 100, 200, 180]), none, 0.8, W, H)
    cut = POOL.score(np.array([0, 100, 400, 400]), none, 0.9, W, H)
    assert whole > cut


def test_a_hidden_view_scores_below_a_clear_one():
    box = np.array([100, 100, 300, 200], np.float32)
    clear = POOL.score(box, np.zeros((0, 4), np.float32), 0.8, W, H)
    hidden = POOL.score(box, np.array([[100, 100, 200, 200]], np.float32), 0.8, W, H)
    assert clear > hidden
    assert abs(hidden[1] - 200 * 100 * 0.5) < 1e-3


def test_pixels_beyond_the_crop_size_do_not_count():
    none = np.zeros((0, 4), np.float32)
    huge = POOL.score(np.array([10, 10, 1000, 700]), none, 0.8, W, H)
    big = POOL.score(np.array([10, 10, 600, 600]), none, 0.9, W, H)
    assert huge[1] == big[1] == 448 * 448
    assert big > huge  # equal pixels, so the detector score decides


def test_the_pool_keeps_the_best_view_and_the_first_sighting_time():
    shots = BestShots()
    shots.keep(_shot(1, (True, 100.0, 0.8), first_seen=5.0))
    assert not shots.better(1, (True, 50.0, 0.9))
    assert shots.better(1, (True, 200.0, 0.5))
    shots.keep(_shot(1, (True, 200.0, 0.5), first_seen=9.0))
    shot = shots.next(now=7.0)
    assert shot.score == (True, 200.0, 0.5) and shot.first_seen == 5.0


def test_a_whole_view_is_ready_after_one_second_a_part_view_after_two():
    shots = BestShots(collect_s=1.0, max_wait_s=2.0)
    shots.keep(_shot(1, (False, 100.0, 0.8), first_seen=0.0))
    shots.keep(_shot(2, (True, 100.0, 0.8), first_seen=0.5))
    assert shots.next(now=1.4) is None
    assert shots.next(now=1.6).track_id == 2
    assert shots.next(now=2.1).track_id == 1


def test_the_longest_waiting_object_goes_first():
    shots = BestShots()
    shots.keep(_shot(7, (True, 10.0, 0.8), first_seen=3.0))
    shots.keep(_shot(4, (True, 99.0, 0.9), first_seen=1.0))
    assert [shots.next(now=10).track_id, shots.next(now=10).track_id] == [4, 7]
    assert shots.next(now=10) is None


def test_small_boxes_are_not_collected():
    shots = BestShots(min_box_px=64)
    assert not shots.big_enough(np.array([0, 0, 63, 200]))
    assert shots.big_enough(np.array([0, 0, 64, 64]))


def test_an_object_that_left_waits_behind_objects_in_view():
    shots = BestShots()
    shots.keep(_shot(1, (True, 10.0, 0.8), first_seen=0.0))
    shots.keep(_shot(2, (True, 10.0, 0.8), first_seen=5.0))
    assert shots.depart(1, b"frame") is None
    assert shots.departed() == [1]
    assert shots.next(now=7.0).track_id == 2  # in view and ready, though it came later
    left = shots.next(now=7.0)
    assert (left.track_id, left.canvas, left.frame, left.departed) == (1, None, b"frame", True)
    assert shots.next(now=7.0) is None


def test_a_full_departed_queue_drops_the_one_that_waited_longest():
    shots = BestShots(max_departed=2)
    for track_id in (1, 2, 3):
        shots.keep(_shot(track_id, (True, 10.0, 0.8), first_seen=float(track_id)))
    assert shots.depart(1, b"a") is None and shots.depart(2, b"b") is None
    assert shots.depart(3, b"c").track_id == 1
    assert sorted(shots.departed()) == [2, 3]


def test_a_failed_shot_put_back_keeps_its_place():
    shots = BestShots()
    for track_id in (1, 2):
        shots.keep(_shot(track_id, (True, 10.0, 0.8), first_seen=float(track_id)))
        shots.depart(track_id, b"f")
    retried = shots.next(now=0.0)
    shots.put_back(retried)
    assert shots.next(now=0.0).track_id == retried.track_id == 1


def test_crop_is_bounded_and_keeps_aspect():
    image = np.zeros((1080, 1920, 3), np.uint8)
    crop = POOL.crop(image, np.array([100, 100, 1100, 600], np.float32))
    assert max(crop.shape[:2]) == 448
    assert abs(crop.shape[1] / crop.shape[0] - 2.0) < 0.05


def test_crop_margin_stays_inside_the_frame():
    image = np.zeros((100, 100, 3), np.uint8)
    crop = POOL.crop(image, np.array([0, 0, 50, 50], np.float32))
    assert crop.shape[:2] == (55, 55)
