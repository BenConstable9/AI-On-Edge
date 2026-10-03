from __future__ import annotations

from camera_processor.capture import CameraCapture


def test_auto_tries_every_video_device_in_number_order(tmp_path):
    for name in ("video10", "video2", "video0", "video-meta", "media0"):
        (tmp_path / name).touch()
    camera = CameraCapture("auto", 1280, 720, tmp_path)
    assert camera._candidates() == [str(tmp_path / n) for n in ("video0", "video2", "video10")]


def test_a_number_or_a_path_picks_one_device(tmp_path):
    assert CameraCapture("3", 1280, 720, tmp_path)._candidates() == [str(tmp_path / "video3")]
    assert CameraCapture("/dev/video5", 1280, 720, tmp_path)._candidates() == ["/dev/video5"]


def test_no_camera_yet_does_not_stop_the_module(tmp_path):
    camera = CameraCapture("auto", 1280, 720, tmp_path)
    camera._RESCAN_S = 0.01
    camera.start()
    assert camera.latest(timeout=0.05) is None
    camera.stop()
