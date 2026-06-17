"""
Manually trigger one watcher cycle (identical to main.py).

Useful for local testing without relying on the cron schedule.

    python scripts/run_once.py
"""

import sys

sys.path.insert(0, ".")

import main  # noqa: E402

main.main()
