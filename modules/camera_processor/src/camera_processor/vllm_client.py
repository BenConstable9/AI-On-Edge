"""Asks the local vLLM server about one image.

Images reach vLLM through a tmpfs shared by both containers, as a file:// URL,
rather than as base64 in the request. That saves a third of the bytes and a JSON
encode and decode on each side. The server allows it with
--allowed-local-media-path.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests

logger = logging.getLogger(__name__)


class VLLMError(RuntimeError):
    """vLLM refused or failed a request."""


class VLLMClient:
    def __init__(self, base_url: str, model: str, shm_dir: Path, timeout_s: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._shm = shm_dir
        self._timeout = timeout_s
        self._session = requests.Session()
        self._last_sweep = 0.0
        self._shm.mkdir(parents=True, exist_ok=True)

    def is_ready(self) -> bool:
        try:
            return self._session.get(f"{self._base_url}/health", timeout=5).ok
        except requests.RequestException:
            return False

    def describe(
        self, image: np.ndarray, prompt: str, max_tokens: int, schema: dict[str, Any] | None = None
    ) -> tuple[str, float]:
        """One question about one image. Returns the answer and the seconds it took.

        With a JSON schema, vLLM constrains the output to it, so the answer always parses.
        """
        path = self._shm / f"{uuid.uuid4().hex}.jpg"
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise VLLMError(f"could not write {path}")
        try:
            body = {
                "model": self._model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"file://{path}"}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            }
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "verdict", "schema": schema}}
            started = time.perf_counter()
            try:
                response = self._session.post(f"{self._base_url}/v1/chat/completions", json=body, timeout=self._timeout)
            except requests.RequestException as exc:
                raise VLLMError(f"no answer from vLLM: {exc}") from exc
            elapsed = time.perf_counter() - started
            if response.status_code >= 400:
                raise VLLMError(f"vLLM returned {response.status_code}: {response.text[:300]}")
            return response.json()["choices"][0]["message"]["content"] or "", elapsed
        finally:
            path.unlink(missing_ok=True)
            self._sweep()

    def _sweep(self, max_age_s: float = 600.0) -> None:
        """Delete files left by a crash, or the tmpfs fills."""
        now = time.time()
        if now - self._last_sweep < 60:
            return
        self._last_sweep = now
        for leftover in self._shm.glob("*.jpg"):
            try:
                if now - leftover.stat().st_mtime > max_age_s:
                    leftover.unlink(missing_ok=True)
            except OSError:
                continue
