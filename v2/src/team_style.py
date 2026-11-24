"""Team style profiles from KenPom: how each team plays on offense and defense.

Three subscriber pages give one row per team:

* ``stats.php`` (four factors): adjusted tempo, AdjO/AdjD, and eFG%, TO%,
  OR% and FT rate for the offense and the defense.
* ``teamstats.php`` (offense): 3P%, 2P%, FT%, Blk% (shots blocked), Stl%
  (stolen from), NST% (non-steal turnovers), 2P Dist (average 2-point shot
  distance, feet), A% (assisted makes), 3PA% (3PA / FGA).
* ``teamstats.php?od=d`` (defense): the same stats for opponents, with Blk%
  and Stl% as the team's own block and steal rates.

Percent stats are stored as percents (54.5, not 0.545). ``season`` uses
KenPom's convention: 2027 is the 2026-27 season.

Two kinds of snapshot share the ``team_style`` table:

* **Live** -- today's season-to-date pages, stamped with today's date. As with
  ratings, a past date can only come from the cache (KenPom serves current
  numbers only), so live stats are never back-dated.
* **Season final** -- ``?y=SEASON`` pages for a finished season, stamped
  April 30 of that season (after every game), so an ``as-of`` query never
  hands end-of-season stats to a game played during that season.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import requests

from src import ingest_kenpom as ik

log = logging.getLogger(__name__)

FOUR_FACTORS_URL = f"{ik.BASE_URL}/stats.php"
TEAMSTATS_URL = f"{ik.BASE_URL}/teamstats.php"

# Column groups (value + rank) in page order -> stored names.
FOUR_FACTOR_GROUPS = [
    ("adjtempo", "tempo"), ("adjoe", "adj_o"),
    ("efg%", "off_efg"), ("to%", "off_to"), ("or%", "off_or"), ("ftrate", "off_ftr"),
    ("adjde", "adj_d"),
    ("efg%", "def_efg"), ("to%", "def_to"), ("or%", "def_or"), ("ftrate", "def_ftr"),
]
_TEAMSTAT_LABELS = ["3p%", "2p%", "ft%", "blk%", "stl%", "nst%", "2pdist", "a%", "3pa%"]
_TEAMSTAT_NAMES = ["3p", "2p", "ft", "blk", "stl", "nst", "2pdist", "ast", "3pa"]

STYLE_COLUMNS = (
    ["tempo", "adj_o", "adj_d"]
    + [f"off_{n}" for n in ("efg", "to", "or", "ftr")]
    + [f"def_{n}" for n in ("efg", "to", "or", "ftr")]
    + [f"off_{n}" for n in _TEAMSTAT_NAMES]
    + [f"def_{n}" for n in _TEAMSTAT_NAMES]
)

# Loose sanity ranges; anything outside means a column shifted.
RANGES = {"tempo": (50, 90), "adj_o": (70, 150), "adj_d": (70, 150), "2pdist": (0, 25)}
PCT_RANGE = (0, 100)


def season_for(day: date) -> int:
    """KenPom season label for a date: Nov 2026 -> 2027."""
    return day.year + 1 if day.month >= 7 else day.year


def final_snapshot_date(season: int) -> date:
    return date(season, 4, 30)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _groups(headers: list[str]) -> list[tuple[str, int]]:
    """(label, value_index) for each column group after Team/Conf."""
    cols = [i for i, label in enumerate(headers) if label not in ("team", "conf")]
    out = []
    for k in range(0, len(cols), 2):
        i = cols[k]
        if k + 1 >= len(cols) or headers[cols[k + 1]] != headers[i]:
            raise ik.KenPomParseError(f"unexpected header layout near {headers[i]!r}: {headers}")
        out.append((headers[i], i))
    return out


def _parse_page(html: str, expected: list[tuple[str, str]], page: str) -> dict[str, dict[str, float]]:
    table, headers = ik._find_table_and_headers(html)
    team_idx = ik._column_index(headers, ("team",), page)
    groups = _groups(headers)
    labels = [g[0] for g in groups]
    want = [e[0] for e in expected]
    if labels[: len(want)] != want:
        raise ik.KenPomParseError(f"{page}: expected columns {want}, got {labels}")
    idx = {name: groups[k][1] for k, (_, name) in enumerate(expected)}
    out: dict[str, dict[str, float]] = {}
    for team, cells in ik._team_rows(table, team_idx):
        if len(cells) <= max(idx.values()):
            continue
        out[team] = {name: ik._to_float(cells[i], team, name) for name, i in idx.items()}
    if not out:
        raise ik.KenPomParseError(f"{page}: no team rows parsed")
    return out


def parse_four_factors(html: str) -> dict[str, dict[str, float]]:
    return _parse_page(html, FOUR_FACTOR_GROUPS, "four factors")


def parse_teamstats(html: str, side: str) -> dict[str, dict[str, float]]:
    """side='off' (default page, ends in AdjOE) or 'def' (od=d, ends in AdjDE)."""
    last = ("adjoe", "_adj") if side == "off" else ("adjde", "_adj")
    expected = [(lab, f"{side}_{n}") for lab, n in zip(_TEAMSTAT_LABELS, _TEAMSTAT_NAMES)] + [last]
    rows = _parse_page(html, expected, f"teamstats ({side})")
    for r in rows.values():
        r.pop("_adj")
    return rows


def _check(team: str, row: dict[str, float]) -> None:
    for name, value in row.items():
        key = name.split("_", 1)[-1] if name.startswith(("off_", "def_")) else name
        lo, hi = RANGES.get(key, PCT_RANGE)
        if not lo <= value <= hi:
            raise ik.KenPomParseError(f"{team}: {name}={value} outside [{lo}, {hi}]")


def build_style_rows(ff_html: str, off_html: str, def_html: str) -> dict[str, dict[str, float]]:
    ff, off, dfn = parse_four_factors(ff_html), parse_teamstats(off_html, "off"), parse_teamstats(def_html, "def")
    teams = set(ff) & set(off) & set(dfn)
    missing = (set(ff) | set(off) | set(dfn)) - teams
    if missing:
        log.warning("style: %d teams missing from a page: %s", len(missing), sorted(missing)[:5])
    rows = {t: {**ff[t], **off[t], **dfn[t]} for t in teams}
    for t, r in rows.items():
        _check(t, r)
    return rows


# --------------------------------------------------------------------------
# Fetching + caching
# --------------------------------------------------------------------------

def _cache_names(snapshot_date: date | None, season: int | None) -> tuple[str, str, str]:
    if season is not None:
        tag = f"y{season}"
        return (f"kenpom_fourfactors_{tag}.html", f"kenpom_teamstats_{tag}.html",
                f"kenpom_teamstats_def_{tag}.html")
    d = snapshot_date.isoformat()
    # The offense page is the one `cli.py ingest` already caches for 3PA%.
    return (f"kenpom_fourfactors_{d}.html", f"kenpom_teamstats_{d}.html",
            f"kenpom_teamstats_def_{d}.html")


def _urls(season: int | None) -> tuple[str, str, str]:
    y = f"y={season}" if season is not None else ""
    return (f"{FOUR_FACTORS_URL}{'?' + y if y else ''}",
            f"{TEAMSTATS_URL}{'?' + y if y else ''}",
            f"{TEAMSTATS_URL}?{y + '&' if y else ''}od=d")


def load_style_pages(
    snapshot_date: date | None = None,
    season: int | None = None,
    cache_dir: Path = ik.DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = 2.0,
) -> tuple[str, str, str]:
    """(four_factors, offense, defense) HTML for a live date or a finished season."""
    today = today or date.today()
    if season is not None:
        if season >= season_for(today):
            raise ik.KenPomError(f"season {season} isn't finished; use a live snapshot")
    else:
        snapshot_date = snapshot_date or today
    paths = [cache_dir / n for n in _cache_names(snapshot_date, season)]
    if season is None and snapshot_date != today and not all(p.exists() for p in paths):
        raise ik.LeakageError(
            f"No cached style pages for {snapshot_date}; KenPom only serves current "
            "stats, so a past date can't be fetched.")

    parsers = [parse_four_factors, lambda h: parse_teamstats(h, "off"), lambda h: parse_teamstats(h, "def")]
    pages = []
    fetched = False
    for path, url, parse in zip(paths, _urls(season), parsers):
        if path.exists():
            pages.append(path.read_text(encoding="utf-8"))
            continue
        if session is None:
            session = ik.new_session()
        ik.login(session)
        if fetched:
            sleep(delay)
        log.info("Fetching %s", url)
        html = ik._get(session, url, sleep)
        fetched = True
        try:
            parse(html)
        except ik.KenPomParseError as exc:
            raise ik.KenPomAuthError(f"{url}: no stats table; login likely failed ({exc})") from exc
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        pages.append(html)
    return tuple(pages)  # type: ignore[return-value]


def store_style(con: duckdb.DuckDBPyConnection, snapshot_date: date, season: int,
                rows: dict[str, dict[str, float]]) -> int:
    cols = ", ".join(STYLE_COLUMNS)
    marks = ", ".join("?" * (len(STYLE_COLUMNS) + 3))
    con.execute("DELETE FROM team_style WHERE snapshot_date = ?", [snapshot_date])
    con.executemany(
        f"INSERT INTO team_style (snapshot_date, season, team, {cols}) VALUES ({marks})",
        [[snapshot_date, season, t] + [r[c] for c in STYLE_COLUMNS] for t, r in sorted(rows.items())],
    )
    return len(rows)


def ingest_style(
    con: duckdb.DuckDBPyConnection,
    snapshot_date: date | None = None,
    season: int | None = None,
    cache_dir: Path = ik.DEFAULT_CACHE_DIR,
    session: requests.Session | None = None,
    today: date | None = None,
) -> tuple[date, int]:
    """Store a live (today / cached date) or season-final style snapshot."""
    today = today or date.today()
    pages = load_style_pages(snapshot_date, season, cache_dir, session, today)
    rows = build_style_rows(*pages)
    if season is not None:
        snap = final_snapshot_date(season)
    else:
        snap = snapshot_date or today
        season = season_for(snap)
    return snap, store_style(con, snap, season, rows)
