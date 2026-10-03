from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from .config import ConfigError, Settings
from .server import ViewerServer

logger = logging.getLogger("viewer")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", stream=sys.stdout
    )
    parser = argparse.ArgumentParser(prog="python -m viewer", description="Show the device's preview and counts.")
    parser.add_argument("--device", help="device address, for example 192.168.0.143 (default: VIEWER_DEVICE)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    try:
        settings = Settings.from_env(args.device)
    except ConfigError as exc:
        logger.error("%s", exc)
        return 2

    try:
        server = ViewerServer(settings)
    except OSError as exc:
        logger.error("cannot listen on %s:%d: %s", settings.host, settings.port, exc)
        return 1

    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve()
    except KeyboardInterrupt:
        logger.info("stopped")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
