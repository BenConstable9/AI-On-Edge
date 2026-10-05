"""Asks the VLM to name and describe tracked objects, without slowing the frame loop.

The VLM answers in about three seconds; the detector runs many times faster. So
the judge runs in its own thread and, whenever the VLM is free, takes the next
object from BestShots: the one that has waited longest, with its best view so
far. Each track is judged once, or again if its detector label changes.

The answer is JSON with a label and a description. The label is free text, so
the VLM can name what the detector has no class for. The prompt asks it to
prefer the detector's class names, and to call any human "person", so the
counts and the pixelation see the names they know.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

import numpy as np

from .best_shot import BestShots, Shot
from .vllm_client import VLLMClient, VLLMError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verdict:
    LABEL_MAX = 24

    label: str
    description: str
    detector_label: str

    @property
    def relabelled(self) -> bool:
        return self.label != self.detector_label

    @classmethod
    def parse(cls, answer: str, detector_label: str) -> Verdict:
        """The VLM's JSON answer. A null label, or anything unusable, keeps the
        detector's label: the VLM gives null when it agrees or is not certain.
        """
        try:
            raw = json.loads(answer)
        except json.JSONDecodeError:
            raw = {"description": answer}
        if not isinstance(raw, dict):
            raw = {"description": str(raw)}
        given = raw.get("label")
        label = (cls.tidy(str(given), cls.LABEL_MAX).lower() if given else "") or detector_label
        return cls(label, cls.tidy(str(raw.get("description", ""))), detector_label)

    @staticmethod
    def tidy(answer: str, limit: int = 60) -> str:
        """One short ASCII line: OpenCV's fonts cannot draw anything else."""
        text = " ".join(answer.encode("ascii", "ignore").decode().split()).strip(" \"'.")
        return text if len(text) <= limit else text[: limit - 3] + "..."


