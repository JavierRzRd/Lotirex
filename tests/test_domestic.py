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


if __name__ == '__main__':
    unittest.main()
