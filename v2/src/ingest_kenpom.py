"""Authenticated KenPom ingestor with point-in-time HTML caching.

Live mode (today's snapshot) combines two pages into one row per team:

* ``index.php`` (public ratings table): AdjEM, AdjO, AdjD, AdjT.
* ``teamstats.php`` (subscriber-only misc. stats): 3PA% = FGA3 / FGA.

Archive mode (historical backfill) reads ``archive.php?d=YYYY-MM-DD``, which
holds the ratings "the morning of" that date, i.e. before that day's games.
KenPom has no point-in-time 3PA history (``teamstats.php`` only serves
season-to-date totals), so archive rows store ``three_pa_rate = NULL``.
Archive pages exist only for game days; other dates return an empty table.

Raw HTML is cached under ``data/raw_cache/`` so each page is fetched at most
once and can be re-parsed later.

Leakage guard: ``index.php``/``teamstats.php`` only serve *current* data, so
a live fetch is only allowed when the snapshot date is today. Back-dated
snapshots must come from the cache or the archive; otherwise today's ratings
would be stamped with a past date and leak future information into backtests.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

from src.db import PROJECT_ROOT, get_db_connection

log = logging.getLogger(__name__)

BASE_URL = "https://kenpom.com"
LOGIN_URL = f"{BASE_URL}/handlers/login_handler.php"
RATINGS_URL = f"{BASE_URL}/index.php"
TEAMSTATS_URL = f"{BASE_URL}/teamstats.php"
ARCHIVE_URL = f"{BASE_URL}/archive.php"

DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "raw_cache"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
# Request metering. Backfill waits delay * U(1, 1 + JITTER) between archive
# fetches (cache hits don't wait). Transient errors (429 / gateway 5xx /
# connection drops) are retried with exponential backoff, honoring a numeric
# Retry-After header. A run aborts after several dates fail in a row, which
# usually means we're being throttled.
DEFAULT_BACKFILL_DELAY = 3.0
BACKFILL_JITTER = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF = 10.0
RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_CONSECUTIVE_FAILURES = 3

# AdjEM is displayed to 2 decimals, AdjO/AdjD to 1, so the identity
# AdjEM = AdjO - AdjD holds only to within rounding.
ADJ_EM_TOLERANCE = 0.2

# Normalized header label -> field. First matching column wins, because the
# ratings table repeats NetRtg/ORtg/DRtg inside the Strength of Schedule group.
RATINGS_HEADERS = {
    "adj_em": ("netrtg", "adjem"),
    "adj_o": ("ortg", "adjo"),
    "adj_d": ("drtg", "adjd"),
    "adj_t": ("adjt",),
}
THREE_PA_HEADERS = ("3pa%", "3pa/fga", "3par", "fg3a%")

_TRAILING_JUNK = re.compile(r"[\s\d*]+$")


class KenPomError(RuntimeError):
    """Base error for the KenPom ingestor."""


class KenPomAuthError(KenPomError):
    """Missing credentials or login did not unlock subscriber pages."""


class KenPomParseError(KenPomError):
    """Page structure didn't match expectations."""


class EmptyRatingsError(KenPomParseError):
    """Ratings table present but has no team rows (e.g. archive off-day)."""


class LeakageError(KenPomError):
    """Refused to stamp current ratings with a past snapshot date."""


@dataclass(frozen=True)
class SnapshotRow:
    snapshot_date: date
    team: str
    adj_em: float
    adj_o: float
    adj_d: float
    adj_t: float
    three_pa_rate: float | None

    def as_tuple(self) -> tuple:
        return (
            self.snapshot_date,
            self.team,
            self.adj_em,
            self.adj_o,
            self.adj_d,
            self.adj_t,
            self.three_pa_rate,
        )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def clean_team_name(raw: str) -> str:
    """Strip trailing tournament seeds/ranks and asterisks: 'Duke 1*' -> 'Duke'."""
    return _TRAILING_JUNK.sub("", raw.strip()).strip()


