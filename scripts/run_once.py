"""
Manually trigger one watcher cycle without starting the continuous loop.

    python scripts/run_once.py
"""

import sys

sys.path.insert(0, ".")
sys.argv.append("--once")

import main  # noqa: E402

main.main()
