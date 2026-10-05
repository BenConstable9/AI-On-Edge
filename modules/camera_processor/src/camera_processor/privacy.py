"""Hides people before any frame leaves the device."""

from __future__ import annotations

import cv2
import numpy as np
import supervision as sv


class PrivacyFilter:
    """Pixelates the hidden classes, such as person, on the preview.

    Pixelation rather than blur: a light blur can be partly reversed. The block
    size follows the box, not the frame: a fixed size left a face near the camera
    about 16 blocks wide, which is enough to recognise it.
    """

    _BLOCKS = 16  # Along the longer side of a box. A face is at most about a third of a person box, so 4 or 5 blocks
    _MIN_BLOCK_PX = 6

    def __init__(self, labels: tuple[str, ...], names: dict[int, str]) -> None:
        """labels: the class names to hide. names: the detector's class id to name map."""
        self._labels = frozenset(labels)
        self._class_ids = np.array([i for i, n in names.items() if n in self._labels], np.int64)

    def apply(
        self, canvas: np.ndarray, scale: float, found: sv.Detections, tracks: list[tuple[str, np.ndarray]]
    ) -> np.ndarray:
        """A copy of the preview with every hidden object pixelated.

        found: this frame's raw detections, weak ones too, not only tracks: a new
        track is not confirmed for a frame or two, and a face must not leak in that
        gap. tracks: (label, box) in frame pixels. A track the VLM named a person
        has no person detection, so it is pixelated by its label.
        """
        boxes = found.xyxy[np.isin(found.class_id, self._class_ids)]
        named = [box for label, box in tracks if label in self._labels]
        if named:
            boxes = np.vstack([boxes, np.array(named, np.float32)])
        return self._pixelate(canvas, boxes * scale)

    @classmethod
    def _pixelate(cls, image: np.ndarray, boxes: np.ndarray) -> np.ndarray:
        """A copy with each box reduced to about `_BLOCKS` blocks along its longer side."""
        out = image.copy()
        height, width = out.shape[:2]
        for x1, y1, x2, y2 in boxes.astype(int):
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            block_px = max(cls._MIN_BLOCK_PX, max(x2 - x1, y2 - y1) // cls._BLOCKS)
            region = out[y1:y2, x1:x2]
            small = cv2.resize(
                region,
                (max(1, (x2 - x1) // block_px), max(1, (y2 - y1) // block_px)),
                interpolation=cv2.INTER_AREA,
            )
            out[y1:y2, x1:x2] = cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
        return out
