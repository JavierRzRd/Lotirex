"""Persistent, idempotent catch-up and nightly results ingestion.

Run one application worker. The process-wide file lock also prevents overlapping
manual backfills. Source failures never become successful empty days.
"""
import argparse
import fcntl
import json
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db
from .source import SourceAccessError, SourceError, scrape_day

LOG = logging.getLogger(__name__)
ZONE = ZoneInfo("America/Santo_Domingo")


def now():
    return datetime.now(ZONE)


def next_run(reference=None):
    reference = (reference or now()).astimezone(ZONE)
    target = datetime.combine(reference.date(), time(23), ZONE)
    return target if reference < target else target + timedelta(days=1)


def initialize():
    db.initialize()
    with db.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS sync_days(
            date TEXT PRIMARY KEY, status TEXT NOT NULL, checked_at TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '')""")
        connection.execute("""CREATE TABLE IF NOT EXISTS source_issues(
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, lottery TEXT NOT NULL,
            source_url TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL,
            observed_at TEXT NOT NULL, UNIQUE(date,lottery,source_url,reason))""")
        allowed = sorted({spec.name for spec in db._SPECS.values()})
        connection.execute("DELETE FROM source_issues WHERE lottery NOT IN (" +
                           ",".join("?" for _ in allowed) + ")", allowed)
    from .import_results import apply_bundle
    with db.connect() as connection:
        apply_bundle(connection)



def pending_days(end, start=None, limit=400, *, prioritize_recent=False, retry_due=False):
    """Return unfinished dates, optionally applying automatic retry scheduling.

    Manual backfills remain chronological and can immediately revisit failures.
    Automatic work gives the latest dates first access to its finite batch and
    slows old, persistently incomplete pages to one retry per day.
    """
    clock = now()
    with db.connect() as connection:
        if start is None:
            earliest = connection.execute("SELECT MAX(date) AS latest FROM draws WHERE source LIKE 'uploaded_workbook%'").fetchone()["latest"]
            if not earliest:
                earliest = connection.execute("SELECT MIN(date) AS earliest FROM draws").fetchone()["earliest"]
            start = date.fromisoformat(earliest) + timedelta(days=1) if earliest else end
        # A same-evening scrape is revisited after midnight for late results.
        # Historical successes are retained and don't repeatedly hit the source.
        done, cooling_down = set(), set()
        for row in connection.execute("SELECT date,status,checked_at FROM sync_days"):
            day = date.fromisoformat(row["date"])
            checked_at = datetime.fromisoformat(row["checked_at"]).astimezone(ZONE)
            checked = checked_at.date()
            if row["status"] == "ok" and (checked > day or (day == clock.date() and checked == day
                                            and (clock.hour < 23 or checked_at.hour >= 23))):
                done.add(row["date"])
            evening_due = (day == clock.date() and clock.hour >= 23
                           and checked == day and checked_at.hour < 23)
            if retry_due and row["status"] in ("partial", "failed") and not evening_due:
                interval = timedelta(minutes=15) if (clock.date() - day).days <= 2 else timedelta(hours=24)
                if clock < checked_at + interval:
                    cooling_down.add(row["date"])
    days = []
    cursor = start
    while cursor <= end:
        if cursor.isoformat() not in done and cursor.isoformat() not in cooling_down:
            days.append(cursor)
        cursor += timedelta(days=1)
    if prioritize_recent:
        cutoff = clock.date() - timedelta(days=2)
        days.sort(key=lambda day: (0, -day.toordinal()) if day >= cutoff else (1, day.toordinal()))
    return days[:limit]


def run_sync(start=None, end=None, max_days=7, *, prioritize_recent=False, retry_due=False):
    initialize()
    end = end or (now().date() if now().hour >= 23 else now().date() - timedelta(days=1))
    if end > now().date() or (start and start > end):
        raise ValueError("Invalid synchronization date range")
    lock_path = Path(db.DB_PATH).with_suffix(".sync.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "inserted": 0, "message": "Another synchronization is running"}
        days = pending_days(end, start, max_days, prioritize_recent=prioritize_recent, retry_due=retry_due)
        if not days:
            return {"status": "ok", "inserted": 0, "message": "No pending dates"}
        timestamp = now().astimezone(timezone.utc).isoformat()
        with db.connect() as connection:
            cursor = connection.execute("INSERT INTO sync_runs(started_at,status,message,inserted) VALUES(?,?,?,0)",
                                        (timestamp, "running", "Fetching source results"))
            run_id = cursor.lastrowid
        inserted, checked, failure, partial = 0, 0, None, []
        for day in days:
            try:
                records = scrape_day(day)
                if not records and not getattr(records, "issues", ()):
                    raise SourceError("Source returned no completed results; date remains pending")
                complete = getattr(records, "complete", True)
                pending = getattr(records, "pending_lotteries", ())
                issues = getattr(records, "issues", ())
                day_inserted = 0
                with db.connect() as connection:
                    for issue in issues:
                        connection.execute("INSERT INTO source_issues(date,lottery,source_url,reason,payload,observed_at) "
                                           "VALUES(?,?,?,?,?,?) ON CONFLICT(date,lottery,source_url,reason) DO UPDATE "
                                           "SET payload=excluded.payload,observed_at=excluded.observed_at",
                                           (day.isoformat(), issue["lottery"], issue["source_url"], issue["reason"],
                                            json.dumps(issue, ensure_ascii=False), now().astimezone(timezone.utc).isoformat()))
                    for record in records:
                        if record["date"] != day.isoformat():
                            raise SourceError("Source draw date does not match requested day")
                        try:
                            day_inserted += int(db.upsert_draw(connection, record["company"], record["lottery"],
                                                              record["date"], record["numbers"], "website",
                                                              record["source_url"], country=record.get("country", "DO")))
                        except db.ConflictingDrawError:
                            # An existing accepted draw stays intact. Conflicts are
                            # audited without discarding other games or later dates.
                            issue = {**record, "reason": "conflicting_draw_for_date"}
                            connection.execute("INSERT INTO source_issues(date,lottery,source_url,reason,payload,observed_at) "
                                               "VALUES(?,?,?,?,?,?) ON CONFLICT(date,lottery,source_url,reason) DO UPDATE "
                                               "SET payload=excluded.payload,observed_at=excluded.observed_at",
                                               (day.isoformat(), record["lottery"], record["source_url"], issue["reason"],
                                                json.dumps(issue, ensure_ascii=False), now().astimezone(timezone.utc).isoformat()))
                            complete = False
                            pending = tuple(sorted(set(pending) | {record["lottery"]}))
                    connection.execute("INSERT INTO sync_days(date,status,checked_at,message) VALUES(?,?,?,?) "
                                       "ON CONFLICT(date) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at,message=excluded.message",
                                       (day.isoformat(), "ok" if complete else "partial", now().astimezone(timezone.utc).isoformat(),
                                        f"{len(records)} results; pending: {', '.join(pending)}" if not complete else f"{len(records)} results"))
                inserted += day_inserted
                checked += 1
                if not complete:
                    partial.append(day.isoformat())
            except (SourceError, ValueError, OSError) as error:
                failure = f"{day.isoformat()}: {error}"
                with db.connect() as connection:
                    connection.execute("INSERT INTO sync_days(date,status,checked_at,message) VALUES(?,?,?,?) "
                                       "ON CONFLICT(date) DO UPDATE SET status=excluded.status,checked_at=excluded.checked_at,message=excluded.message",
                                       (day.isoformat(), "failed", now().astimezone(timezone.utc).isoformat(), failure))
                if isinstance(error, (SourceAccessError, OSError)):
                    # A transport outage affects all dates; a missing historical
                    # page affects only that date and must not block newer ones.
                    break
        status = "failed" if failure else ("partial" if partial else "ok")
        message = failure or (f"Checked {checked} dates; inserted {inserted} results" +
                              (f"; {len(partial)} dates await remaining daily results: {', '.join(partial[:3])}" if partial else ""))
        with db.connect() as connection:
            connection.execute("UPDATE sync_runs SET finished_at=?,status=?,message=?,inserted=? WHERE id=?",
                               (now().astimezone(timezone.utc).isoformat(), status, message, inserted, run_id))
        return {"status": status, "message": message, "inserted": inserted}


def scheduler(stop):
    """Catch up and poll for 23:00; retry recent work every 15 min, older daily."""
    initialize()
    retry_after = datetime.min.replace(tzinfo=ZONE)
    paused_end = None
    while not stop.is_set():
        clock = now()
        end = clock.date() if clock.hour >= 23 else clock.date() - timedelta(days=1)
        if (clock >= retry_after or end != paused_end) and pending_days(
                end, limit=1, prioritize_recent=True, retry_due=True):
            try:
                result = run_sync(end=end, prioritize_recent=True, retry_due=True)
                LOG.info("Results update: %s", result)
                # Persisted per-date checks control normal retries. A historical
                # failure must not delay a new date becoming available at 23:00.
                retry_after = datetime.min.replace(tzinfo=ZONE)
                paused_end = None
            except Exception:
                LOG.exception("Results update failed")
                retry_after = clock + timedelta(minutes=15)
                paused_end = end
        stop.wait(30)


def main():
    parser = argparse.ArgumentParser(description="Backfill lottery results from verified source dates")
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--max-days", type=int, default=400)
    parser.add_argument("--recent", action="store_true", help="Fetch the latest dates before older gaps")
    args = parser.parse_args()
    if args.max_days < 1 or args.max_days > 1000:
        parser.error("--max-days must be between 1 and 1000")
    result = run_sync(args.start, args.end, args.max_days, prioritize_recent=args.recent)
    print(result)
    raise SystemExit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
