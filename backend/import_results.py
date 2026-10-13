"""Merge packaged Dominican results without replacing an existing live database."""
import argparse
import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import db


def apply_bundle(connection, path=None):
    """Serialize imports across Passenger workers before checking bundle markers."""
    database = connection.execute('PRAGMA database_list').fetchone()['file']
    if not database:
        return _apply_bundle(connection, path)
    lock_path = Path(database).with_suffix('.bundle.lock')
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, 'a+') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _apply_bundle(connection, path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _apply_bundle(connection, path=None):
    path = Path(path) if path else db.ROOT / 'seed' / 'domestic-results.json'
    if not path.is_file():
        return {'inserted': 0, 'conflicts': 0}
    contents = path.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    payload = json.loads(contents)
    if payload.get('country') != 'DO' or not isinstance(payload.get('results'), list):
        raise ValueError('Invalid Dominican results bundle')
    history = payload.get('kind') == 'history'
    connection.execute('CREATE TABLE IF NOT EXISTS installed_result_bundles '
                       '(sha256 TEXT PRIMARY KEY, installed_at TEXT NOT NULL)')
    if connection.execute('SELECT 1 FROM installed_result_bundles WHERE sha256=?', (digest,)).fetchone():
        return {'inserted': 0, 'conflicts': 0}
    # Validate the entire input before recording any result or import marker.
    for record in payload['results']:
        source = record.get('source', '')
        workbook = history and source.startswith('uploaded_workbook')
        if not workbook and (source != 'website' or not (record.get('source_url') or '').startswith('https://')):
            raise ValueError('Bundle requires website source provenance')
        db.validate_draw(record['company'], record['lottery'], record['date'],
                         record['numbers'], country='DO')
    if history:
        database = connection.execute('PRAGMA database_list').fetchone()['file']
        if database:
            from .backup import snapshot_database
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            destination = Path(database).parent.parent / 'backups' / f'pre-bundled-history-{stamp}.sqlite3'
            snapshot_database(Path(database), destination)
    inserted = conflicts = 0
    stamp = datetime.now(timezone.utc).isoformat()
    with connection:
        for record in payload['results']:
            try:
                inserted += int(db.upsert_draw(connection, record['company'], record['lottery'],
                                               record['date'], record['numbers'], record['source'],
                                               record.get('source_url'), country='DO'))
            except db.ConflictingDrawError:
                conflicts += 1
                issue = {**record, 'reason': 'conflicting_draw_for_date'}
                connection.execute('INSERT INTO source_issues(date,lottery,source_url,reason,payload,observed_at) '
                                   'VALUES(?,?,?,?,?,?) ON CONFLICT(date,lottery,source_url,reason) DO UPDATE '
                                   'SET payload=excluded.payload,observed_at=excluded.observed_at',
                                   (record['date'], record['lottery'], record.get('source_url') or 'seed:domestic-history.json', issue['reason'],
                                    json.dumps(issue, ensure_ascii=False), stamp))
        connection.execute('INSERT INTO installed_result_bundles VALUES(?,?)', (digest, stamp))
    return {'inserted': inserted, 'conflicts': conflicts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    from . import sync
    sync.initialize()
    with db.connect() as connection:
        print(json.dumps(apply_bundle(connection, args.file)))


if __name__ == '__main__':
    main()
