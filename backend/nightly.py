"""Run the Dominican 23:00 updater independently of web worker lifetimes."""
import logging
import signal
import threading

from . import sync
from .deployment import ensure_database


def main():
    logging.basicConfig(level=logging.INFO)
    ensure_database()
    sync.initialize()
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    logging.info("Dominican results updater: daily 23:00 America/Santo_Domingo; pending results retried every 15 minutes")
    sync.scheduler(stop)


if __name__ == "__main__":
    main()