def _norm_header(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _expanded_headers(row: Tag) -> list[str]:
    """Header labels with colspans expanded so indices line up with <td>s."""
    labels: list[str] = []
    for cell in row.find_all(["th", "td"]):
        label = _norm_header(cell.get_text(" ", strip=True))
        labels.extend([label] * int(cell.get("colspan", 1) or 1))
    return labels


def _find_table_and_headers(html: str) -> tuple[Tag, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="ratings-table")
    if table is None:
        raise KenPomParseError("No <table id='ratings-table'> found on page")

    header_rows = [
        tr
        for tr in table.find_all("tr")
        if tr.find("th") and "team" in _expanded_headers(tr)
    ]
    if not header_rows:
        raise KenPomParseError("No header row containing 'Team' found")
    return table, _expanded_headers(header_rows[0])


def _column_index(headers: list[str], candidates: Iterable[str], page: str) -> int:
    for i, label in enumerate(headers):
        if label in candidates:
            return i
    raise KenPomParseError(f"{page}: none of {tuple(candidates)} in headers {headers}")


def _team_rows(table: Tag, team_idx: int) -> Iterable[tuple[str, list[str]]]:
    """Yield (team, cell_texts) for data rows; skips repeated header rows."""
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) <= team_idx:
            continue
        team_cell = cells[team_idx]
        link = team_cell.find("a")
        raw_name = link.get_text(strip=True) if link else team_cell.get_text(" ", strip=True)
        team = clean_team_name(raw_name)
        if team:
            yield team, [c.get_text(strip=True) for c in cells]


def _to_float(text: str, team: str, field: str) -> float:
    try:
        return float(text.replace("+", ""))
    except ValueError as exc:
        raise KenPomParseError(f"{team}: cannot parse {field}={text!r}") from exc


def parse_ratings(html: str) -> dict[str, dict[str, float]]:
    """Parse index.php into {team: {adj_em, adj_o, adj_d, adj_t}}."""
    table, headers = _find_table_and_headers(html)
    team_idx = _column_index(headers, ("team",), "ratings")
    cols = {f: _column_index(headers, labels, "ratings") for f, labels in RATINGS_HEADERS.items()}

    ratings: dict[str, dict[str, float]] = {}
    for team, cells in _team_rows(table, team_idx):
        if len(cells) <= max(cols.values()):
            continue
        ratings[team] = {f: _to_float(cells[i], team, f) for f, i in cols.items()}

    if not ratings:
        raise EmptyRatingsError("ratings: no team rows parsed")

    # Guard against column drift: AdjEM must equal AdjO - AdjD (to rounding).
    bad = [
        t
        for t, r in ratings.items()
        if abs(r["adj_em"] - (r["adj_o"] - r["adj_d"])) > ADJ_EM_TOLERANCE
    ]
    if bad:
        raise KenPomParseError(
            f"ratings: AdjEM != AdjO - AdjD for {len(bad)} teams (e.g. {bad[:3]}); "
            "column mapping is likely wrong"
        )
    return ratings


def parse_three_pa_rate(html: str) -> dict[str, float]:
    """Parse teamstats.php into {team: 3PA/FGA as a fraction in [0, 1]}."""
    table, headers = _find_table_and_headers(html)
    team_idx = _column_index(headers, ("team",), "teamstats")
    idx = _column_index(headers, THREE_PA_HEADERS, "teamstats")

    rates: dict[str, float] = {}
    for team, cells in _team_rows(table, team_idx):
        if len(cells) <= idx:
            continue
        value = _to_float(cells[idx], team, "three_pa_rate")
        rate = value / 100.0 if value > 1.0 else value
        if not 0.0 <= rate <= 1.0:
            raise KenPomParseError(f"{team}: 3PA rate out of range: {cells[idx]!r}")
        rates[team] = rate

    if not rates:
        raise KenPomParseError("teamstats: no team rows parsed")
    return rates


