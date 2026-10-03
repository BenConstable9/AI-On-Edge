"""Settings from the environment, read once at start.

The module detects all 80 COCO classes, counts every object that appears, and
asks the VLM about each one. People are pixelated in
the preview only: the VLM gets the original crop, on the device. The same
settings work for a street and for an office.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Everything the environment decides. Read once, at start."""

    camera_source: str = "auto"
    camera_device_dir: Path = Path("/dev")
    capture_width: int = 1280
    capture_height: int = 720

    detector_dir: Path = Path("/opt/detector")
    # ByteTrack's track_activation_threshold: the score that starts a new object. Its published default.
    min_confidence: float = 0.7
    privacy_blur: tuple[str, ...] = ("person",)

    vllm_base_url: str = "http://vllm:8001"
    served_model_name: str = "qwen3-vl-4b"
    shm_dir: Path = Path("/shm")
    request_timeout_s: float = 60.0
    judge_crop_px: int = 448
    judge_question: str = "Under 8 words: its colour, and any brand or company name on it. Do not read number plates."
    judge_max_duty: float = 0.5
    judge_max_tokens: int = 48
    judge_min_box_px: int = 64

    preview_width: int = 960
    preview_jpeg_quality: int = 70
    mjpeg_enabled: bool = True
    mjpeg_port: int = 8090

    device_id: str = "jetson-orin"
    telemetry_interval_s: float = 10.0

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        defaults = cls()
        settings = replace(
            defaults,
            camera_source=env.get("CAMERA_SOURCE", defaults.camera_source),
            camera_device_dir=Path(env.get("CAMERA_DEVICE_DIR", defaults.camera_device_dir)),
            capture_width=int(env.get("CAPTURE_WIDTH", defaults.capture_width)),
            capture_height=int(env.get("CAPTURE_HEIGHT", defaults.capture_height)),
            detector_dir=Path(env.get("DETECTOR_DIR", defaults.detector_dir)),
            min_confidence=float(env.get("MIN_CONFIDENCE", defaults.min_confidence)),
            vllm_base_url=env.get("VLLM_BASE_URL", defaults.vllm_base_url),
            served_model_name=env.get("SERVED_MODEL_NAME", defaults.served_model_name),
            shm_dir=Path(env.get("IMAGE_SHM_DIR", defaults.shm_dir)),
            request_timeout_s=float(env.get("REQUEST_TIMEOUT_S", defaults.request_timeout_s)),
            judge_crop_px=int(env.get("JUDGE_CROP_PX", defaults.judge_crop_px)),
            preview_width=int(env.get("PREVIEW_WIDTH", defaults.preview_width)),
            preview_jpeg_quality=int(env.get("PREVIEW_JPEG_QUALITY", defaults.preview_jpeg_quality)),
            mjpeg_enabled=env.get("MJPEG_ENABLED", "1") == "1",
            mjpeg_port=int(env.get("MJPEG_PORT", defaults.mjpeg_port)),
            device_id=env.get("IOTEDGE_DEVICEID") or env.get("DEVICE_ID", defaults.device_id),
            telemetry_interval_s=float(env.get("TELEMETRY_INTERVAL_S", defaults.telemetry_interval_s)),
        )
        if not 0 < settings.min_confidence < 1:
            raise ValueError(f"MIN_CONFIDENCE must be between 0 and 1, got {settings.min_confidence}")
        return settings
