"""CLI entry point.

Usage:
    python -m grizzly                       # acquire a number, then watch for its SMS
    python -m grizzly watch <id> [<id> ...] # watch already-owned activations only
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from .bot import Watcher, run
from .config import Config
from .notify import Notifier

LOG = logging.getLogger("grizzly")


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
    )


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    _setup_logging()

    watch_mode = bool(argv) and argv[0] == "watch"
    try:
        config = Config.for_watch() if watch_mode else Config.from_env()
        notifier = Notifier.from_config(config)
    except ValueError as error:
        LOG.error("startup failed: %s", error)
        return 2

    shutdown = threading.Event()

    def _shutdown(_signal: int, _frame: object) -> None:
        LOG.info("shutdown requested")
        shutdown.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        if watch_mode:
            activation_ids = argv[1:]
            if not activation_ids:
                LOG.error("usage: python -m grizzly watch <activation_id> [<activation_id> ...]")
                return 2
            watcher = Watcher(config, notifier)
            try:
                watcher.watch(activation_ids, shutdown)
            finally:
                watcher.close()
            return 0
        return run(config, notifier, shutdown)
    finally:
        notifier.close()


if __name__ == "__main__":
    raise SystemExit(main())
