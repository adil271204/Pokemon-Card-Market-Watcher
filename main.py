"""Entry point – run one monitoring cycle.

This file is called by the Render Cron Job every 10 minutes.
It can also be executed locally:

    python main.py
"""

import logging
import sys

from src import config
from src.watcher import Watcher

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Pokemon Card Market Watcher starting.")
    try:
        watcher = Watcher()
        watcher.run()
        logger.info("Watcher cycle complete.")
    except Exception:
        logger.exception("Unhandled error during watcher cycle.")
        sys.exit(1)


if __name__ == "__main__":
    main()
