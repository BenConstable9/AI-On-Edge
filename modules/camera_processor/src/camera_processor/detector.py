"""RF-DETR through ONNX Runtime.

The model is exported to ONNX when the image is built, so the device needs no
PyTorch model code. It runs on the ONNX Runtime CUDA provider. The TensorRT
provider is not used: the base image's TensorRT 10.3 is built for JetPack 6 and
cannot load on a JetPack 7 host.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import supervision as sv

logger = logging.getLogger(__name__)


class DetectorError(RuntimeError):
    """The detector model is missing or cannot run."""


class Detector:
    # ImageNet normalisation, as RF-DETR was trained.
    _MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
    _STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)
    # Duplicate removal; see remove_duplicates.
    _SAME_BOX_IOU = 0.7
    _PART_IOS = 0.8

    def __init__(self, model_dir: Path) -> None:
        model_path = model_dir / "model.onnx"
        meta_path = model_dir / "classes.json"
        if not model_path.is_file() or not meta_path.is_file():
            raise DetectorError(f"detector model not found in {model_dir}")

        meta = json.loads(meta_path.read_text())
        self.names: dict[int, str] = {int(k): v for k, v in meta["classes"].items()}
        self.label = f"rf-detr-{meta.get('variant', 'unknown')}"

        # Name only the providers we want. If ONNX Runtime fails to load one in
        # a list, it skips the rest of the list and falls back to the CPU.
        # EXHAUSTIVE, the default search, timed every cuDNN algorithm and cost
        # about a minute at start on the Orin, for the same steady speed.
        available = set(ort.get_available_providers())
        wanted = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}), ("CPUExecutionProvider", {})]
        providers = [p for p in wanted if p[0] in available]
        logger.info("loading %s", model_path)
        self._session = ort.InferenceSession(str(model_path), providers=providers)
        self.provider = self._session.get_providers()[0]
        if self.provider == "CPUExecutionProvider":
            logger.warning("detector is on the CPU; expect a few frames per second at best")
        logger.info("detector %s on %s", self.label, self.provider)

        model_input = self._session.get_inputs()[0]
        self._input_name = model_input.name
        self._side = int(model_input.shape[-1])
        self._output_names = [o.name for o in self._session.get_outputs()]
        if set(self._output_names) >= {"dets", "labels"}:
            self._output_names = ["dets", "labels"]

        # The first run allocates GPU memory. Do it here, not on the first frame.
        started = time.perf_counter()
        self._session.run(self._output_names, {self._input_name: np.zeros((1, 3, self._side, self._side), np.float32)})
        logger.info("detector warm in %.1f s", time.perf_counter() - started)

    def detect(self, image_bgr: np.ndarray, min_confidence: float) -> sv.Detections:
        height, width = image_bgr.shape[:2]
        outputs = self._session.run(self._output_names, {self._input_name: self.preprocess(image_bgr, self._side)})
        return self.decode(outputs[0][0], outputs[1][0], width, height, min_confidence)

    @classmethod
    def preprocess(cls, image_bgr: np.ndarray, side: int) -> np.ndarray:
        """BGR frame to the model's (1, 3, side, side) normalised RGB tensor."""
        # One C pass for resize, BGR to RGB and scale. The NumPy version took 10 ms a frame.
        blob = cv2.dnn.blobFromImage(image_bgr, 1.0 / 255.0, (side, side), swapRB=True, crop=False)
        blob -= cls._MEAN
        blob /= cls._STD
        return blob

    @classmethod
    def decode(
        cls,
        boxes: np.ndarray,
        logits: np.ndarray,
        width: int,
        height: int,
        min_confidence: float,
        class_ids: np.ndarray | None = None,
        max_detections: int = 100,
    ) -> sv.Detections:
        """RF-DETR raw output to frame-pixel boxes, one box for each object.

        boxes is (queries, 4) as normalised centre x, centre y, width, height.
        logits is (queries, classes). One query may score for several classes, so
        the top-k runs over queries and classes together, as rfdetr itself does.
        """
        if class_ids is not None:
            if len(class_ids) == 0:
                return sv.Detections.empty()
            logits = logits[:, class_ids]
        scores = 1.0 / (1.0 + np.exp(-logits))
        flat = scores.reshape(-1)
        keep = np.flatnonzero(flat >= min_confidence)
        if keep.size == 0:
            return sv.Detections.empty()
        keep_scores = keep[np.argsort(-flat[keep])][:max_detections]

        query, column = np.divmod(keep_scores, scores.shape[1])
        cx, cy, bw, bh = boxes[query].T
        xyxy = np.stack(
            [(cx - bw / 2) * width, (cy - bh / 2) * height, (cx + bw / 2) * width, (cy + bh / 2) * height],
            axis=1,
        )
        xyxy = np.clip(xyxy, 0, [width, height, width, height]).astype(np.float32)
        ids = class_ids[column] if class_ids is not None else column
        found = sv.Detections(xyxy=xyxy, confidence=flat[keep_scores].astype(np.float32), class_id=ids.astype(np.int64))
        return cls.remove_duplicates(found)

    @classmethod
    def remove_duplicates(cls, found: sv.Detections) -> sv.Detections:
        """One box for each object, with supervision's NMS.

        RF-DETR's decode, like DETR's, ranks every query and class pair and applies
        no NMS, so one object can come back twice. Measured on the road, a parked
        SUV had a whole-car box and a cabin box in 95 % of frames, and ByteTrack
        made them two objects. So, in two passes:
        1. IoU over 0.7, any class: the same box under another class, as in standard NMS.
        2. Intersection over the smaller box over 0.8, same class only: a part of an
           object boxed again, SAHI's IOS match. Same class only, because a person
           in front of a car also lies inside the car's box.
        """
        if len(found) < 2:
            return found
        found = found.with_nms(threshold=cls._SAME_BOX_IOU, class_agnostic=True)
        return found.with_nms(threshold=cls._PART_IOS, overlap_metric=sv.OverlapMetric.IOS)