class Judge:
    _MAX_REMEMBERED = 1000
    _MAX_ATTEMPTS = 2

    SCHEMA = {
        "type": "object",
        "properties": {
            "label": {"anyOf": [{"type": "string", "maxLength": Verdict.LABEL_MAX}, {"type": "null"}]},
            "description": {"type": "string", "maxLength": 80},
        },
        "required": ["label", "description"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        client: VLLMClient,
        question: str,
        shots: BestShots,
        vocabulary: tuple[str, ...],
        *,
        max_duty: float = 0.5,
        max_tokens: int = 48,
    ) -> None:
        """shots: where the judge takes its next object from, with that object's best view.
        vocabulary: the detector's class names, offered to the VLM as preferred labels.
        max_duty: the largest share of time the VLM may be busy. The detector shares
        its GPU: measured, back-to-back calls cut 13 fps to 7.5, half duty keeps 10.5."""
        self._client = client
        self._question = question
        self._shots = shots
        self._vocabulary = vocabulary
        self._max_duty = max_duty
        self._max_tokens = max_tokens
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._in_flight: int | None = None
        self._verdicts: OrderedDict[int, Verdict] = OrderedDict()
        self._new: deque[tuple[int, Verdict, Shot]] = deque()
        self._given_up: deque[Shot] = deque()
        self._recent: deque[dict[str, object]] = deque(maxlen=10)
        self._attempts: dict[int, int] = {}
        self._latency: deque[float] = deque(maxlen=50)
        self._thread = threading.Thread(target=self._run, name="judge", daemon=True)
        self._last_correction: dict[str, str] | None = None
        self.ready = False
        self.answered = 0
        self.corrected = 0
        self.failed = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def needs(self, track_id: int) -> bool:
        """The track has no answer yet and may still be asked."""
        if not self.ready:
            return False
        with self._lock:
            return track_id not in self._verdicts and self._attempts.get(track_id, 0) < self._MAX_ATTEMPTS

    def verdict(self, track_id: int) -> Verdict | None:
        with self._lock:
            return self._verdicts.get(track_id)

    def new_verdicts(self) -> list[tuple[int, Verdict, Shot]]:
        """Answers since the last call, each with the view the VLM was given."""
        with self._lock:
            fresh = list(self._new)
            self._new.clear()
            return fresh

    def given_up(self) -> list[Shot]:
        """Shots that failed on every attempt since the last call."""
        with self._lock:
            shots = list(self._given_up)
            self._given_up.clear()
            return shots

    def asking(self, track_id: int) -> bool:
        """The VLM is looking at this track now."""
        with self._lock:
            return self._in_flight == track_id

    def in_flight(self) -> int | None:
        """The track the VLM is looking at now, in view or not."""
        with self._lock:
            return self._in_flight

    def recent(self) -> list[dict[str, object]]:
        """The newest answers first."""
        with self._lock:
            return list(reversed(self._recent))

    def last_correction(self) -> dict[str, str] | None:
        """The newest answer that changed the detector's label."""
        with self._lock:
            return self._last_correction

    def stats(self) -> dict[str, object]:
        ordered = sorted(self._latency)
        return {
            "ready": self.ready,
            "answered": self.answered,
            "corrected": self.corrected,
            "failed": self.failed,
            "ms_p50": round(ordered[len(ordered) // 2] * 1000) if ordered else None,
        }

    def prompt(self, detector_label: str, question: str | None = None) -> str:
        """The detector's class names are preferred, so the counts do not split across
        synonyms. Any other name is allowed for what the detector cannot name."""
        return (
            f"A detector labelled this object '{detector_label}'. Reply in JSON. "
            f"label: null if '{detector_label}' is right, or if you are not certain. "
            f"Give a name only when the detector is clearly wrong. "
            f"Prefer one of these names: {', '.join(self._vocabulary)}. "
            f"If none fits, use your own one or two lowercase words. "
            f"Call any human 'person', whatever their age or sex. "
            f"description: {question or self._question}"
        )

    def _warm_up(self) -> None:
        """The first request after vLLM starts takes about 13 s, and the first with
        this JSON schema compiles a grammar for about 30 s more. Pay both here."""
        try:
            image = np.full((64, 64, 3), 128, np.uint8)
            _, seconds = self._client.describe(image, self.prompt("object", "Its colour."), 32, self.SCHEMA)
            logger.info("vLLM warm in %.1f s", seconds)
        except VLLMError as exc:
            logger.warning("vLLM warm-up failed: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set() and not self.ready:
            self.ready = self._client.is_ready()
            if not self.ready:
                logger.info("waiting for vLLM; detection and counting carry on without it")
                self._stop.wait(15)
        if self.ready:
            logger.info("vLLM is ready; the judge is on")
            self._warm_up()

        rest_until = 0.0
        while not self._stop.is_set():
            if self._stop.wait(max(0.0, rest_until - time.monotonic())):
                return
            shot = self._shots.next()
            if shot is None:
                self._stop.wait(0.1)
                continue

            track_id, label, crop = shot.track_id, shot.label, shot.crop
            with self._lock:
                if track_id in self._verdicts:
                    continue
                self._attempts[track_id] = self._attempts.get(track_id, 0) + 1
                self._in_flight = track_id
            started = time.monotonic()
            try:
                answer, seconds = self._client.describe(crop, self.prompt(label), self._max_tokens, self.SCHEMA)
            except VLLMError as exc:
                self.failed += 1
                logger.warning("judge failed for %s #%d: %s", label, track_id, exc)
                with self._lock:
                    if self._attempts[track_id] < self._MAX_ATTEMPTS:
                        self._shots.put_back(shot)
                    else:
                        self._given_up.append(shot)
                continue
            finally:
                with self._lock:
                    self._in_flight = None
                busy = time.monotonic() - started
                rest_until = time.monotonic() + busy * (1 - self._max_duty) / self._max_duty

            verdict = Verdict.parse(answer, label)
            self._latency.append(seconds)
            self.answered += 1
            with self._lock:
                self._verdicts[track_id] = verdict
                self._new.append((track_id, verdict, shot))
                if verdict.relabelled:
                    self.corrected += 1
                    self._last_correction = {"detector": label, "label": verdict.label}
                self._recent.append(
                    {
                        "id": track_id,
                        "label": verdict.label,
                        "detector": label,
                        "text": verdict.description,
                        "ms": round(seconds * 1000),
                    }
                )
                while len(self._verdicts) > self._MAX_REMEMBERED:
                    self._verdicts.popitem(last=False)
                if len(self._attempts) > self._MAX_REMEMBERED:
                    self._attempts.clear()
            change = f" (detector: {label})" if verdict.relabelled else ""
            logger.info(
                "judge #%d in %.0f ms: %s%s: %s", track_id, seconds * 1000, verdict.label, change, verdict.description
            )
