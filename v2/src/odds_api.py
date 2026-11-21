"""The Odds API client: live NCAAB slate, market lines, and final scores.

Endpoints (v4, sport ``basketball_ncaab``):
  * ``/odds``   markets h2h,spreads,totals for DraftKings + FanDuel
                (cost 3 credits: markets x 1 region-equivalent)
  * ``/scores`` completed games for the last ``days_from`` days (cost 2)

Free tier is 500 credits/month; one ``update`` (odds + scores) costs 5.

Storage (team A = the feed's home team; KenPom names via TeamNameMapper):
  * ``scheduled_games``: one row per event (commence_time in UTC).
  * ``historical_games``: inserted with NULL scores when first seen; scores
    filled from ``/scores``. ``hca_eff`` = DEFAULT_HCA_EFF because the feed has
    no neutral-site flag (holiday tournaments get home-court; known bias).
  * ``odds_snapshots``: every pull taken before tipoff, per book.
  * ``vegas_closing_odds``: refreshed from the latest pre-tipoff snapshot
    (DraftKings, then FanDuel), so after tipoff it holds the closing line.
Raw JSON responses are cached under ``data/raw_cache/``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

from src.db import PROJECT_ROOT
from src.import_v1 import game_id_for
from src.simulate_game import DEFAULT_HCA_EFF
from src.team_names import TeamNameMapper

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "basketball_ncaab"
BOOKS = ("draftkings", "fanduel")
EASTERN = ZoneInfo("America/New_York")
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "raw_cache"
REQUEST_TIMEOUT = 30


class OddsApiError(RuntimeError):
    pass


def utc_now() -> datetime:
    """Naive UTC 'now' (all timestamps in the odds tables are naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def eastern_date(commence_utc: datetime) -> date:
    """Game date as US/Eastern calendar day (a 7 pm PT tip is 'tonight')."""
    return commence_utc.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()


def get_api_key() -> str:
    key = os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    if not key:
        raise OddsApiError("Set THE_ODDS_API_KEY in .env")
    return key


@dataclass
class Quota:
    remaining: str | None = None
    used: str | None = None
    last_cost: str | None = None


def _get(session: requests.Session, path: str, params: dict, quota: Quota) -> list:
    resp = session.get(f"{BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT)
    quota.remaining = resp.headers.get("x-requests-remaining", quota.remaining)
    quota.used = resp.headers.get("x-requests-used", quota.used)
    quota.last_cost = resp.headers.get("x-requests-last", quota.last_cost)
    if resp.status_code == 401:
        raise OddsApiError("The Odds API rejected the key (401)")
    if resp.status_code == 429:
        raise OddsApiError("The Odds API quota/rate limit reached (429)")
    resp.raise_for_status()
    return resp.json()


def fetch_odds(session: requests.Session, key: str, quota: Quota) -> list:
    return _get(session, f"/sports/{SPORT}/odds", {
        "apiKey": key, "regions": "us", "markets": "h2h,spreads,totals",
        "oddsFormat": "american", "dateFormat": "iso", "bookmakers": ",".join(BOOKS),
    }, quota)


def fetch_scores(session: requests.Session, key: str, quota: Quota, days_from: int = 3) -> list:
    return _get(session, f"/sports/{SPORT}/scores",
                {"apiKey": key, "daysFrom": days_from, "dateFormat": "iso"}, quota)


# --------------------------------------------------------------------------
# Parsing (pure)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduledGame:
    game_id: str
    source_event_id: str
    commence_time: datetime
    game_date: date
    team_a: str
    team_b: str


@dataclass(frozen=True)
class OddsSnapshot:
    game_id: str
    fetched_at: datetime
    book: str
    spread_a: float | None
    ml_a: float | None
    ml_b: float | None
    total: float | None


def _map_event(event: dict, mapper: TeamNameMapper, unmatched: set) -> ScheduledGame | None:
    home, away = mapper.to_kenpom(event.get("home_team")), mapper.to_kenpom(event.get("away_team"))
    for raw, kp in ((event.get("home_team"), home), (event.get("away_team"), away)):
        if kp is None:
            unmatched.add(raw)
    if home is None or away is None:
        return None
    commence = parse_utc(event["commence_time"])
    gd = eastern_date(commence)
    return ScheduledGame(game_id_for(gd, home, away), event["id"], commence, gd, home, away)


def _book_snapshot(game: ScheduledGame, book: dict, home_raw: str, away_raw: str,
                   fetched_at: datetime) -> OddsSnapshot:
    spread_a = ml_a = ml_b = total = None
    for market in book.get("markets", []):
        outcomes = {o.get("name"): o for o in market.get("outcomes", [])}
        if market.get("key") == "spreads":
            h, a = outcomes.get(home_raw), outcomes.get(away_raw)
            if h and a and h.get("point") is not None and abs(h["point"] + a["point"]) < 1e-9:
                spread_a = float(h["point"])
        elif market.get("key") == "h2h":
            h, a = outcomes.get(home_raw), outcomes.get(away_raw)
            if h and a and abs(h["price"]) >= 100 and abs(a["price"]) >= 100:
                ml_a, ml_b = float(h["price"]), float(a["price"])
        elif market.get("key") == "totals":
            over = outcomes.get("Over")
            if over and over.get("point") is not None:
                total = float(over["point"])
    return OddsSnapshot(game.game_id, fetched_at, book["key"], spread_a, ml_a, ml_b, total)


def parse_odds(events: list, mapper: TeamNameMapper, fetched_at: datetime):
    """-> (scheduled games, pre-tipoff snapshots, unmatched team names)."""
    games, snaps, unmatched = [], [], set()
    for event in events:
        g = _map_event(event, mapper, unmatched)
        if g is None:
            continue
        games.append(g)
        if fetched_at >= g.commence_time:
            continue  # in-play lines are not pre-game market prices
        for book in event.get("bookmakers", []):
            if book.get("key") in BOOKS:
                snap = _book_snapshot(g, book, event["home_team"], event["away_team"], fetched_at)
                if any(v is not None for v in (snap.spread_a, snap.ml_a, snap.total)):
                    snaps.append(snap)
    return games, snaps, unmatched


def parse_scores(events: list, mapper: TeamNameMapper):
    """-> (list of (ScheduledGame, score_a, score_b) for completed games, unmatched)."""
    finals, unmatched = [], set()
    for event in events:
        if not event.get("completed") or not event.get("scores"):
            continue
        g = _map_event(event, mapper, unmatched)
        if g is None:
            continue
        pts = {s.get("name"): s.get("score") for s in event["scores"]}
        try:
            a, b = int(pts[event["home_team"]]), int(pts[event["away_team"]])
        except (KeyError, TypeError, ValueError):
            continue
        finals.append((g, a, b))
    return finals, unmatched


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def store_games(con: duckdb.DuckDBPyConnection, games: list[ScheduledGame]) -> None:
    for g in games:
        con.execute(
            "INSERT OR REPLACE INTO scheduled_games VALUES (?, 'the-odds-api', ?, ?, ?, ?)",
            [g.game_id, g.source_event_id, g.commence_time, g.team_a, g.team_b])
        con.execute(
            "INSERT INTO historical_games VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?) "
            "ON CONFLICT (game_id) DO NOTHING",
            [g.game_id, g.game_date, g.team_a, g.team_b, DEFAULT_HCA_EFF])


def store_snapshots(con: duckdb.DuckDBPyConnection, snaps: list[OddsSnapshot]) -> None:
    for s in snaps:
        con.execute("INSERT INTO odds_snapshots VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    [s.game_id, s.fetched_at, s.book, s.spread_a, s.ml_a, s.ml_b, s.total])


def market_line(con: duckdb.DuckDBPyConnection, game_id: str, latest_at: datetime | None = None,
                earliest: bool = False) -> dict:
    """Spread / ML / total for a game from odds_snapshots.

    Uses the latest snapshot at or before ``latest_at`` (default: any), or the
    earliest when ``earliest``; each field comes from the first book in BOOKS
    order that has it.
    """
    order = "ASC" if earliest else "DESC"
    rows = con.execute(
        f"""SELECT book, spread_a, ml_a, ml_b, total FROM odds_snapshots
            WHERE game_id = ? AND (? IS NULL OR fetched_at <= ?)
            ORDER BY fetched_at {order}""",
        [game_id, latest_at, latest_at]).fetchall()
    out = {"spread_a": None, "ml_a": None, "ml_b": None, "total": None}
    for book in BOOKS:
        book_rows = [r for r in rows if r[0] == book]
        for r in book_rows:  # already in preference order (latest/earliest first)
            if out["spread_a"] is None and r[1] is not None:
                out["spread_a"] = r[1]
            if out["ml_a"] is None and r[2] is not None and r[3] is not None:
                out["ml_a"], out["ml_b"] = r[2], r[3]
            if out["total"] is None and r[4] is not None:
                out["total"] = r[4]
    return out


def refresh_closing_odds(con: duckdb.DuckDBPyConnection, game_ids: list[str]) -> int:
    n = 0
    for gid in game_ids:
        m = market_line(con, gid)
        if m["spread_a"] is None and m["ml_a"] is None:
            continue
        con.execute("INSERT OR REPLACE INTO vegas_closing_odds VALUES (?, ?, ?, ?)",
                    [gid, m["spread_a"], m["ml_a"], m["ml_b"]])
        n += 1
    return n


def store_finals(con: duckdb.DuckDBPyConnection, finals: list) -> int:
    for g, a, b in finals:
        store_games(con, [g])
        con.execute("UPDATE historical_games SET actual_score_a = ?, actual_score_b = ? "
                    "WHERE game_id = ?", [a, b, g.game_id])
    return len(finals)


@dataclass
class UpdateStats:
    events: int = 0
    games: int = 0
    snapshots: int = 0
    closing_lines: int = 0
    finals: int = 0
    unmatched: set = field(default_factory=set)
    quota: Quota = field(default_factory=Quota)


def current_teams(con: duckdb.DuckDBPyConnection, as_of: date) -> list[str]:
    """KenPom teams in the latest snapshot on or before ``as_of``. Matching feed
    names against *every* team ever stored would hit names KenPom has since
    retired (e.g. "New Orleans" -> "LSU New Orleans" in 2026-27) and price the
    game off last season's ratings."""
    rows = con.execute(
        "SELECT team FROM kenpom_snapshots WHERE snapshot_date = "
        "(SELECT max(snapshot_date) FROM kenpom_snapshots WHERE snapshot_date <= ?)", [as_of]).fetchall()
    return [r[0] for r in rows]


def update(
    con: duckdb.DuckDBPyConnection,
    session: requests.Session | None = None,
    key: str | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    scores_days: int = 3,
    now: datetime | None = None,
) -> UpdateStats:
    """Pull odds + scores once and store everything (5 credits)."""
    session = session or requests.Session()
    key = key or get_api_key()
    now = now or utc_now()
    stats = UpdateStats()
    mapper = TeamNameMapper(current_teams(con, eastern_date(now)))

    events = fetch_odds(session, key, stats.quota)
    scores = fetch_scores(session, key, stats.quota, scores_days)
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    (cache_dir / f"odds_{stamp}.json").write_text(json.dumps(events))
    (cache_dir / f"scores_{stamp}.json").write_text(json.dumps(scores))

    games, snaps, unmatched = parse_odds(events, mapper, now)
    finals, unmatched_scores = parse_scores(scores, mapper)
    store_games(con, games)
    store_snapshots(con, snaps)
    stats.closing_lines = refresh_closing_odds(con, sorted({s.game_id for s in snaps}))
    stats.finals = store_finals(con, finals)
    stats.events, stats.games, stats.snapshots = len(events), len(games), len(snaps)
    stats.unmatched = unmatched | unmatched_scores
    return stats
