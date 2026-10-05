import pytest

from camera_processor.config import Settings


def test_defaults_pixelate_people():
    settings = Settings()
    assert settings.privacy_blur == ("person",)
    assert 0 < settings.min_confidence < 1
    assert "number plates" in settings.judge_question


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("CAMERA_SOURCE", "/videos/road.mp4")
    monkeypatch.setenv("IOTEDGE_DEVICEID", "orin-1")
    monkeypatch.setenv("MIN_CONFIDENCE", "0.4")
    settings = Settings.from_env()
    assert settings.camera_source == "/videos/road.mp4"
    assert settings.device_id == "orin-1"
    assert settings.min_confidence == 0.4


@pytest.mark.parametrize("value", ["0", "1.5"])
def test_settings_reject_a_confidence_outside_zero_to_one(monkeypatch, value):
    monkeypatch.setenv("MIN_CONFIDENCE", value)
    with pytest.raises(ValueError):
        Settings.from_env()
