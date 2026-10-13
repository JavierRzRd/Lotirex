"""Descriptive statistics. Frequencies are observations, never future odds."""
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import db

ZONE = ZoneInfo("America/Santo_Domingo")
SEASONS = {"spring": (3, 4, 5), "summer": (6, 7, 8),
           "autumn": (9, 10, 11), "winter": (12, 1, 2)}


def today():
    return datetime.now(ZONE).date()


def lottery_dict(row):
    result = dict(row)
    result["rules"] = json.loads(result.get("rules") or "[]")
    return result


def get_lottery(connection, identity):
    row = connection.execute("SELECT * FROM lotteries WHERE id=? AND country='DO'", (identity,)).fetchone()
    if row is None:
        raise ValueError("Lottery not found")
    return lottery_dict(row)


def pool_for_position(lottery, index):
    cursor = 0
    for pool in lottery["rules"]:
        cursor += pool["count"]
        if index < cursor:
            return pool["label"]
    return "main"


def positions(lottery, numbers, selected="all"):
    for index, number in enumerate(numbers):
        pool = pool_for_position(lottery, index)
        if selected in ("bonus", "extra") and pool != selected:
            continue
        if selected.isdigit() and index != int(selected) - 1:
            continue
        if selected == "all" and pool != "main":
            continue
        ordered = lottery["kind"] == "quiniela" or (lottery["kind"] == "pick" and lottery["draw_size"] <= 3)
        label = str(index + 1) if ordered else "winning"
        yield {"number": number, "position": label, "pool": pool}


def draw_positions(lottery, row, selected="all"):
    historical = {**lottery, "rules": row.get("rules") or lottery["rules"]}
    return positions(historical, row["numbers"], selected)


