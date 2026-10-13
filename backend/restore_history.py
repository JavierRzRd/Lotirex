"""Back up and merge Dominican seed history without replacing accepted draws."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from . import db, sync
from .backup import snapshot_database


def restore(seed, dry_run=False):
    seed = Path(seed).resolve()
    if seed == db.DB_PATH.resolve():
        raise ValueError('Seed and live database must be different files')
    with closing(sqlite3.connect(seed.as_uri() + '?mode=ro', uri=True)) as original:
        original.row_factory = sqlite3.Row
        if original.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Seed integrity check failed')
        records = [dict(row) for row in original.execute(
            "SELECT l.company,l.name,d.date,d.numbers,d.source,d.source_url "
            "FROM draws d JOIN lotteries l ON l.id=d.lottery_id WHERE l.country='DO' ORDER BY d.date,l.name")]
    # Validate before taking any action on the live file.
    for record in records:
        record['numbers'] = json.loads(record['numbers'])
        db.validate_draw(record['company'], record['name'], record['date'], record['numbers'], country='DO')
    result = {'database': str(db.DB_PATH), 'seed_draws': len(records), 'inserted': 0,
              'conflicts': 0, 'backup': None, 'dry_run': dry_run}
    if dry_run:
        return result
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    if db.DB_PATH.is_file():
        backup = db.DB_PATH.parent.parent / 'backups' / f'pre-history-merge-{stamp}.sqlite3'
        snapshot_database(db.DB_PATH, backup)
        result['backup'] = str(backup)
    sync.initialize()
    with db.connect() as connection:
        for record in records:
            try:
                result['inserted'] += int(db.upsert_draw(connection, record['company'], record['name'],
                    record['date'], record['numbers'], record['source'], record['source_url'], country='DO'))
            except db.ConflictingDrawError:
                result['conflicts'] += 1
                issue = {**record, 'lottery': record['name'], 'reason': 'conflicting_draw_for_date'}
                connection.execute('INSERT INTO source_issues(date,lottery,source_url,reason,payload,observed_at) '
                    'VALUES(?,?,?,?,?,?) ON CONFLICT(date,lottery,source_url,reason) DO UPDATE '
                    'SET payload=excluded.payload,observed_at=excluded.observed_at',
                    (record['date'],record['name'],record['source_url'] or 'seed:lotirex.sqlite3',
                     issue['reason'],json.dumps(issue,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, default=db.ROOT / 'seed' / 'lotirex.sqlite3')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(restore(args.seed, args.dry_run)))


if __name__ == '__main__':
    main()
