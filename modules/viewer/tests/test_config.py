import pytest

from viewer.config import ConfigError, Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.0.143", "http://192.168.0.143:8090"),
        ("192.168.0.143:9000", "http://192.168.0.143:9000"),
        ("http://jetson.local:8090/", "http://jetson.local:8090"),
    ],
)
def test_device_address_forms(raw, expected):
    assert Settings.parse_device(raw) == expected


@pytest.mark.parametrize("raw", ["https://192.168.0.143", "file:///etc/passwd", "http://"])
def test_bad_device_addresses_are_rejected(raw):
    with pytest.raises(ConfigError):
        Settings.parse_device(raw)


def test_settings_need_a_device(monkeypatch):
    monkeypatch.delenv("VIEWER_DEVICE", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_settings_listen_on_localhost_by_default(monkeypatch):
    monkeypatch.setenv("VIEWER_DEVICE", "192.168.0.143")
    settings = Settings.from_env()
    assert (settings.host, settings.port) == ("127.0.0.1", 8080)