def rank(values):
    counts = Counter(values)
    total = sum(counts.values())
    return [{"number": number, "count": count,
             "percent": round(count * 100 / total, 2) if total else 0}
            for number, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


def load_rows(connection, identity, start=None, end=None):
    query = "SELECT date,numbers,source,rules FROM draws WHERE lottery_id=?"
    args = [identity]
    if start:
        query += " AND date>=?"
        args.append(start.isoformat())
    if end:
        query += " AND date<=?"
        args.append(end.isoformat())
    query += " ORDER BY date DESC"
    return [{"date": row["date"], "numbers": json.loads(row["numbers"]),
             "source": row["source"], "rules": json.loads(row["rules"] or "[]")}
            for row in connection.execute(query, args)]


def dashboard(identity, mode="today", month=None, season=None, start=None,
              end=None, position="all", limit=10, offset=0, reference=None):
    reference = reference or today()
    with db.connect() as connection:
        lottery = get_lottery(connection, identity)
        rows = load_rows(connection, identity, start, end)
    rows = [row for row in rows
            if (month is None or date.fromisoformat(row["date"]).month == month)
            and (season is None or date.fromisoformat(row["date"]).month in SEASONS[season])]
    observations = [entry for row in rows for entry in draw_positions(lottery, row, position)]
    values = [entry["number"] for entry in observations]
    sample = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        if day >= reference:
            continue
        if mode == "today" and (day.month, day.day) != (reference.month, reference.day):
            continue
        if mode == "weekday" and day.weekday() != reference.weekday():
            continue
        if mode == "month" and day.month != reference.month:
            continue
        sample.append(row)
    sample_values = [entry["number"] for row in sample
                     for entry in draw_positions(lottery, row, position)]
    recommendations = []
    rules = lottery["rules"] or [{"label": "main", "count": lottery["draw_size"],
                                   "min": lottery["number_min"], "max": lottery["number_max"]}]
    for pool in rules:
        if position != "all" and (position.isdigit() or pool["label"] != position):
            continue
        pool_values = [entry["number"] for row in sample
                       for entry in draw_positions(lottery, row,
                           "all" if pool["label"] == "main" else pool["label"])]
        playable_rank = [entry for entry in rank(pool_values)
                         if pool["min"] <= entry["number"] <= pool["max"]]
        recommendations.extend([{**entry, "pool": pool["label"]}
                                for entry in playable_rank[:pool.get("play_count", pool["count"])]])
    if position.isdigit():
        recommendations = [{**entry, "pool": "main"} for entry in rank(sample_values)[:1]]
    trends = [{"date": row["date"], **entry} for row in rows[:120]
              for entry in draw_positions(lottery, row, position)]
    calendar = []
    for calendar_month in range(1, 13):
        monthly = [row for row in rows if date.fromisoformat(row["date"]).month == calendar_month]
        ranking = rank([entry["number"] for row in monthly
                        for entry in draw_positions(lottery, row, position)])
        calendar.append({"month": calendar_month, "draws": len(monthly),
                         "top_number": ranking[0]["number"] if ranking else None,
                         "count": ranking[0]["count"] if ranking else 0})
    return {"lottery": lottery,
            "filters": {"mode": mode, "month": month, "season": season,
                        "start": start.isoformat() if start else None,
                        "end": end.isoformat() if end else None, "position": position},
            "summary": {"draws": len(rows), "total_numbers": len(values),
                        "earliest": rows[-1]["date"] if rows else None,
                        "latest": rows[0]["date"] if rows else None,
                        "even_percent": round(sum(n % 2 == 0 for n in values) * 100 / len(values), 1) if values else 0,
                        "distinct_numbers": len(set(values))},
            "recommendation": {"numbers": recommendations, "sample_draws": len(sample),
                               "sample_numbers": len(sample_values), "mode": mode,
                               "earliest": sample[-1]["date"] if sample else None,
                               "latest": sample[0]["date"] if sample else None,
                               "reference_date": reference.isoformat()},
            "ranking": rank(values), "trends": trends,
            "trend_draw_limit": 120, "history": rows[offset:offset + limit],
            "calendar": calendar,
            "pagination": {"total": len(rows), "limit": limit, "offset": offset}}


def analyze(numbers):
    with db.connect() as connection:
        lotteries = {row["id"]: lottery_dict(row) for row in connection.execute("SELECT * FROM lotteries WHERE country='DO'")}
        counts = defaultdict(Counter)
        appearances = defaultdict(Counter)
        draw_counts = Counter()
        matched_draws = Counter()
        for row in connection.execute("SELECT d.lottery_id,d.numbers,d.rules FROM draws d JOIN lotteries l ON l.id=d.lottery_id WHERE l.country='DO'"):
            identity = row["lottery_id"]
            lottery = {**lotteries[identity], "rules": json.loads(row["rules"] or "[]")}
            drawn = json.loads(row["numbers"])
            draw_counts[identity] += 1
            for number in numbers:
                if number in drawn:
                    matched_draws[(identity, number)] += 1
                for index, value in enumerate(drawn):
                    if value == number:
                        pool = pool_for_position(lottery, index)
                        ordered = lottery["kind"] == "quiniela" or (lottery["kind"] == "pick" and lottery["draw_size"] <= 3)
                        label = str(index + 1) if ordered else ("winning" if pool == "main" else pool)
                        counts[(identity, number)][label] += 1
                        appearances[identity][number] += 1
        result = []
        for number in numbers:
            matches = []
            for identity, lottery in lotteries.items():
                count = appearances[identity][number]
                if count:
                    matches.append({"id": identity, "company": lottery["company"],
                                    "name": lottery["name"], "draws": draw_counts[identity],
                                    "appearances": count,
                                    "rate": round(matched_draws[(identity, number)] * 100 / draw_counts[identity], 2),
                                    "positions": [{"label": label, "count": value}
                                                  for label, value in sorted(counts[(identity, number)].items())]})
            matches.sort(key=lambda row: (-row["rate"], -row["draws"], row["name"]))
            result.append({"number": number, "lotteries": matches})
    return {"numbers": result}