def build_archive_snapshot(snapshot_date: date, archive_html: str) -> list[SnapshotRow]:
    """Archive pages carry ratings only; 3PA rate is unknown (NULL)."""
    return [
        SnapshotRow(snapshot_date, team, three_pa_rate=None, **r)
        for team, r in sorted(parse_ratings(archive_html).items())
    ]


def build_snapshot(
    snapshot_date: date, ratings_html: str, teamstats_html: str
) -> list[SnapshotRow]:
    ratings = parse_ratings(ratings_html)
    rates = parse_three_pa_rate(teamstats_html)

    missing = sorted(set(ratings) - set(rates))
    if missing:
        log.warning("No 3PA rate for %d teams (stored as NULL): %s", len(missing), missing[:10])

    return [
        SnapshotRow(snapshot_date, team, three_pa_rate=rates.get(team), **r)
        for team, r in sorted(ratings.items())
    ]


# --------------------------------------------------------------------------
# Network + cache
# --------------------------------------------------------------------------


def ratings_cache_path(cache_dir: Path, snapshot_date: date) -> Path:
    return cache_dir / f"kenpom_{snapshot_date.isoformat()}.html"


def teamstats_cache_path(cache_dir: Path, snapshot_date: date) -> Path:
    return cache_dir / f"kenpom_teamstats_{snapshot_date.isoformat()}.html"


def archive_cache_path(cache_dir: Path, snapshot_date: date) -> Path:
    return cache_dir / f"kenpom_archive_{snapshot_date.isoformat()}.html"


