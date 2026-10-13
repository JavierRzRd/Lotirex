"""SQLite storage and explicit, positional lottery validation.

Historical observations are not predictions. Pools are kept separate so a
bonus ball is never counted as an ordinary winning number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("LOTIREX_DB", ROOT / "data" / "lotirex.sqlite3"))
# The current official calendar starts on this date. This is the best available
# historical cutoff, not an officially announced rule-change date. See docs/game-rules.md.
KINO_84_START_DATE = Date(2026, 9, 15)


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slug_id(company: str, lottery: str) -> str:
    return _key(company).replace(" ", "-") + "--" + _key(lottery).replace(" ", "-")


def _pool(label: str, count: int, minimum: int, maximum: int, **extra) -> dict:
    return {"label": label, "count": count, "min": minimum, "max": maximum, **extra}


@dataclass(frozen=True)
class GameSpec:
    company: str
    name: str
    country: str
    kind: str
    pools: tuple[dict, ...]
    sizes: tuple[int, ...]
    distinct_main: bool = False

    @property
    def rules(self) -> list[dict]:
        return [dict(p) for p in self.pools]


_SPECS: dict[str, GameSpec] = {}


def _register(company, name, country="DO", kind="quiniela", pools=None,
              sizes=None, distinct_main=False, aliases=()):
    pools = pools or [_pool("main", 3, 0, 99)]
    spec = GameSpec(company, name, country, kind, tuple(pools),
                    tuple(sizes or [sum(p["count"] for p in pools)]), distinct_main)
    for alias in (name, *aliases):
        _SPECS[_key(alias)] = spec


_register("Leidsa", "Quiniela Leidsa", aliases=("Quiniela Palé",))
_register("Leidsa", "Loto Pool", kind="lotto", pools=[_pool("main", 5, 1, 31)], distinct_main=True)
_register("Leidsa", "Super Kino TV", kind="kino", pools=[_pool("main", 20, 1, 84, play_count=10)], distinct_main=True,
          aliases=("Super Kino",))
_register("Leidsa", "Loto - Loto Más", kind="lotto",
          pools=[_pool("main", 6, 1, 40), _pool("bonus", 1, 1, 12), _pool("extra", 1, 1, 15)],
          sizes=(7, 8), distinct_main=True, aliases=("Loto Loto Más", "Loto Más", "Loto", "Loto - Loto Más - Super Loto Más"))
# Pega 3 Más is kept as three ordered positions, rather than a single 3-digit integer.
_register("Leidsa", "Pega 3 Más", kind="pick", pools=[_pool("main", 3, 0, 50)], aliases=("Pega 3",))
_register("Loteka", "Quiniela Loteka")
_register("Loteka", "Mega Chances", kind="pick", pools=[_pool("main", 5, 0, 99)], aliases=("Mega Chance",))
_register("La Primera", "Loto 5", kind="lotto",
          pools=[_pool("main", 5, 1, 38), _pool("bonus", 1, 1, 10)], distinct_main=True,
          aliases=("Lotto 5",))
_register("Lotería Nacional", "Lotería Nacional", aliases=("Nacional Noche", "Quiniela Nacional", "Loteria Nacional- Noche"))
_register("Lotería Nacional", "Gana Más", aliases=("Gana Mas", "Loteria Nacional- Gana Más"))
_register("Lotería Nacional", "Juega + Pega +", kind="pick",
          pools=[_pool("main", 5, 1, 26)], aliases=("Juega Pega", "Juega + Pega"))
_register("Lotería Real", "Quiniela Real")
_register("Lotería Real", "Loto Real", kind="lotto",
          pools=[_pool("main", 6, 1, 38)], distinct_main=True)
_register("La Suerte Dominicana", "La Suerte MD", aliases=("La Suerte Mediodía", "Quiniela La Suerte"))
_register("La Suerte Dominicana", "La Suerte 6PM", aliases=("La Suerte 6:00 PM", "La Suerte Tarde", "Quiniela La Suerte 6PM"))
_register("LoteDom", "LoteDom", aliases=("Quiniela LoteDom",))
_register("La Primera", "La Primera Día", aliases=("La Primera Dia", "Quiniela La Primera"))
_register("La Primera", "Primera Noche", aliases=("La Primera Noche", "Quiniela La Primera Noche"))


class ConflictingDrawError(ValueError):
    """Different results for one game and draw date require human/source review."""


def game_spec(company: str, lottery: str, country: str | None = None) -> GameSpec:
    if not isinstance(company, str) or not company.strip() or not isinstance(lottery, str) or not lottery.strip():
        raise ValueError("missing_company_or_lottery")
    if any(marker in company + lottery for marker in ("Ã", "Â", "\ufffd")):
        raise ValueError("corrupt_name_encoding")
    company_key, name_key = _key(company), _key(lottery)
    if any(word in company_key + " " + name_key for word in ("anguila", "anguilla", "king lottery")):
        raise ValueError("excluded_country")
    if country and country != "DO":
        raise ValueError("excluded_country")
    try:
        return _SPECS[name_key]
    except KeyError:
        raise ValueError("unsupported_lottery") from None


def current_date() -> Date:
    return datetime.now(ZoneInfo("America/Santo_Domingo")).date()


def parse_date(value) -> Date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid_date")
    try:
        return Date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid_date") from None


def rules_for_draw(lottery: str, date, size: int) -> list[dict]:
    """Return the actual historical pools, including an optional old multiplier."""
    try:
        spec = _SPECS[_key(lottery)]
    except KeyError:
        raise ValueError("unsupported_lottery") from None
    draw_date = parse_date(date)
    if size not in spec.sizes:
        raise ValueError("invalid_number_count")
    if spec.name == "Super Kino TV" and draw_date < KINO_84_START_DATE:
        return [_pool("main", 20, 1, 80, play_count=10)]
    if spec.name == "Loto - Loto Más" and size == 7:
        return [_pool("main", 6, 1, 38), _pool("bonus", 1, 1, 10)]
    return spec.rules


def validate_draw(company: str, lottery: str, date, numbers, country="DO"):
    spec = game_spec(company, lottery, country)
    draw_date = parse_date(date)
    if draw_date > current_date():
        raise ValueError("future_date")
    if not isinstance(numbers, (list, tuple)) or any(type(n) is not int for n in numbers):
        raise ValueError("numbers_must_be_integers")
    rules = rules_for_draw(spec.name, draw_date, len(numbers))
    offset = 0
    for pool in rules:
        values = numbers[offset:offset + pool["count"]]
        if any(n < pool["min"] or n > pool["max"] for n in values):
            raise ValueError("number_out_of_range")
        if pool["label"] == "main" and spec.distinct_main and len(set(values)) != len(values):
            raise ValueError("duplicate_main_numbers")
        offset += pool["count"]
    return spec, draw_date.isoformat(), list(numbers), rules


def analysis_values(numbers: list[int], rules: list[dict]):
    """Separate historical non-winning metadata from the actual drawn balls."""
    balls, ball_rules, metadata = [], [], {}
    offset = 0
    for pool in rules:
        values = numbers[offset:offset + pool["count"]]
        if pool.get("analyze", True):
            balls.extend(values)
            ball_rules.append(dict(pool))
        else:
            metadata[pool.get("meaning", pool["label"])] = values[0] if len(values) == 1 else values
        offset += pool["count"]
    return balls, ball_rules, metadata


def connect(path=None) -> sqlite3.Connection:
    target = Path(path) if path is not None else DB_PATH
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if str(target) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize(connection=None) -> None:
    own = connection is None
    connection = connection or connect()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS lotteries (
            id TEXT PRIMARY KEY, company TEXT NOT NULL, name TEXT NOT NULL,
            country TEXT NOT NULL CHECK(country IN ('DO','US')),
            draw_size INTEGER NOT NULL, number_min INTEGER NOT NULL,
            number_max INTEGER NOT NULL, kind TEXT NOT NULL, rules TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS draws (
            id INTEGER PRIMARY KEY, lottery_id TEXT NOT NULL REFERENCES lotteries(id),
            date TEXT NOT NULL, numbers TEXT NOT NULL, source TEXT NOT NULL,
            source_url TEXT, rules TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
            UNIQUE(lottery_id, date)
        );
        CREATE INDEX IF NOT EXISTS draws_date ON draws(date);
        CREATE INDEX IF NOT EXISTS draws_lottery_date ON draws(lottery_id,date);
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, message TEXT, inserted INTEGER DEFAULT 0
        );
    """)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(draws)")}
    if "metadata" not in columns:
        connection.execute("ALTER TABLE draws ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
    remove_foreign_lotteries(connection)
    refresh_catalog_metadata(connection)
    connection.commit()
    if own:
        connection.close()


def remove_foreign_lotteries(connection):
    """Back up legacy data once before removing every non-Dominican game."""
    allowed = sorted({spec.name for spec in _SPECS.values()})
    placeholders = ",".join("?" for _ in allowed)
    predicate = f"country != 'DO' OR name NOT IN ({placeholders})"
    foreign = connection.execute(f"SELECT id FROM lotteries WHERE {predicate}", allowed).fetchall()
    if not foreign:
        return 0
    # A disk database is backed up before deletion; in-memory test DBs need none.
    database = connection.execute("PRAGMA database_list").fetchone()["file"]
    if database:
        from .backup import snapshot_database
        stamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot_database(Path(database), Path(database).parent.parent / "backups" /
                          f"pre-dominican-only-{stamp}.sqlite3")
    identities = [row["id"] for row in foreign]
    marks = ",".join("?" for _ in identities)
    with connection:
        connection.execute(f"DELETE FROM draws WHERE lottery_id IN ({marks})", identities)
        connection.execute(f"DELETE FROM lotteries WHERE id IN ({marks})", identities)
    return len(identities)


def refresh_catalog_metadata(connection) -> None:
    """Refresh researched Leidsa formats without changing any drawn values.

    Existing pre-cutoff observations retain their historical 1–80 rules. Records
    since the inferred current-calendar start use the official current 1–84 pool.
    """
    spec = _SPECS[_key("Super Kino TV")]
    identity = slug_id(spec.company, spec.name)
    current_rules = json.dumps(spec.rules, ensure_ascii=False)
    connection.execute("UPDATE lotteries SET number_max=?,rules=? WHERE id=? AND (number_max<>? OR rules<>?)",
                       (84, current_rules, identity, 84, current_rules))
    connection.execute("UPDATE draws SET rules=? WHERE lottery_id=? AND date>=? AND rules<>?",
                       (current_rules, identity, KINO_84_START_DATE.isoformat(), current_rules))
    historical_kino = [_pool("main", 20, 1, 80, play_count=10)]
    historical_rules = json.dumps(historical_kino, ensure_ascii=False)
    connection.execute("UPDATE draws SET rules=? WHERE lottery_id=? AND date<? AND rules<>?",
                       (historical_rules, identity, KINO_84_START_DATE.isoformat(), historical_rules))
    pega = _SPECS[_key("Pega 3 Más")]
    pega_id = slug_id(pega.company, pega.name)
    # Existing observations were checked to fit the official 0–50 envelope.
    # This updates validation metadata, not the ordered values of a draw.
    pega_rules = json.dumps(pega.rules, ensure_ascii=False)
    connection.execute("UPDATE lotteries SET number_max=?,rules=? WHERE id=? AND (number_max<>? OR rules<>?)",
                       (50, pega_rules, pega_id, 50, pega_rules))
    connection.execute("UPDATE draws SET rules=? WHERE lottery_id=? AND rules<>?",
                       (pega_rules, pega_id, pega_rules))


def upsert_draw(connection, company, lottery, date, numbers, source,
                source_url=None, country="DO") -> bool:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("missing_source")
    spec, iso_date, validated_numbers, rules = validate_draw(company, lottery, date, numbers, country)
    validated_numbers, rules, draw_metadata = analysis_values(validated_numbers, rules)
    lottery_id = slug_id(spec.company, spec.name)
    existing = connection.execute(
        "SELECT numbers FROM draws WHERE lottery_id=? AND date=?", (lottery_id, iso_date)
    ).fetchone()
    if existing:
        if json.loads(existing["numbers"]) != validated_numbers:
            raise ConflictingDrawError("conflicting_draw_for_date")
        return False
    metadata = spec.rules
    connection.execute("""
        INSERT INTO lotteries(id,company,name,country,draw_size,number_min,number_max,kind,rules)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING
    """, (lottery_id, spec.company, spec.name, spec.country,
          sum(p["count"] for p in metadata), metadata[0]["min"], metadata[0]["max"],
          spec.kind, json.dumps(metadata, ensure_ascii=False)))
    connection.execute("""
        INSERT INTO draws(lottery_id,date,numbers,source,source_url,rules,metadata)
        VALUES(?,?,?,?,?,?,?)
    """, (lottery_id, iso_date, json.dumps(validated_numbers), source, source_url,
          json.dumps(rules, ensure_ascii=False), json.dumps(draw_metadata, ensure_ascii=False)))
    return True
