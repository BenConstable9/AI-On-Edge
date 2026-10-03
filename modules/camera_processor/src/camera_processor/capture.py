"""Camera capture with a single-slot buffer.

Deliberately not a queue. Inference is slower than capture, so a queue would grow
without bound and the viewer would show progressively older frames — a demo that
looks broken. Holding only the newest frame means the pipeline always works on
what the camera can currently see, and intervening frames are simply dropped.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraCapture:
    """source: "auto" for the first video device that gives frames, a device
    number or path, or a video file. device_dir holds the video device nodes."""

    # About 2 s of failed reads at 0.1 s each before the camera counts as gone.
    _LOST_AFTER_FAILURES = 20
    _RESCAN_S = 2.0

    def __init__(self, source: str, width: int, height: int, device_dir: Path = Path("/dev")) -> None:
        self._source = source
        self._device_dir = device_dir
        self._width = width
        self._height = height
        self._capture: cv2.VideoCapture | None = None
        self._is_file = os.path.isfile(source)
        self._frame_interval = 0.0

        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._new_frame = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._captured = 0
        self._dropped = 0  # captured frames replaced before the pipeline took them

    def start(self) -> None:
        if not self._open():
            if self._is_file:
                raise RuntimeError(f"could not open video file {self._source!r}")
            logger.warning("no camera in %s yet; looking every %.0f s", self._device_dir, self._RESCAN_S)
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def _candidates(self) -> list[str]:
        if self._is_file:
            return [self._source]
        if self._source == "auto":
            nodes = [p for p in self._device_dir.glob("video*") if p.name[5:].isdigit()]
            return [str(p) for p in sorted(nodes, key=lambda p: int(p.name[5:]))]
        if self._source.isdigit():
            return [str(self._device_dir / f"video{self._source}")]
        return [self._source]

    def _open(self) -> bool:
        for path in self._candidates():
            capture = self._open_one(path)
            if capture is not None:
                self._capture = capture
                return True
        return False

    def _open_one(self, path: str) -> cv2.VideoCapture | None:
        if self._is_file:
            capture = cv2.VideoCapture(path)
        else:
            capture = cv2.VideoCapture(path, cv2.CAP_V4L2)
            # Uncompressed YUYV at 1280x720 fills USB 2 at 10 fps. MJPG reaches 30.
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            capture.set(cv2.CAP_PROP_FPS, 30)
        if not capture.isOpened():
            capture.release()
            return None
        if not self._is_file:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            # Keep the driver-side buffer minimal so reads return current frames.
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # A webcam has metadata nodes beside its picture node. They open, but give no frame.
            if not capture.read()[0]:
                capture.release()
                return None

        actual_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = int(capture.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode(errors="replace")
        logger.info(
            "camera open: %s at %dx%d, %s, %.0f fps", path, actual_w, actual_h, fourcc, capture.get(cv2.CAP_PROP_FPS)
        )

        # A camera blocks until the next frame. A file does not, so pace it to its
        # own frame rate, or it plays many times too fast and tracks jump.
        fps = capture.get(cv2.CAP_PROP_FPS) if self._is_file else 0.0
        self._frame_interval = 1.0 / fps if fps > 0 else 0.0
        return capture

    def _reopen(self) -> None:
        """Wait for the camera to come back, for example after it is plugged in again."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        while not self._stop.is_set():
            if self._open():
                logger.info("camera found again")
                return
            self._stop.wait(self._RESCAN_S)

    def _run(self) -> None:
        if self._capture is None:
            self._reopen()
        due = time.monotonic()
        failures = 0
        while not self._stop.is_set() and self._capture is not None:
            ok, image = self._capture.read()
            if not ok:
                if self._is_file:
                    # A recording stands in for the camera, so start it again.
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    logger.info("video ended, starting again")
                    continue
                failures += 1
                if failures == 1:
                    logger.warning("camera read failed; retrying every 0.1 s")
                if failures >= self._LOST_AFTER_FAILURES:
                    logger.warning("camera lost; looking for it every %.0f s", self._RESCAN_S)
                    self._reopen()
                    failures = 0
                    due = time.monotonic()
                    continue
                time.sleep(0.1)
                continue
            if failures:
                logger.info("camera reads again")
                failures = 0

            if self._frame_interval:
                due += self._frame_interval
                time.sleep(max(0.0, due - time.monotonic()))

            self._captured += 1
            with self._lock:
                if self._latest is not None:
                    self._dropped += 1
                self._latest = image
            self._new_frame.set()

    def latest(self, timeout: float = 1.0) -> np.ndarray | None:
        """Take the newest frame, clearing the slot so the same one isn't reused."""
        if not self._new_frame.wait(timeout):
            return None
        with self._lock:
            frame = self._latest
            self._latest = None
            self._new_frame.clear()
        return frame

    @property
    def stats(self) -> dict[str, int]:
        """Frame counts for the telemetry."""
        return {"captured": self._captured, "dropped": self._dropped}

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._capture is not None:
            self._capture.release()
        logger.info("camera stopped: %s", self.stats)
