"""Create a private, consistent SQLite snapshot without replacing any file."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from . import db


def snapshot_database(source: Path, destination: Path) -> Path:
    """Include committed WAL transactions and publish the snapshot exclusively.

    The temporary database lives beside the destination so publishing it with a
    hard link is atomic. A concurrent writer of the destination causes an error;
    neither an existing snapshot nor a live database can be overwritten.
    """
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if not source.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source}")
    if os.path.lexists(destination):
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        # mode=ro prevents a disappeared/mistyped source from creating a new DB.
        # Do not use immutable=1: that would ignore live WAL transactions.
        source_uri = source.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as snapshot:
                original.backup(snapshot)
                # Ship a standalone file: copied WAL mode would create sidecars
                # when the snapshot is opened, even though the backup is complete.
                snapshot.execute("PRAGMA journal_mode=DELETE")
        # mkstemp creates mode 0600; retain it before publishing the final name.
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as completed:
            os.fsync(completed.fileno())
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New snapshot path (must not exist)")
    args = parser.parse_args(argv)
    output = args.output or db.ROOT / "backups" / (
        "lotirex-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".sqlite3"
    )
    try:
        path = snapshot_database(db.DB_PATH, output)
    except (OSError, sqlite3.Error) as error:
        print(f"Database backup failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"path": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
