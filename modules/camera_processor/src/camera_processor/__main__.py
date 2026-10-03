from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from .config import Settings
from .detector import DetectorError
from .pipeline import Pipeline

logger = logging.getLogger("camera_processor")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # The IoT SDK logs every connection step at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("paho").setLevel(logging.WARNING)
    try:
        pipeline = Pipeline(Settings.from_env())
    except (DetectorError, ValueError) as exc:
        logger.error("cannot start: %s", exc)
        return 2

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        logger.info("shutdown requested")
        pipeline.stop()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    return pipeline.run()


if __name__ == "__main__":
    raise SystemExit(main())
