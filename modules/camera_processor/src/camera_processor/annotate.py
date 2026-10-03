"""Draws tracks on the preview.

The module draws on the preview-sized frame, not the camera frame, so line
widths and labels are the same size on screen whatever the camera resolution.
Timings and counts are not drawn: the viewer shows them from /state.
"""

from __future__ import annotations

import colorsys
import zlib

import cv2
import numpy as np


class Annotator:
    _FONT = cv2.FONT_HERSHEY_SIMPLEX

    def __init__(self, width: int, jpeg_quality: int) -> None:
        """width: the largest preview width in pixels."""
        self._width = width
        self._quality = jpeg_quality

    def downscale(self, image: np.ndarray) -> tuple[np.ndarray, float]:
        """The frame at most the preview width, and the scale applied to it."""
        if image.shape[1] <= self._width:
            return image, 1.0
        scale = self._width / image.shape[1]
        height = int(round(image.shape[0] * scale))
        return cv2.resize(image, (self._width, height), interpolation=cv2.INTER_AREA), scale

    def draw_tracks(self, canvas: np.ndarray, tracks: list[tuple[int, str, np.ndarray, bool]]) -> None:
        """tracks: (id, label, box, relabelled by the VLM). Descriptions stay in the viewer:
        on the picture, long ones cover the objects they describe."""
        for track_id, label, box, relabelled in tracks:
            colour = self.colour_for(label)
            x1, y1, x2, y2 = box.astype(int)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 2)
            self._label(canvas, f"#{track_id} {label}" + ("*" if relabelled else ""), x1, y1, colour)

    def highlight(self, canvas: np.ndarray, box: np.ndarray) -> np.ndarray:
        """A copy of the canvas with one box picked out, for an event frame."""
        marked = canvas.copy()
        x1, y1, x2, y2 = box.astype(int)
        cv2.rectangle(marked, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (0, 0, 0), 5)
        cv2.rectangle(marked, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), (255, 255, 255), 2)
        return marked

    def to_jpeg(self, image: np.ndarray) -> bytes:
        ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not ok:
            raise RuntimeError("could not encode the preview JPEG")
        return buffer.tobytes()

    @staticmethod
    def colour_for(label: str) -> tuple[int, int, int]:
        """Stable per-label colour. zlib.crc32 because str hash() changes per process."""
        hue = (zlib.crc32(label.encode()) % 360) / 360.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
        return int(b * 255), int(g * 255), int(r * 255)

    def _label(self, canvas: np.ndarray, text: str, x: int, y: int, colour: tuple[int, int, int]) -> None:
        (w, h), base = cv2.getTextSize(text, self._FONT, 0.55, 1)
        top = max(0, y - h - base - 6)
        cv2.rectangle(canvas, (x, top), (x + w + 8, top + h + base + 6), colour, cv2.FILLED)
        cv2.putText(canvas, text, (x + 4, top + h + 3), self._FONT, 0.55, (20, 20, 20), 1, cv2.LINE_AA)
