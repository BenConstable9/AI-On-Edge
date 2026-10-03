"""Settings from the environment, read once at start."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigError(ValueError):
    """A setting that cannot be used."""


@dataclass(frozen=True)
class Settings:
    device_url: str
    host: str = "127.0.0.1"
    port: int = 8080
    timeout_s: float = 5.0

    @classmethod
    def from_env(cls, device: str | None = None) -> Settings:
        env = os.environ
        raw = device or env.get("VIEWER_DEVICE")
        if not raw:
            raise ConfigError("give the device address with --device or VIEWER_DEVICE, for example 192.168.0.143")
        return cls(
            device_url=cls.parse_device(raw),
            host=env.get("VIEWER_HOST", cls.host),
            port=int(env.get("VIEWER_PORT", cls.port)),
            timeout_s=float(env.get("VIEWER_TIMEOUT_S", cls.timeout_s)),
        )

    @staticmethod
    def parse_device(raw: str) -> str:
        """A host, host:port or URL to the base URL of the device's preview server."""
        text = raw.strip()
        if "://" not in text:
            text = f"http://{text}"
        parsed = urlparse(text)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ConfigError(f"not a device address: {raw!r}")
        port = parsed.port or 8090
        return f"http://{parsed.hostname}:{port}"
