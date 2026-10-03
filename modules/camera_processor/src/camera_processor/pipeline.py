"""The frame loop: detect, track, count, describe, draw, publish.

One frame in flight at a time, always the newest. The VLM works beside the loop
in its own thread, so detection never waits for it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import numpy as np
import supervision as sv

from .annotate import Annotator
from .best_shot import BestShots, Shot
from .capture import CameraCapture
from .config import Settings
from .counting import Census
from .detector import Detector
from .events import EventLog
from .judge import Judge
from .mjpeg import MjpegServer
from .privacy import PrivacyFilter
from .telemetry import TelemetryClient
from .tracker import Track, Tracker
from .vllm_client import VLLMClient

logger = logging.getLogger(__name__)


class Pipeline:
    # ByteTrack's second association stage uses weak detections to keep known
    # objects. The paper uses 0.1; the replay on the road recording used 0.2.
    _DETECTION_FLOOR = 0.2
    # ByteTrack scales its lost-track memory by the frame rate. Measured on the Orin: 10 to 13.
    _TRACKER_FRAME_RATE = 12.0
    # Track ids seen, kept to log each id once. Ids only grow, so the oldest are out of view.
    _SEEN_KEEP = 1000
    # Waiting states in which no view was kept, so an object that leaves cannot be described.
    _NO_VIEW = ("small", "loading", "failed")

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stop = threading.Event()
        self._detect_times: deque[float] = deque(maxlen=100)
        self._frame_times: deque[float] = deque(maxlen=60)
        self._last_telemetry = time.monotonic()
        self._reported_seen: dict[str, int] = {}
        self._camera = CameraCapture(
            settings.camera_source, settings.capture_width, settings.capture_height, settings.camera_device_dir
        )
        self._detector = Detector(settings.detector_dir)
        self._privacy = PrivacyFilter(settings.privacy_blur, self._detector.names)
        self._annotator = Annotator(settings.preview_width, settings.preview_jpeg_quality)
        self._tracker = Tracker(self._TRACKER_FRAME_RATE, track_activation_threshold=settings.min_confidence)
        self._census = Census()
        self._shots = BestShots(min_box_px=settings.judge_min_box_px, crop_px=settings.judge_crop_px)
        self._judge = Judge(
            VLLMClient(
                settings.vllm_base_url, settings.served_model_name, settings.shm_dir, settings.request_timeout_s
            ),
            settings.judge_question,
            self._shots,
            tuple(sorted(set(self._detector.names.values()))),
            max_duty=settings.judge_max_duty,
            max_tokens=settings.judge_max_tokens,
        )
        self._events = EventLog()
        self._logged_seen: set[int] = set()
        self._not_asked: dict[int, str] = {}
        self._waiting: dict[str, str] = {}
        self._mjpeg = MjpegServer(settings.mjpeg_port, self._events) if settings.mjpeg_enabled else None
        self._telemetry = TelemetryClient()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        self._camera.start()
        self._judge.start()
        if self._mjpeg is not None:
            self._mjpeg.start()
        self._telemetry.start()
        logger.info(
            "running: all %d classes, pixelating %s", len(self._detector.names), ", ".join(self._settings.privacy_blur)
        )
        try:
            while not self._stop.is_set():
                self._tick()
        finally:
            self._camera.stop()
            self._judge.stop()
            if self._mjpeg is not None:
                self._mjpeg.stop()
            self._telemetry.stop()
        return 0

    def _tick(self) -> None:
        image = self._camera.latest(timeout=1.0)
        if image is None:
            return
        height, width = image.shape[:2]

        t0 = time.perf_counter()
        found = self._detector.detect(image, min(self._DETECTION_FLOOR, self._settings.min_confidence))
        self._detect_times.append(time.perf_counter() - t0)

        tracks = self._tracker.update(found)
        names = self._detector.names

        # A VLM label, where the track has one, replaces the detector's everywhere.
        drawn: list[tuple[int, str, np.ndarray, str | None, bool]] = []
        labels: dict[int, str] = {}
        events: list[tuple[str, int, str, str, str, np.ndarray | None]] = []
        answered: list[tuple[int, object, Shot]] = []
        candidates: list[tuple[Track, str, tuple[bool, float, float]]] = []
        waiting: dict[str, str] = {}
        # Every answer is listed, with the view the VLM was given, even if its object has left.
        for track_id, verdict, shot in self._judge.new_verdicts():
            answered.append((track_id, verdict, shot))
        for track in tracks:
            detector_label = names.get(track.class_id, str(track.class_id))
            if track.track_id not in self._logged_seen:
                self._logged_seen.add(track.track_id)
                events.append(("seen", track.track_id, detector_label, detector_label, "", track.box))
            verdict = self._judge.verdict(track.track_id)
            if verdict is None:
                status = self._collect(track, detector_label, found, width, height, candidates)
                self._not_asked[track.track_id] = status
                waiting[str(track.track_id)] = status
                labels[track.track_id] = detector_label
                drawn.append((track.track_id, detector_label, track.box, None, False))
            else:
                labels[track.track_id] = verdict.label
                drawn.append(
                    (track.track_id, verdict.label, track.box, verdict.description or None, verdict.relabelled)
                )
        self._forget_old_seen()
        # An object that leaves keeps its place: its best view waits for the VLM. Only
        # an object with no view to send is reported as not described.
        for track in self._tracker.dropped:
            reason = self._not_asked.pop(track.track_id, None)
            if reason is None or self._judge.verdict(track.track_id) is not None:
                continue
            shot = self._shots.view(track.track_id)
            if shot is not None and shot.canvas is not None:
                frame = self._annotator.to_jpeg(self._annotator.highlight(shot.canvas, shot.box * shot.scale))
                evicted = self._shots.depart(track.track_id, frame)
                if evicted is not None:
                    events.append(("gone", evicted.track_id, evicted.label, evicted.label, "full", None))
            elif reason in self._NO_VIEW:
                label = names.get(track.class_id, str(track.class_id))
                events.append(("gone", track.track_id, label, label, reason, None))
        for shot in self._judge.given_up():
            if shot.track_id not in labels:
                events.append(("gone", shot.track_id, shot.label, shot.label, "failed", None))
        for track_id in self._shots.departed():
            waiting[str(track_id)] = "queued"
        asking = self._judge.in_flight()
        if asking is not None and asking not in labels:
            waiting[str(asking)] = "asking"
        if len(self._not_asked) > 2000:
            self._not_asked = {t: self._not_asked[t] for t in sorted(self._not_asked)[-1000:]}

        self._census.update(tracks, labels)
        self._waiting = waiting

        self._frame_times.append(time.monotonic())
        canvas, scale = self._render(image, found, drawn, events, answered)
        for track, label, score in candidates:
            self._shots.keep(
                Shot(
                    track.track_id,
                    label,
                    self._shots.crop(image, track.box),
                    track.box.copy(),
                    canvas.copy(),
                    scale,
                    score,
                    time.monotonic(),
                )
            )
        self._maybe_report()

    def _collect(
        self,
        track: Track,
        label: str,
        found: sv.Detections,
        width: int,
        height: int,
        candidates: list[tuple[Track, str, tuple[bool, float, float]]],
    ) -> str:
        """Score this view of an undescribed object, and keep it if it is the best so far.

        Returns the object's state for the viewer: loading, asking, small, failed, edge or busy.
        """
        if not self._judge.ready:
            return "loading"
        if self._judge.asking(track.track_id):
            return "asking"
        if not self._shots.big_enough(track.box):
            return "small"
        if not self._judge.needs(track.track_id):
            return "failed"
        others = found.xyxy[np.any(found.xyxy != track.box, axis=1)]
        score = self._shots.score(track.box, others, track.score, width, height)
        if self._shots.better(track.track_id, score):
            candidates.append((track, label, score))
        return "busy" if score[0] else "edge"

    def _render(
        self,
        image: np.ndarray,
        found: sv.Detections,
        drawn: list[tuple[int, str, np.ndarray, str | None, bool]],
        events: list[tuple[str, int, str, str, str, np.ndarray | None]],
        answered: list[tuple[int, object, Shot]],
    ) -> tuple[np.ndarray, float]:
        """Publish this frame. Returns the pixelated, labelled preview and its scale."""
        annotator = self._annotator
        canvas, scale = annotator.downscale(image)
        canvas = self._privacy.apply(canvas, scale, found, [(label, box) for _, label, box, _, _ in drawn])

        annotator.draw_tracks(canvas, [(tid, label, box * scale, changed) for tid, label, box, _, changed in drawn])
        jpeg = annotator.to_jpeg(canvas)
        height, width = image.shape[:2]
        for kind, track_id, label, detector_label, description, box in events:
            if box is None:
                self._events.add(kind, track_id, label, detector_label, description, None)
                continue
            marked = annotator.to_jpeg(annotator.highlight(canvas, box * scale))
            self._events.add(
                kind, track_id, label, detector_label, description, marked, self._fraction(box, width, height)
            )
        # The frame of an answer is the moment the VLM saw, not the moment the answer came.
        for track_id, verdict, shot in answered:
            marked = shot.frame or annotator.to_jpeg(annotator.highlight(shot.canvas, shot.box * shot.scale))
            self._events.add(
                "verdict",
                track_id,
                verdict.label,
                verdict.detector_label,
                verdict.description,
                marked,
                self._fraction(shot.box, width, height),
            )

        if self._mjpeg is not None:
            self._mjpeg.update(jpeg)
            self._mjpeg.update_state(
                {
                    "device_id": self._settings.device_id,
                    "detector": self._detector.label,
                    "provider": self._detector.provider.replace("ExecutionProvider", ""),
                    "vlm": self._settings.served_model_name,
                    "detector_ms": round(self._detect_ms(0.5), 1),
                    "fps": round(self._fps(), 1),
                    **self._census.snapshot(),
                    "judge": self._judge.stats(),
                    "last_correction": self._judge.last_correction(),
                    "described": self._judge.recent(),
                    # Each undescribed object in view, and why: small, edge, busy, asking or loading.
                    "session": self._events.session,
                    "waiting": self._waiting,
                }
            )
        return canvas, scale

    def _maybe_report(self) -> None:
        now = time.monotonic()
        window = now - self._last_telemetry
        if window < self._settings.telemetry_interval_s:
            return
        self._last_telemetry = now
        census = self._census.snapshot()
        appeared = {
            label: count - self._reported_seen.get(label, 0)
            for label, count in census["seen"].items()
            if count > self._reported_seen.get(label, 0)
        }
        self._reported_seen = census["seen"]
        payload = {
            "window_s": round(window, 1),
            "appeared": appeared,
            **census,
            "device_fps": round(self._fps(), 1),
            "detector_ms_p50": round(self._detect_ms(0.5), 1),
            "detector_ms_p95": round(self._detect_ms(0.95), 1),
            "detector": f"{self._detector.label} on {self._detector.provider}",
            "judge": self._judge.stats(),
            "camera": self._camera.stats,
        }
        logger.info("telemetry %s", payload)
        self._telemetry.send_telemetry(payload)

    def _detect_ms(self, quantile: float) -> float:
        if not self._detect_times:
            return 0.0
        ordered = sorted(self._detect_times)
        return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))] * 1000

    def _fps(self) -> float:
        """Frames processed per second, so a slow camera shows as a low figure."""
        if len(self._frame_times) < 2:
            return 0.0
        span = self._frame_times[-1] - self._frame_times[0]
        return (len(self._frame_times) - 1) / span if span > 0 else 0.0

    def _forget_old_seen(self) -> None:
        if len(self._logged_seen) > 2 * self._SEEN_KEEP:
            newest = sorted(self._logged_seen)[-self._SEEN_KEEP :]
            self._logged_seen = set(newest)

    @staticmethod
    def _fraction(box: np.ndarray, width: int, height: int) -> tuple[float, float, float, float]:
        """A box as fractions of the frame, for the viewer."""
        return (
            round(float(box[0]) / width, 4),
            round(float(box[1]) / height, 4),
            round(float(box[2]) / width, 4),
            round(float(box[3]) / height, 4),
        )