def get_credentials() -> tuple[str, str]:
    user = os.getenv("KENPOM_USER") or os.getenv("KENPOM_EMAIL")
    password = os.getenv("KENPOM_PASS") or os.getenv("KENPOM_PASSWORD")
    if not user or not password:
        raise KenPomAuthError(
            "Set KENPOM_USER and KENPOM_PASS (or KENPOM_EMAIL / KENPOM_PASSWORD)"
        )
    return user, password


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def login(session: requests.Session) -> None:
    """Log the session in once; later calls are no-ops."""
    if getattr(session, "kenpom_logged_in", False):
        return
    user, password = get_credentials()
    resp = session.post(
        LOGIN_URL,
        data={"email": user, "password": password, "submit": "Login!"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    session.kenpom_logged_in = True


def _retry_wait(resp: requests.Response | None, attempt: int) -> float:
    retry_after = resp.headers.get("Retry-After") if resp is not None else None
    if retry_after and retry_after.strip().isdigit():
        return float(retry_after)
    return RETRY_BACKOFF * 2**attempt


def _get(
    session: requests.Session,
    url: str,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """GET with retries on transient failures (see metering notes above)."""
    for attempt in range(MAX_RETRIES + 1):
        resp = None
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_RETRIES:
                raise
        else:
            if resp.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                resp.raise_for_status()
                return resp.text
        wait = _retry_wait(resp, attempt)
        status = resp.status_code if resp is not None else "connection error"
        log.warning("GET %s -> %s; retry %d/%d in %.0fs", url, status,
                    attempt + 1, MAX_RETRIES, wait)
        sleep(wait)
    raise AssertionError("unreachable")


def load_pages(
    snapshot_date: date,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
) -> tuple[str, str]:
    """Return (ratings_html, teamstats_html), from cache when available.

    Freshly fetched pages are parsed before being cached, so a login wall or
    broken page never poisons the cache.
    """
    today = today or date.today()
    ratings_path = ratings_cache_path(cache_dir, snapshot_date)
    teamstats_path = teamstats_cache_path(cache_dir, snapshot_date)

    to_fetch = [p for p in (ratings_path, teamstats_path) if not p.exists()]
    if to_fetch and snapshot_date != today:
        raise LeakageError(
            f"No cached KenPom pages for {snapshot_date} ({[p.name for p in to_fetch]}). "
            "KenPom only serves current ratings, so they can't be fetched for a past "
            "date. Use the archive backfill (--start/--end) for historical ratings."
        )

    if ratings_path.exists():
        log.info("Using cached %s", ratings_path.name)
        ratings_html = ratings_path.read_text(encoding="utf-8")
        fresh_ratings = False
    else:
        session = session or new_session()
        log.info("Fetching %s", RATINGS_URL)
        ratings_html = _get(session, RATINGS_URL)
        parse_ratings(ratings_html)
        fresh_ratings = True

    if teamstats_path.exists():
        log.info("Using cached %s", teamstats_path.name)
        teamstats_html = teamstats_path.read_text(encoding="utf-8")
    else:
        session = session or new_session()
        login(session)
        log.info("Fetching %s", TEAMSTATS_URL)
        teamstats_html = _get(session, TEAMSTATS_URL)
        try:
            parse_three_pa_rate(teamstats_html)
        except KenPomParseError as exc:
            raise KenPomAuthError(
                "teamstats.php didn't contain a 3PA% table; login likely failed "
                f"or page layout changed ({exc})"
            ) from exc
        cache_dir.mkdir(parents=True, exist_ok=True)
        teamstats_path.write_text(teamstats_html, encoding="utf-8")

    if fresh_ratings:
        cache_dir.mkdir(parents=True, exist_ok=True)
        ratings_path.write_text(ratings_html, encoding="utf-8")

    return ratings_html, teamstats_html


def load_archive_page(
    snapshot_date: date,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Return archive HTML for a past date, or None if KenPom has no ratings
    for it (non-game day). Only pages with team rows are cached."""
    today = today or date.today()
    if snapshot_date >= today:
        raise KenPomError(
            f"Archive is only available for past dates (got {snapshot_date}); "
            "use live mode for today."
        )

    path = archive_cache_path(cache_dir, snapshot_date)
    if path.exists():
        log.info("Using cached %s", path.name)
        return path.read_text(encoding="utf-8")

    session = session or new_session()
    login(session)
    log.info("Fetching archive for %s", snapshot_date)
    html = _get(session, f"{ARCHIVE_URL}?d={snapshot_date.isoformat()}", sleep)
    try:
        parse_ratings(html)
    except EmptyRatingsError:
        log.info("No archived ratings for %s (no games that day)", snapshot_date)
        return None
    except KenPomParseError as exc:
        if "ratings-table" in str(exc):
            raise KenPomAuthError(
                f"archive.php had no ratings table; login likely failed ({exc})"
            ) from exc
        raise

    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


# --------------------------------------------------------------------------
# Persistence + entry point
# --------------------------------------------------------------------------

# Upsert on (snapshot_date, team). A NULL three_pa_rate (archive row) never
# overwrites a known value from a live scrape of the same date.
UPSERT_SQL = """
    INSERT INTO kenpom_snapshots
        (snapshot_date, team, adj_em, adj_o, adj_d, adj_t, three_pa_rate)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (snapshot_date, team) DO UPDATE SET
        adj_em = excluded.adj_em,
        adj_o = excluded.adj_o,
        adj_d = excluded.adj_d,
        adj_t = excluded.adj_t,
        three_pa_rate = COALESCE(excluded.three_pa_rate, kenpom_snapshots.three_pa_rate)
"""


def upsert_snapshot(rows: list[SnapshotRow], db_path: str | Path | None = None) -> int:
    with get_db_connection(db_path=db_path) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            con.executemany(UPSERT_SQL, [r.as_tuple() for r in rows])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return len(rows)


def ingest(
    snapshot_date: date | None = None,
    db_path: str | Path | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
) -> list[SnapshotRow]:
    today = today or date.today()
    snapshot_date = snapshot_date or today
    ratings_html, teamstats_html = load_pages(snapshot_date, cache_dir, session, today)
    rows = build_snapshot(snapshot_date, ratings_html, teamstats_html)
    upsert_snapshot(rows, db_path)
    return rows


@dataclass
class BackfillResult:
    ingested: dict[date, int] = field(default_factory=dict)
    no_data: list[date] = field(default_factory=list)
    failed: dict[date, str] = field(default_factory=dict)


def backfill(
    start: date,
    end: date,
    db_path: str | Path | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
    delay: float = DEFAULT_BACKFILL_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> BackfillResult:
    """Ingest archived ratings for every date in [start, end] (inclusive).

    Off-days are skipped; per-date HTTP/parse failures are recorded and the
    run continues. Auth failures abort, since every later date would fail too,
    as do MAX_CONSECUTIVE_FAILURES failed dates in a row (likely throttling).
    Each date is committed as it lands and cached dates are never refetched,
    so an aborted run can simply be rerun.
    """
    today = today or date.today()
    if start > end:
        raise KenPomError(f"--start {start} is after --end {end}")
    if end >= today:
        raise KenPomError(f"--end must be before today ({today}); got {end}")

    session = session or new_session()
    result = BackfillResult()
    fetched_any = False
    consecutive_failures = 0
    day = start
    while day <= end:
        needs_fetch = not archive_cache_path(cache_dir, day).exists()
        if needs_fetch and fetched_any:
            sleep(delay * random.uniform(1.0, 1.0 + BACKFILL_JITTER))
        try:
            html = load_archive_page(day, cache_dir, session, today, sleep)
            if html is None:
                result.no_data.append(day)
            else:
                rows = build_archive_snapshot(day, html)
                result.ingested[day] = upsert_snapshot(rows, db_path)
            consecutive_failures = 0
        except KenPomAuthError:
            raise
        except (requests.RequestException, KenPomError) as exc:
            log.warning("Failed %s: %s", day, exc)
            result.failed[day] = str(exc)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise KenPomError(
                    f"Aborting backfill at {day}: {consecutive_failures} consecutive "
                    f"failures (possibly rate limited). Last error: {exc}. "
                    "Rerun later; cached dates are skipped."
                ) from exc
        fetched_any = fetched_any or needs_fetch
        day += timedelta(days=1)
    return result


def load_env_files(paths: Iterable[Path]) -> None:
    """Minimal .env loader: KEY=VALUE lines; never overrides existing env vars."""
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest KenPom ratings into DuckDB: today's live snapshot (default) "
        "or an archive backfill over --start..--end."
    )
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="Live snapshot date YYYY-MM-DD (default: today; past dates "
                        "must be cached)")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="Archive backfill start date (enables backfill mode)")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="Archive backfill end date, inclusive (default: yesterday)")
    parser.add_argument("--delay", type=float, default=DEFAULT_BACKFILL_DELAY,
                        help="Seconds between archive fetches")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_env_files([PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"])

    if args.start is not None:
        if args.date is not None:
            parser.error("--date can't be combined with --start/--end")
        end = args.end or date.today() - timedelta(days=1)
        try:
            res = backfill(args.start, end, args.db_path, args.cache_dir, delay=args.delay)
        except KenPomError as exc:
            log.error("%s", exc)
            return 1
        print(f"Backfilled {len(res.ingested)} dates ({sum(res.ingested.values())} rows) "
              f"from {args.start} to {end}; {len(res.no_data)} off-days skipped; "
              f"{len(res.failed)} failed")
        for d, err in res.failed.items():
            print(f"  FAILED {d}: {err}")
        return 1 if res.failed else 0

    try:
        rows = ingest(args.date, args.db_path, args.cache_dir)
    except KenPomError as exc:
        log.error("%s", exc)
        return 1

    missing = sum(r.three_pa_rate is None for r in rows)
    print(f"Upserted {len(rows)} teams for {rows[0].snapshot_date} "
          f"({missing} missing 3PA rate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
