import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from backend import db, sync
from backend.source import DAILY_LOTTERIES, SourceDraws, parse_results


class DomesticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        self.old_root = db.ROOT
        db.ROOT = Path(self.directory.name)
        db.DB_PATH = Path(self.directory.name) / 'data' / 'test.sqlite3'
        sync.initialize()

    def tearDown(self):
        db.DB_PATH = self.old_path
        db.ROOT = self.old_root
        self.directory.cleanup()

    def record(self, day, numbers):
        return dict(company='Leidsa', lottery='Quiniela Leidsa', date=day,
                    numbers=numbers, country='DO', source_url='https://www.loteriadominicana.com.do/')

    def test_foreign_games_cannot_enter_storage(self):
        for game in ('New York 11:30', 'Florida Día', 'PowerBall', 'Mega Millions',
                     'Anguila 6:00 PM', 'King Lottery 7:30'):
            with self.subTest(game=game), self.assertRaises(ValueError):
                db.game_spec('source', game)
        with self.assertRaises(ValueError):
            db.game_spec('Leidsa', 'Quiniela Leidsa', 'US')

    def test_migration_backs_up_foreign_draws_and_preserves_domestic(self):
        with db.connect() as c:
            db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', '2026-03-11', [1, 2, 3], 'website')
            c.execute("INSERT INTO lotteries VALUES('foreign','Florida','Florida Día','US',3,0,99,'quiniela','[]')")
            c.execute("INSERT INTO draws(lottery_id,date,numbers,source,rules) VALUES('foreign','2026-03-11','[4,5,6]','website','[]')")
        sync.initialize()
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM lotteries').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT numbers FROM draws').fetchone()[0], '[1, 2, 3]')
        backups = list((Path(self.directory.name) / 'backups').glob('pre-dominican-only-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with db.connect(backups[0]) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM draws').fetchone()[0], 2)
        sync.initialize()
        self.assertEqual(len(list(backups[0].parent.glob('*.sqlite3'))), 1)

    def test_parser_excludes_foreign_cards_and_pending_games(self):
        html = ''.join(f'<div data-company="source" data-lottery="{game}" '
                       'data-date="2026-03-11" data-numbers="01 02 03"></div>'
                       for game in ('Quiniela Leidsa', 'Florida Día', 'New York 11:30'))
        results = parse_results(html, 'https://www.loteriadominicana.com.do/', '2026-03-11')
        self.assertEqual([r['lottery'] for r in results], ['Quiniela Leidsa'])
        self.assertFalse(any('Florida' in n or 'New York' in n for n in DAILY_LOTTERIES))

    def test_conflict_is_audited_without_blocking_next_date(self):
        with db.connect() as c:
            db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', '2026-03-11', [1,2,3], 'uploaded_workbook_unverified')
        def fetch(day):
            return SourceDraws([self.record(day.isoformat(), [4,5,6])])
        with patch.object(sync, 'scrape_day', side_effect=fetch):
            result = sync.run_sync(date(2026,3,11), date(2026,3,12), 2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['inserted'], 1)
        with db.connect() as c:
            self.assertEqual(json.loads(c.execute("SELECT numbers FROM draws WHERE date='2026-03-11'").fetchone()[0]), [1,2,3])
            self.assertEqual(c.execute('SELECT reason FROM source_issues').fetchone()[0], 'conflicting_draw_for_date')
            self.assertEqual(c.execute("SELECT status FROM sync_days WHERE date='2026-03-12'").fetchone()[0], 'ok')

    def test_schedule_is_2300_dominican_time(self):
        before = datetime(2026,10,13,22,59,tzinfo=sync.ZONE)
        due = sync.next_run(before)
        self.assertEqual(due.isoformat(), '2026-10-13T23:00:00-04:00')
        self.assertEqual(sync.next_run(due).isoformat(), '2026-10-14T23:00:00-04:00')

    def test_current_day_is_not_fetched_before_2300(self):
        for hour, expected in ((22, date(2026,10,12)), (23, date(2026,10,13))):
            with self.subTest(hour=hour), patch.object(sync, 'now', return_value=datetime(2026,10,13,hour,tzinfo=sync.ZONE)), patch.object(sync, 'pending_days', return_value=[]) as pending:
                self.assertEqual(sync.run_sync()['status'], 'ok')
                self.assertEqual(pending.call_args.args[0], expected)

    def test_bundled_results_merge_once_and_preserve_conflicts(self):
        from backend.import_results import apply_bundle
        with db.connect() as c:
            db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', '2026-03-11', [1,2,3], 'website')
        records = [dict(self.record(day, [4,5,6]), source='website')
                   for day in ('2026-03-11', '2026-03-12')]
        path = Path(self.directory.name) / 'bundle.json'
        path.write_text(json.dumps({'country':'DO', 'results':records}))
        with db.connect() as c:
            self.assertEqual(apply_bundle(c, path), {'inserted':1, 'conflicts':1})
            self.assertEqual(apply_bundle(c, path), {'inserted':0, 'conflicts':0})
            self.assertEqual(json.loads(c.execute("SELECT numbers FROM draws WHERE date='2026-03-11'").fetchone()[0]), [1,2,3])
        from backend.app import data_quality
        self.assertFalse(data_quality()['source_issues'][0]['resolved'])

    def test_bad_bundle_does_not_partially_import(self):
        from backend.import_results import apply_bundle
        records = [dict(self.record('2026-03-11', [4,5,6]), source='website'),
                   dict(self.record('2026-03-12', [4,5,6]), source='website', lottery='Florida Día')]
        path = Path(self.directory.name) / 'bundle.json'
        path.write_text(json.dumps({'country':'DO', 'results':records}))
        with db.connect() as c:
            with self.assertRaises(ValueError):
                apply_bundle(c, path)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM draws').fetchone()[0], 0)

    def test_missing_historical_page_does_not_block_newer_results(self):
        from backend.source import UnsupportedPageError
        def fetch(day):
            if day == date(2026,3,11):
                raise UnsupportedPageError('No historical cards')
            return SourceDraws([self.record(day.isoformat(), [4,5,6])])
        with patch.object(sync, 'scrape_day', side_effect=fetch):
            result = sync.run_sync(date(2026,3,11), date(2026,3,12), 2)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['inserted'], 1)
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT date FROM draws').fetchone()[0], '2026-03-12')

    def test_catalog_and_data_quality_work_without_sqlite_json_functions(self):
        import sqlite3
        from backend.app import catalog, data_quality
        with db.connect() as c:
            db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', '2026-03-11', [1,2,3], 'website')
            issue = self.record('2026-03-11', [4,5,6])
            c.execute('INSERT INTO source_issues(date,lottery,source_url,reason,payload,observed_at) VALUES(?,?,?,?,?,?)',
                      ('2026-03-11','Quiniela Leidsa',issue['source_url'],'conflicting_draw_for_date',json.dumps(issue),'2026-03-12T00:00:00+00:00'))
        connection = db.connect()
        def authorize(action, arg1, arg2, *_):
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower().startswith('json'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        try:
            with patch.object(db, 'connect', return_value=connection):
                result = catalog()
                self.assertEqual(result['summary']['draws'], 1)
                self.assertEqual(result['summary']['source_issues'], 1)
                self.assertFalse(data_quality()['source_issues'][0]['resolved'])
        finally:
            connection.close()

    def test_history_merge_backs_up_and_preserves_existing_draws(self):
        from backend.restore_history import restore
        seed = Path(self.directory.name) / 'seed.sqlite3'
        with db.connect(seed) as c:
            db.initialize(c)
            for day in ('2026-03-11', '2026-03-12'):
                db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', day, [4,5,6], 'website')
        with db.connect() as c:
            db.upsert_draw(c, 'Leidsa', 'Quiniela Leidsa', '2026-03-11', [1,2,3], 'website')
        dry = restore(seed, True)
        self.assertEqual(dry['seed_draws'], 2)
        self.assertIsNone(dry['backup'])
        result = restore(seed)
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['conflicts'], 1)
        self.assertTrue(Path(result['backup']).is_file())
        with db.connect() as c:
            self.assertEqual(json.loads(c.execute("SELECT numbers FROM draws WHERE date='2026-03-11'").fetchone()[0]), [1,2,3])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM draws').fetchone()[0], 2)
        self.assertEqual(restore(seed)['inserted'], 0)

    def test_history_bundle_automatically_merges_once_with_original_provenance(self):
        root = Path(self.directory.name)
        (root / 'seed').mkdir()
        record = dict(self.record('2026-03-11', [4,5,6]),
                      source='uploaded_workbook_unverified', source_url=None)
        (root / 'seed' / 'domestic-history.json').write_text(json.dumps(
            {'country':'DO', 'kind':'history', 'results':[record]}))
        sync.initialize()
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT source FROM draws').fetchone()[0], 'uploaded_workbook_unverified')
        self.assertEqual(len(list((root / 'backups').glob('pre-bundled-history-*.sqlite3'))), 1)

        sync.initialize()
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM draws').fetchone()[0], 1)
        self.assertEqual(len(list((root / 'backups').glob('pre-bundled-history-*.sqlite3'))), 1)

    def test_concurrent_workers_import_history_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from backend.import_results import apply_bundle
        path = Path(self.directory.name) / 'history.json'
        record = dict(self.record('2026-03-11', [4,5,6]), source='uploaded_workbook_unverified', source_url=None)
        path.write_text(json.dumps({'country':'DO','kind':'history','results':[record]}))
        def run(_):
            with db.connect() as c:
                return apply_bundle(c, path)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(run, range(2)))
        self.assertEqual(sum(r['inserted'] for r in results), 1)
        with db.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM installed_result_bundles').fetchone()[0], 1)
        self.assertEqual(len(list((Path(self.directory.name) / 'backups').glob('pre-bundled-history-*.sqlite3'))), 1)


if __name__ == '__main__':
    unittest.main()
