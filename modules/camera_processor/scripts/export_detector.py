"""Export RF-DETR to ONNX, with the class names beside it.

Runs in the first stage of the camera-processor image build, so the device image
carries the ONNX file and none of the PyTorch model code. Run it by hand to try a
different size:

    python scripts/export_detector.py --variant medium --output ./detector
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

logger = logging.getLogger("export_detector")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=("nano", "small", "medium", "large"), default="small")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import rfdetr
    from rfdetr.assets.coco_classes import COCO_CLASSES

    model = getattr(rfdetr, f"RFDETR{args.variant.capitalize()}")()
    resolution = int(model.model_config.resolution)

    with tempfile.TemporaryDirectory() as scratch:
        exported = Path(model.export(output_dir=scratch, verbose=False))
        if exported.is_dir():
            candidates = sorted(exported.glob("*.onnx"))
            if len(candidates) != 1:
                logger.error("expected one ONNX file in %s, found %s", exported, candidates)
                return 1
            exported = candidates[0]
        args.output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(exported, args.output / "model.onnx")

    meta = {
        "variant": args.variant,
        "resolution": resolution,
        "rfdetr": version("rfdetr"),
        "classes": {str(k): v for k, v in COCO_CLASSES.items()},
    }
    (args.output / "classes.json").write_text(json.dumps(meta, indent=2))
    size_mb = (args.output / "model.onnx").stat().st_size / 1e6
    logger.info("exported rf-detr-%s at %d px, %.0f MB, to %s", args.variant, resolution, size_mb, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
