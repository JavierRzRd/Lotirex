import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, db, sync

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    sync.initialize()
    stop = threading.Event()
    worker = None
    if os.environ.get("LOTIREX_SCHEDULER", "1") == "1":
        worker = threading.Thread(target=sync.scheduler, args=(stop,), daemon=True, name="nightly-results")
        worker.start()
    yield
    stop.set()
    if worker:
        worker.join(timeout=3)


app = FastAPI(title="Lotirex", lifespan=lifespan)


@app.get("/api/health")
def health():
    with db.connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS total FROM draws d JOIN lotteries l ON l.id=d.lottery_id WHERE l.country='DO'").fetchone()["total"]
    return {"status": "ok", "draws": count, "scheduler": os.environ.get("LOTIREX_SCHEDULER", "1") == "1"}


def source_issue_statuses(connection):
    """Resolve audit records in Python, including SQLite builds without JSON1."""
    rows = connection.execute("""SELECT si.*, d.numbers AS accepted_numbers,
        d.source AS accepted_source FROM source_issues si
        LEFT JOIN lotteries l ON l.name=si.lottery AND l.country='DO'
        LEFT JOIN draws d ON d.lottery_id=l.id AND d.date=si.date
        ORDER BY si.date DESC""")
    issues = []
    for row in rows:
        issue = dict(row)
        numbers = issue.pop("accepted_numbers")
        source = issue.pop("accepted_source")
        issue["payload"] = json.loads(issue["payload"])
        resolved = source == "website"
        if resolved and issue["reason"] == "conflicting_draw_for_date":
            resolved = json.loads(numbers) == issue["payload"].get("numbers")
        issue["resolved"] = resolved
        issues.append(issue)
    return issues


@app.get("/api/catalog")
def catalog():
    with db.connect() as connection:
        rows = list(connection.execute("SELECT * FROM lotteries WHERE country='DO' ORDER BY company,name"))
        summary = dict(connection.execute("SELECT COUNT(*) AS draws,MIN(d.date) AS earliest,MAX(d.date) AS latest FROM draws d JOIN lotteries l ON l.id=d.lottery_id WHERE l.country='DO'").fetchone())
        latest = connection.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        issues = source_issue_statuses(connection)
        summary["source_issue_audits"] = len(issues)
        summary["source_issues"] = sum(not issue["resolved"] for issue in issues)
        summary["pending_dates"] = connection.execute("SELECT COUNT(*) AS total FROM sync_days WHERE status!='ok'").fetchone()["total"]
    companies = {}
    for row in rows:
        company = companies.setdefault(row["company"], {"name": row["company"], "lotteries": []})
        company["lotteries"].append(analytics.lottery_dict(row))
    summary["lotteries"] = len(rows)
    summary["years"] = max(1, int((date.fromisoformat(summary["latest"]) - date.fromisoformat(summary["earliest"])).days / 365.25)) if summary["draws"] else 0
    report_path = ROOT / "data/import-report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        summary["quarantined"] = report.get("quarantined", report.get("quarantined_rows", 0))
        summary["raw_rows"] = report.get("source_rows", report.get("rows_read", report.get("raw_rows", 0)))
        summary["imported_rows"] = report.get("inserted", 0) + report.get("already_present", 0)
        summary["excluded_foreign"] = report.get("excluded_foreign", 0)
    last_run = dict(latest) if latest else None
    source_access = "unknown"
    if last_run:
        message = last_run["message"].lower()
        if last_run["status"] in ("ok", "partial"):
            source_access = "ok"
        elif last_run["status"] == "running":
            source_access = "unknown"
        else:
            source_access = "blocked" if any(token in message for token in ("403", "proxy", "denied", "blocked")) else "unavailable"
    return {"companies": list(companies.values()), "summary": summary,
            "sync": {"last_run": last_run, "next_run": sync.next_run().isoformat(),
                     "timezone": "America/Santo_Domingo", "source_access": source_access}}


@app.get("/api/data-quality")
def data_quality():
    report = ROOT / "data/import-report.json"
    with db.connect() as connection:
        issues = source_issue_statuses(connection)
        pending = [dict(row) for row in connection.execute(
            "SELECT * FROM sync_days WHERE status!='ok' ORDER BY date DESC")]
    return {"import": json.loads(report.read_text()) if report.exists() else None,
            "source_issues": issues, "pending_dates": pending}


@app.get("/api/dashboard")
def dashboard(lottery: str, mode: str = "today", month: int | None = Query(None, ge=1, le=12),
              season: str | None = None, start: date | None = None, end: date | None = None,
              position: str = "all", limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
    if mode not in ("today", "weekday", "month", "all"):
        raise HTTPException(422, "Invalid recommendation mode")
    if season is not None and season not in analytics.SEASONS:
        raise HTTPException(422, "Invalid season")
    if position not in ("all", "1", "2", "3", "bonus", "extra"):
        raise HTTPException(422, "Invalid position")
    if start and end and start > end:
        raise HTTPException(422, "Start date must precede end date")
    try:
        if position != "all":
            with db.connect() as connection:
                selected = analytics.get_lottery(connection, lottery)
                ordered = selected["kind"] == "quiniela" or (selected["kind"] == "pick" and selected["draw_size"] <= 3)
                if position.isdigit() and not ordered:
                    raise HTTPException(422, "Numeric places apply only to ordered quiniela games")
                if position in ("bonus", "extra") and not any(pool["label"] == position for pool in selected["rules"]):
                    raise HTTPException(422, "This game does not have that bonus pool")
        return analytics.dashboard(lottery, mode, month, season, start, end, position, limit, offset)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/api/analyze")
def analyze(numbers: str = Query(..., max_length=100)):
    if not re.fullmatch(r"\s*\d{1,2}(?:[\s,]+\d{1,2})*\s*", numbers):
        raise HTTPException(422, "Enter numbers from 00 to 99 separated by spaces or commas")
    parsed = list(dict.fromkeys(int(value) for value in re.findall(r"\d+", numbers)))
    if len(parsed) > 10:
        raise HTTPException(422, "Analyze at most 10 numbers at a time")
    return analytics.analyze(parsed)


if (ROOT / "dist/assets").is_dir():
    app.mount("/assets", StaticFiles(directory=ROOT / "dist/assets"), name="assets")


@app.get("/")
def homepage():
    index = ROOT / "dist/index.html"
    if not index.exists():
        raise HTTPException(503, "Build the frontend with npm run build")
    return FileResponse(index)
