"""Dominican-only dated result cards from loteriadominicana.com.do.

The source's verified date-picker code reverses DDMMYYYY, converts the integer
to uppercase hexadecimal, then base64 encodes it. The adapter only uses this
encoding after detecting that exact navigation code in a fetched source page.
Each card's actual draw date is checked because the page includes older draws.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
import base64
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from .db import game_spec, validate_draw

SOURCE_URL = "https://www.loteriadominicana.com.do/"
ALLOWED_HOSTS = frozenset({"www.loteriadominicana.com.do", "loteriadominicana.com.do"})
USER_AGENT = "LotirexResultsBot/0.1"
MAX_RESPONSE_BYTES = 3_000_000
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
MONTHS = {name: index for index, name in enumerate(
    ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"), 1)}
SOURCE_ALIASES = {
    "loteria nacional- gana mas": "Gana Más",
    "loteria nacional- noche": "Lotería Nacional",
    "quiniela pale": "Quiniela Leidsa",
    "mega chance": "Mega Chances",
    "quiniela la primera": "La Primera Día",
    "quiniela la primera noche": "Primera Noche",
    "lotto 5": "Loto 5",
    "quiniela la suerte": "La Suerte MD",
    "quiniela la suerte 6pm": "La Suerte 6PM",
    }
# These products appear daily in the verified source; weekly lotto cards can
# legitimately carry an older date and do not make a day incomplete. Late
# published results keep the date pending after the 23:00 first pass.
DAILY_LOTTERIES = frozenset({
    "Gana Más", "Lotería Nacional", "Quiniela Leidsa", "Pega 3 Más",
    "Loto Pool", "Super Kino TV", "Quiniela Real", "Quiniela Loteka",
    "Mega Chances", "La Primera Día", "Primera Noche", "Loto 5",
    "La Suerte MD", "La Suerte 6PM", "LoteDom",
})


class SourceError(RuntimeError):
    """The source could not safely provide the requested results."""


class SourceAccessError(SourceError):
    """A transport or source access policy prevented the request."""


class UnsupportedPageError(SourceError):
    """The page does not expose a supported, verifiable result layout."""


class SourceDraws(list):
    """Published draws plus completion information for retryable daily work."""

    def __init__(self, records, pending_lotteries=(), issues=()):
        super().__init__(records)
        self.issues = list(issues)
        self.pending_lotteries = tuple(sorted(pending_lotteries))
        self.complete = not self.pending_lotteries and not self.issues


def _normalized(value: str) -> str:
    return " ".join("".join(char for char in unicodedata.normalize("NFKD", value.lower())
                            if not unicodedata.combining(char)).split())


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Requested date must use YYYY-MM-DD.") from exc


def _dates(text: str) -> set[date]:
    """Read unambiguous full dates; numbers alone are never interpreted as dates."""
    result = set()
    for year, month, day in re.findall(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text):
        try:
            result.add(date(int(year), int(month), int(day)))
        except ValueError:
            pass
    for day, month, year in re.findall(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)", text):
        try:
            result.add(date(int(year), int(month), int(day)))
        except ValueError:
            pass
    for day, month_name, year in re.findall(r"(?<!\d)(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{4})(?!\d)", _normalized(text)):
        if month_name in MONTHS:
            try:
                result.add(date(int(year), MONTHS[month_name], int(day)))
            except ValueError:
                pass
    return result


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceAccessError("Invalid source URL port.") from exc
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or port not in (None, 443)):
        raise SourceAccessError("Source requests require HTTPS on the configured lottery source hosts.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: "_Node | None" = None
    children: list["_Node | str"] = field(default_factory=list)

    def text(self) -> str:
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(child.text() if isinstance(child, _Node) else child for child in self.children).strip()

    def descendants(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()


class _Document(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {key: value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _classes(node: _Node) -> set[str]:
    return set(node.attrs.get("class", "").lower().split())


def _number_values(card: _Node) -> list[int]:
    if "data-numbers" in card.attrs:
        raw = card.attrs["data-numbers"].strip()
        if not re.fullmatch(r"\d{1,2}(?:[\s,|;-]+\d{1,2})*", raw):
            raise UnsupportedPageError("A result card contains invalid winning numbers.")
        return [int(value) for value in re.findall(r"\d+", raw)]
    values = []
    markers = {"number", "numero", "ball", "ball-red", "ball-green", "ball-yellow", "bola", "bolita", "game-ball", "result-number", "winning-number"}
    for node in card.descendants():
        parent = node.parent
        grouped = parent is not None and bool(_classes(parent) & {"numbers", "numeros", "balls", "winning-numbers"})
        if "data-number" in node.attrs or _classes(node) & markers or (grouped and node.tag in {"li", "span", "b", "strong"}):
            raw = node.attrs.get("data-number", node.text()).strip()
            if not re.fullmatch(r"\d{1,2}", raw):
                raise UnsupportedPageError("A winning-number element is not a plain number.")
            # A wrapper and its child can both carry semantic number classes.
            if not any("data-number" in child.attrs or _classes(child) & markers for child in node.descendants()):
                values.append(int(raw))
    return values


def _page_dates(document: _Document) -> set[date]:
    result = set()
    for node in document.root.descendants():
        if node.tag in {"main", "body"}:
            result |= _dates(node.attrs.get("data-date", ""))
        if node.tag == "input" and (node.attrs.get("type") == "date" or node.attrs.get("id") == "Date"):
            result |= _dates(node.attrs.get("value", ""))
        if node.tag in {"h1", "title"} and re.search(r"resultados?|results?|sorteos?", _normalized(node.text())):
            result |= _dates(node.text())
    return result


def _card_dates(card: _Node) -> set[date]:
    result = _dates(card.attrs.get("data-date", ""))
    for node in card.descendants():
        if node.tag == "time":
            result |= _dates(node.attrs.get("datetime", node.text()))
        if "result-item-date" in _classes(node):
            result |= _dates(node.text())
        result |= _dates(node.attrs.get("data-date", ""))
    return result


def parse_results(html: str, source_url: str, expected_date: date | str) -> list[dict]:
    """Parse the verified source layout and explicitly dated semantic cards."""
    target = _as_date(expected_date)
    source_url = _safe_url(source_url)
    document = _Document(html)
    page_dates = _page_dates(document)
    records = []
    issues = []
    seen = set()
    candidates = {"lottery", "loteria", "game", "game-block", "draw", "result", "result-item", "result-card", "lottery-card", "resultado"}
    for card in document.root.descendants():
        if "data-lottery" not in card.attrs and not _classes(card) & candidates:
            continue
        headings = [node.text() for node in card.descendants() if node.tag in {"h2", "h3", "h4", "h5"} and node.text()]
        lottery = card.attrs.get("data-lottery", "").strip() or (headings[0] if len(headings) == 1 else "")
        company = card.attrs.get("data-company", "").strip()
        ancestor = card.parent
        while not company and ancestor is not None:
            company = ancestor.attrs.get("data-company", "").strip()
            if "result" in _classes(ancestor):
                logos = [image.attrs.get("alt", "") for header in ancestor.descendants()
                         if "result-header" in _classes(header)
                         for image in header.descendants() if image.tag == "img"]
                company = company or (logos[0] if len(logos) == 1 else "")
            ancestor = ancestor.parent
        if not company:
            labels = [node.text() for node in card.descendants() if _classes(node) & {"company", "empresa", "lottery-company"}]
            company = labels[0] if len(labels) == 1 else ""
        if not lottery:
            continue
        # Product aliases are an explicit DO/US allowlist. Never classify an
        # unfamiliar or foreign game as Dominican just because it has numbers.
        try:
            spec = game_spec(company or "source", SOURCE_ALIASES.get(_normalized(lottery), lottery), card.attrs.get("data-country") or None)
        except ValueError:
            continue
        company, lottery = spec.company, spec.name
        numbers = _number_values(card)
        if not numbers:
            continue
        card_dates = _card_dates(card)
        verified_dates = card_dates or page_dates
        if verified_dates != {target}:
            continue
        record = {"company": company, "lottery": lottery, "date": target.isoformat(), "numbers": numbers, "source_url": source_url, "country": spec.country}
        try:
            validate_draw(company, lottery, target, numbers, country=spec.country)
        except ValueError as exc:
            reason = str(exc)
            if reason not in {"number_out_of_range", "invalid_number_count", "duplicate_main_numbers"}:
                raise UnsupportedPageError(f"Source draw validation failed: {reason}.") from exc
            # Preserve every literal source number for review. A malformed game
            # must neither enter the statistical database nor prevent unrelated
            # valid games from being saved for the same day.
            issue = {**record, "reason": reason}
            if issue not in issues:
                issues.append(issue)
            continue
        key = (company, lottery, target.isoformat())
        if key in seen:
            prior = next(record for record in records if (record["company"], record["lottery"], record["date"]) == key)
            if prior["numbers"] != numbers:
                raise UnsupportedPageError("The source contains conflicting results for one lottery and date.")
            continue
        seen.add(key)
        records.append(record)
    if not records and not issues:
        raise UnsupportedPageError(f"No supported results with a verified draw date of {target.isoformat()} were found; the source may be incomplete or its layout may have changed.")
    pending = DAILY_LOTTERIES - {record["lottery"] for record in records}
    pending |= {issue["lottery"] for issue in issues}
    return SourceDraws(records, pending, issues)


def resolve_date_url(html: str, source_url: str, requested_date: date | str) -> str:
    """Follow a dated source link or a real GET date form; opaque codes are not guessed."""
    target = _as_date(requested_date)
    source_url = _safe_url(source_url)
    document = _Document(html)
    for node in document.root.descendants():
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        href = node.attrs["href"]
        clues = " ".join([node.text(), node.attrs.get("data-date", ""), node.attrs.get("title", "")])
        clues += " " + " ".join(value for _, value in parse_qsl(urlsplit(href).query))
        if _dates(clues) == {target}:
            try:
                return _safe_url(urljoin(source_url, href))
            except SourceAccessError:
                continue
    for form in document.root.descendants():
        if form.tag != "form" or form.attrs.get("method", "get").lower() != "get":
            continue
        inputs = [node for node in form.descendants() if node.tag == "input" and node.attrs.get("name")]
        fields = [(node.attrs["name"], node.attrs.get("value", "")) for node in inputs if node.attrs.get("type") == "hidden"]
        for node in inputs:
            if node.attrs.get("type") != "date":
                continue
            try:
                action = _safe_url(urljoin(source_url, form.attrs.get("action") or source_url))
            except SourceAccessError:
                continue
            parsed = urlsplit(action)
            date_name = node.attrs["name"]
            pairs = [(key, value) for key, value in parse_qsl(parsed.query) + fields if key != date_name]
            pairs.append((date_name, target.isoformat()))
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), ""))
    # This is a translation of observed source code, not a guessed date scheme.
    # Require the exact algorithm and corresponding date-picker to fail clearly
    # if the upstream navigation implementation changes.
    date_picker = any(node.tag == "input" and node.attrs.get("id") == "Date" and
                      len(_dates(node.attrs.get("value", ""))) == 1 for node in document.root.descendants())
    # Script text is intentionally excluded from Node.text(), so read its raw
    # child text here, without evaluating any upstream JavaScript.
    scripts = " ".join("".join(child for child in node.children if isinstance(child, str))
                       for node in document.root.descendants() if node.tag == "script")
    signature = "encrypt.base64.encode(parseInt($(obj).val().replace(/[\\-]+/g,'').reverse(),10).toHex())"
    if date_picker and signature in scripts and "this.location.href='/?d='+v" in scripts:
        reversed_digits = target.strftime("%d%m%Y")[::-1]
        encoded = base64.b64encode(format(int(reversed_digits), "X").encode("ascii")).decode("ascii")
        return _safe_url(urljoin(source_url, "/?" + urlencode({"d": encoded})))
    raise UnsupportedPageError(f"The source does not expose a supported link or GET date form for {target.isoformat()}. Its opaque date parameter cannot safely be inferred.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SourceClient:
    def __init__(self, source_url: str = SOURCE_URL, min_interval: float = 2.0, timeout: float = 30.0):
        self.source_url = _safe_url(source_url)
        self.min_interval = max(1.0, min_interval)
        self.timeout = timeout
        self._last_request = 0.0
        self._robots: dict[str, RobotFileParser] = {}
        self._robots_checked: dict[str, float] = {}
        self._opener = build_opener(_NoRedirect())
        self._landing_html: str | None = None
        self._landing_checked = 0.0

    def _request(self, url: str, interval: float) -> tuple[str, dict]:
        remaining = interval - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9", "Accept-Encoding": "identity"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise SourceAccessError("Source response exceeds the safe size limit.")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    return body.decode(charset, errors="replace"), dict(response.headers.items())
                except LookupError as exc:
                    raise SourceAccessError("The source declared an unsupported text encoding.") from exc
        except HTTPError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceAccessError(f"Could not reach the lottery source: {exc}") from exc

    def _robot_rules(self, url: str) -> RobotFileParser:
        host = urlsplit(url).hostname
        if host in self._robots and time.monotonic() - self._robots_checked[host] < 3600:
            return self._robots[host]
        robots_url = f"https://{host}/robots.txt"
        parser = RobotFileParser(robots_url)
        try:
            text, headers = self._request(robots_url, self.min_interval)
            content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
            if "html" in content_type.lower() or re.match(r"\s*(?:<!doctype\s+html|<html)", text, re.IGNORECASE):
                raise SourceAccessError("Source robots.txt returned an HTML page; scraping policy cannot be verified.")
            parser.parse(text.splitlines())
        except HTTPError as exc:
            if exc.code in (404, 410):
                parser.parse([])
            else:
                raise SourceAccessError(f"Cannot verify source robots.txt policy (HTTP {exc.code}); scraping has stopped.") from exc
        self._robots[host] = parser
        self._robots_checked[host] = time.monotonic()
        return parser

    def fetch_html(self, url: str) -> str:
        url = _safe_url(url)
        for _ in range(4):
            rules = self._robot_rules(url)
            if not rules.can_fetch(USER_AGENT, url):
                raise SourceAccessError("Source robots.txt disallows this result URL.")
            delay = rules.crawl_delay(USER_AGENT) or 0
            rate = rules.request_rate(USER_AGENT)
            interval = max(self.min_interval, delay, rate.seconds / rate.requests if rate and rate.requests > 0 else 0)
            try:
                html, headers = self._request(url, interval)
                content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
                if content_type and not content_type.lower().startswith(("text/html", "application/xhtml+xml")):
                    raise SourceAccessError("The result URL did not return an HTML document.")
                return html
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                    url = _safe_url(urljoin(url, exc.headers["Location"]))
                    continue
                raise SourceAccessError(f"Lottery source request failed with HTTP {exc.code}.") from exc
        raise SourceAccessError("The lottery source redirected too many times.")

    def scrape_day(self, requested_date: date | str) -> list[dict]:
        target = _as_date(requested_date)
        if self._landing_html is None or time.monotonic() - self._landing_checked > 1800:
            self._landing_html = self.fetch_html(self.source_url)
            self._landing_checked = time.monotonic()
        html = self._landing_html
        try:
            historical_url = resolve_date_url(html, self.source_url, target)
        except UnsupportedPageError:
            # A latest-results page may contain yesterday's results for only
            # some games. Use it directly only when its explicit selected date
            # matches the requested date, or each card dates itself and there
            # is no page date. Otherwise historical retrieval must fail.
            page_dates = _page_dates(_Document(html))
            if page_dates and page_dates != {target}:
                raise
            return parse_results(html, self.source_url, target)
        return parse_results(html if historical_url == self.source_url else self.fetch_html(historical_url), historical_url, target)


_client: SourceClient | None = None
_client_lock = threading.Lock()


def scrape_day(requested_date: date | str) -> list[dict]:
    # Keep robots/navigation caches and request pacing across a long backfill.
    # Scheduler and manual runs share one transport rather than resetting the
    # delay for each requested day.
    global _client
    with _client_lock:
        if _client is None:
            _client = SourceClient()
        return _client.scrape_day(requested_date)
