"""Entry point – runs as a long-running background worker.

Each cycle searches eBay, scores deals, and sends Telegram alerts.
Then sleeps for INTERVAL_SECONDS before the next cycle.

Local one-shot run:
    python main.py --once

Continuous (as deployed on Render):
    python main.py
"""

import logging
import sys
import time

from src import config
from src.watcher import Watcher

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 600  # 10 minutes


def run_once(watcher: Watcher) -> None:
    logger.info("Starting watcher cycle.")
    try:
        watcher.run()
        logger.info("Watcher cycle complete.")
    except Exception:
        logger.exception("Unhandled error during watcher cycle – will retry next interval.")


def main() -> None:
    one_shot = "--once" in sys.argv

    logger.info("Pokemon Card Market Watcher starting (mode=%s).", "once" if one_shot else "loop")
    watcher = Watcher()

    if one_shot:
        run_once(watcher)
        return

    # Continuous loop for Render Background Worker
    while True:
        run_once(watcher)
        logger.info("Sleeping %d seconds until next cycle.", INTERVAL_SECONDS)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
