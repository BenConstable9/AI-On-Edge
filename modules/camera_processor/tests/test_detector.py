import numpy as np
import supervision as sv

from camera_processor.detector import Detector


def _logit(p):
    return np.log(p / (1 - p))


def test_preprocess_shape_channel_order_and_normalisation():
    image = np.zeros((20, 40, 3), np.uint8)
    image[:, :, 2] = 255  # red in BGR
    tensor = Detector.preprocess(image, 8)
    assert tensor.shape == (1, 3, 8, 8)
    assert tensor.dtype == np.float32
    assert np.allclose(tensor[0, 0], (1 - 0.485) / 0.229)  # R channel first
    assert np.allclose(tensor[0, 2], (0 - 0.406) / 0.225)


def test_decode_scales_centre_boxes_to_frame_pixels():
    boxes = np.array([[0.5, 0.5, 0.2, 0.4]], np.float32)
    logits = np.full((1, 91), -10.0, np.float32)
    logits[0, 3] = _logit(0.9)
    found = Detector.decode(boxes, logits, width=1000, height=500, min_confidence=0.5)
    assert found.class_id.tolist() == [3]
    assert np.allclose(found.xyxy, [[400, 150, 600, 350]])
    assert np.allclose(found.confidence, [0.9], atol=1e-5)


def test_decode_keeps_only_wanted_classes_and_maps_ids_back():
    boxes = np.array([[0.5, 0.5, 0.1, 0.1], [0.2, 0.2, 0.1, 0.1]], np.float32)
    logits = np.full((2, 91), -10.0, np.float32)
    logits[0, 1] = _logit(0.95)  # person
    logits[1, 8] = _logit(0.8)  # truck
    found = Detector.decode(boxes, logits, 100, 100, 0.5, class_ids=np.array([3, 8]))
    assert found.class_id.tolist() == [8]


def test_decode_sorts_by_score_and_honours_threshold():
    boxes = np.array([[0.2, 0.5, 0.1, 0.1], [0.5, 0.5, 0.1, 0.1], [0.8, 0.5, 0.1, 0.1]], np.float32)
    logits = np.full((3, 91), -10.0, np.float32)
    logits[0, 3], logits[1, 3], logits[2, 3] = _logit(0.6), _logit(0.9), _logit(0.3)
    found = Detector.decode(boxes, logits, 100, 100, 0.5)
    assert np.allclose(found.confidence, [0.9, 0.6], atol=1e-5)


def test_one_box_under_two_classes_becomes_one_detection():
    boxes = np.array([[0.5, 0.5, 0.4, 0.2]], np.float32)
    logits = np.full((1, 91), -10.0, np.float32)
    logits[0, 3], logits[0, 8] = _logit(0.8), _logit(0.6)  # car, truck
    found = Detector.decode(boxes, logits, 1000, 1000, 0.5)
    assert found.class_id.tolist() == [3]


def _boxes(boxes, class_ids):
    return sv.Detections(
        xyxy=np.array(boxes, np.float32),
        confidence=np.linspace(0.9, 0.8, len(boxes)).astype(np.float32),
        class_id=np.array(class_ids, np.int64),
    )


def test_a_part_boxed_again_inside_its_object_is_dropped():
    # The SUV case: a whole-car box and a cabin box inside it, both "car".
    whole, cabin = [781, 52, 1123, 212], [800, 55, 1000, 150]
    kept = Detector.remove_duplicates(_boxes([whole, cabin], [3, 3]))
    assert kept.xyxy.tolist() == [whole]


def test_a_person_in_front_of_a_car_is_kept():
    car, person = [100, 100, 500, 300], [200, 120, 260, 290]
    assert len(Detector.remove_duplicates(_boxes([car, person], [3, 1]))) == 2


def test_two_cars_side_by_side_are_both_kept():
    assert len(Detector.remove_duplicates(_boxes([[0, 0, 100, 50], [80, 0, 180, 50]], [3, 3]))) == 2


def test_decode_with_no_wanted_classes_is_empty():
    found = Detector.decode(
        np.zeros((1, 4), np.float32), np.zeros((1, 91), np.float32), 10, 10, 0.1, np.array([], np.int64)
    )
    assert len(found) == 0 and found.class_id is not None


def test_boxes_are_clipped_to_the_frame():
    boxes = np.array([[0.05, 0.5, 0.3, 0.2]], np.float32)
    logits = np.full((1, 91), -10.0, np.float32)
    logits[0, 3] = _logit(0.9)
    found = Detector.decode(boxes, logits, 100, 100, 0.5)
    assert found.xyxy[0, 0] == 0
