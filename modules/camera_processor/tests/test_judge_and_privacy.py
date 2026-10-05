import time
from itertools import pairwise

import numpy as np
import supervision as sv

from camera_processor.best_shot import BestShots, Shot
from camera_processor.judge import Judge, Verdict
from camera_processor.privacy import PrivacyFilter
from camera_processor.vllm_client import VLLMError

NAMES = ("bicycle", "bus", "car", "chair", "cup", "keyboard", "motorcycle", "person", "potted plant")


def test_verdict_takes_the_vlm_label_when_it_differs():
    verdict = Verdict.parse('{"label": "Stroller", "description": "black pram"}', "motorcycle")
    assert (verdict.label, verdict.description, verdict.relabelled) == ("stroller", "black pram", True)


def test_a_null_label_keeps_the_detector_label():
    verdict = Verdict.parse('{"label": null, "description": "black office chair"}', "chair")
    assert (verdict.label, verdict.description, verdict.relabelled) == ("chair", "black office chair", False)


def test_verdict_keeps_the_detector_label_when_they_agree_or_the_answer_is_unusable():
    assert not Verdict.parse('{"label": "chair", "description": "black"}', "chair").relabelled
    broken = Verdict.parse("a red mug", "cup")
    assert (broken.label, broken.description, broken.relabelled) == ("cup", "a red mug", False)
    assert Verdict.parse('{"label": "", "description": "x"}', "cup").label == "cup"


def test_prompt_offers_the_classes_allows_other_names_and_asks_for_person():
    judge = Judge(_ReadyClient(), "under 8 words, its colour.", BestShots(), ("bus", "chair", "cup"))
    prompt = judge.prompt("chair")
    assert "'chair'" in prompt and "bus, chair, cup" in prompt and "your own" in prompt
    assert "'person'" in prompt and prompt.endswith("under 8 words, its colour.")


class _ReadyClient:
    def is_ready(self):
        return True


def _offer(shots, track_id, label):
    crop = np.zeros((8, 8, 3), np.uint8)
    shots.keep(Shot(track_id, label, crop, np.zeros(4, np.float32), crop, 1.0, (True, 1.0, 0.9), 0.0))


def _wait_for(judge, track_id):
    deadline = time.monotonic() + 5
    while judge.verdict(track_id) is None:
        assert time.monotonic() < deadline, f"no verdict for track {track_id}"
        time.sleep(0.01)


def test_the_judge_needs_every_unanswered_track_people_included():
    judge = Judge(_ReadyClient(), "its colour.", BestShots(), NAMES)
    judge.ready = True
    assert judge.needs(1) and judge.needs(2)


class _CorrectingClient(_ReadyClient):
    def describe(self, image, prompt, max_tokens, schema):
        if "'motorcycle'" in prompt:
            return '{"label": "bicycle", "description": "cargo bike"}', 0.01
        return '{"label": null, "description": "red"}', 0.01


def test_the_judge_counts_corrections_and_keeps_the_latest():
    shots = BestShots(collect_s=0, max_wait_s=0)
    judge = Judge(_CorrectingClient(), "its colour.", shots, NAMES, max_duty=1.0)
    judge.start()
    try:
        for track_id, label in ((1, "car"), (2, "motorcycle")):
            _offer(shots, track_id, label)
            _wait_for(judge, track_id)
    finally:
        judge.stop()
    assert (judge.answered, judge.stats()["corrected"]) == (2, 1)
    assert judge.last_correction() == {"detector": "motorcycle", "label": "bicycle"}
    fresh = judge.new_verdicts()
    assert [(t, v.label, s.track_id) for t, v, s in fresh] == [(1, "car", 1), (2, "bicycle", 2)]
    assert judge.new_verdicts() == []


class _FlakyClient(_ReadyClient):
    def __init__(self, failures):
        self.failures = failures
        self.warm = False

    def describe(self, image, prompt, max_tokens, schema):
        if not self.warm:
            self.warm = True
            return '{"label": null, "description": "x"}', 0.01
        if self.failures:
            self.failures -= 1
            raise VLLMError("vLLM restarted")
        return '{"label": null, "description": "red"}', 0.01


