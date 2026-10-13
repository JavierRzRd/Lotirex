"""Build a Dominican-only cPanel ZIP, retaining the already compiled frontend."""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import db, sync
from backend.backup import snapshot_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/lotirex-cpanel.zip')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Database must exist; no empty snapshot is created')
    with db.connect(args.database) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            parser.error('Database integrity check failed')
        if connection.execute("SELECT COUNT(*) FROM lotteries WHERE country != 'DO'").fetchone()[0]:
            parser.error('Database must contain only Dominican lotteries')
        count = connection.execute('SELECT COUNT(*) FROM draws').fetchone()[0]
    # The deployment branch has no frontend source: reuse its compiled assets.
    with zipfile.ZipFile(ROOT / 'lotirex-cpanel.zip') as previous:
        files = {name: previous.read(name) for name in previous.namelist()
                 if name.startswith('dist/') or name in ('data/import-report.json', 'data/backfill-report.json')}
    for directory in ('backend', 'docs', 'scripts', 'tests'):
        for path in (ROOT / directory).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ('requirements-cpanel.txt', 'passenger_wsgi.py', 'seed/domestic-results.json'):
        files[name] = (ROOT / name).read_bytes()
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / 'seed.sqlite3'
        snapshot_database(args.database, snapshot)
        files['seed/lotirex.sqlite3'] = snapshot.read_bytes()
    manifest = {
        'built_at': datetime.now(timezone.utc).isoformat(), 'python': '3.12',
        'startup_file': 'passenger_wsgi.py', 'entry_point': 'application',
        'seed': 'seed/lotirex.sqlite3', 'live_database': 'data/lotirex.sqlite3',
        'country': 'DO', 'draws': count,
        'updates': '23:00 America/Santo_Domingo, via cPanel cron every 15 minutes; retry pending dates',
        'files': {name: hashlib.sha256(value).hexdigest() for name, value in sorted(files.items())},
    }
    files['deployment-manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode() + b'\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=args.output.parent, suffix='.zip')
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, value in sorted(files.items()):
                archive.writestr(name, value)
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(json.dumps({'package': str(args.output), 'draws': count,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
