"""Install a bundled seed once, preserving the production database on upgrades."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

from . import db
from .backup import snapshot_database


def ensure_database(seed_path: Path | None = None) -> bool:
    """Return True only when a seed was installed into a missing live database.

    Local development has no seed and continues to use the normal importer.
    WSGI workers and cron jobs share this lock before checking the live path.
    """
    seed = Path(seed_path) if seed_path is not None else db.ROOT / "seed" / "lotirex.sqlite3"
    if not seed.is_file():
        return False
    destination = Path(db.DB_PATH)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination.parent / ".seed.lock", flags, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            # Even an empty or older existing database belongs to the deployment.
            # Repair/replacement requires a separate deliberate operator action.
            if os.path.lexists(destination):
                return False
            snapshot_database(seed, destination)
            return True
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