def test_a_failed_call_is_retried_with_the_same_view():
    shots = BestShots(collect_s=0, max_wait_s=0)
    judge = Judge(_FlakyClient(failures=1), "its colour.", shots, NAMES, max_duty=1.0)
    _offer(shots, 1, "car")
    shots.depart(1, b"frame")
    judge.start()
    try:
        _wait_for(judge, 1)
    finally:
        judge.stop()
    assert (judge.failed, judge.verdict(1).description) == (1, "red")
    assert judge.new_verdicts()[0][2].frame == b"frame"


def test_a_view_that_fails_twice_is_given_up():
    shots = BestShots(collect_s=0, max_wait_s=0)
    judge = Judge(_FlakyClient(failures=2), "its colour.", shots, NAMES, max_duty=1.0)
    judge.start()
    try:
        _offer(shots, 1, "car")
        deadline = time.monotonic() + 5
        while not (given := judge.given_up()):
            assert time.monotonic() < deadline, "the judge never gave up"
            time.sleep(0.01)
    finally:
        judge.stop()
    assert [s.track_id for s in given] == [1] and judge.verdict(1) is None and not judge.needs(1)


class _TimedClient(_ReadyClient):
    def __init__(self):
        self.calls = []

    def describe(self, image, prompt, max_tokens, schema):
        started = time.monotonic()
        time.sleep(0.1)
        self.calls.append((started, time.monotonic()))
        return '{"label": null, "description": "x"}', 0.1


def test_the_judge_rests_so_the_vlm_is_busy_at_most_max_duty():
    client = _TimedClient()
    shots = BestShots(collect_s=0, max_wait_s=0)
    judge = Judge(client, "its colour.", shots, NAMES, max_duty=0.5)
    judge.start()
    try:
        for track_id in range(1, 4):
            _offer(shots, track_id, "car")
            _wait_for(judge, track_id)
    finally:
        judge.stop()
    # The warm-up call comes first; each later call waits at least as long as the one before took.
    gaps = [start - previous_end for (_, previous_end), (start, _) in pairwise(client.calls[1:])]
    assert len(gaps) == 2 and all(gap >= 0.09 for gap in gaps)


def test_tidy_makes_one_short_ascii_line():
    assert Verdict.tidy('  "White  Royal Mail\nvan."  ') == "White Royal Mail van"
    assert Verdict.tidy("Caf\u00e9 lorry") == "Caf lorry"
    long = Verdict.tidy("x" * 100)
    assert len(long) == 60 and long.endswith("...")


PRIVACY = PrivacyFilter(("person",), {0: "person", 2: "car"})


def _found(boxes, class_ids):
    return sv.Detections(
        xyxy=np.array(boxes, np.float32),
        confidence=np.full(len(boxes), 0.9, np.float32),
        class_id=np.array(class_ids, np.int64),
    )


def test_a_person_detection_is_pixelated_and_nothing_else():
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    out = PRIVACY.apply(image, 1.0, _found([[0, 0, 32, 32], [32, 32, 64, 64]], [0, 2]), [])
    assert len(np.unique(out[:6, :6].reshape(-1, 3), axis=0)) == 1
    assert np.array_equal(out[32:, 32:], image[32:, 32:])
    assert not np.array_equal(out, image)


def test_a_track_the_vlm_named_a_person_is_pixelated_without_a_person_detection():
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    box = np.array([0, 0, 32, 32], np.float32)
    out = PRIVACY.apply(image, 1.0, _found([[0, 0, 32, 32]], [2]), [("person", box)])
    assert len(np.unique(out[:6, :6].reshape(-1, 3), axis=0)) == 1
    assert np.array_equal(PRIVACY.apply(image, 1.0, _found([[0, 0, 32, 32]], [2]), [("car", box)]), image)


def test_a_person_near_the_camera_gets_as_few_blocks_as_one_far_away():
    # A fixed block size left a close face about 16 blocks wide, enough to recognise.
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    for box in ([0, 0, 960, 720], [0, 0, 240, 180]):
        out = PRIVACY.apply(image, 1.0, _found([box], [0]), [])
        region = out[: box[3], : box[2]]
        columns = len(np.unique(region[0], axis=0))
        assert columns <= 16, (box, columns)
