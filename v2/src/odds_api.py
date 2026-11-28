"""The Odds API client: live NCAAB slate, market lines, and final scores.

Endpoints (v4, sport ``basketball_ncaab``):
  * ``/odds``   markets h2h,spreads,totals for up to 10 US books (BOOKS)
                (cost 3 credits: markets x 1 region-equivalent; the API
                bills every 10 named bookmakers as one region)
  * ``/scores`` completed games for the last ``days_from`` days (cost 2)

Free tier is 500 credits/month; one ``update`` (odds + scores) costs 5, an
odds-only pre-tip pull (``update(scores=False)``) costs 3.

Storage (team A = the feed's home team; KenPom names via TeamNameMapper):
  * ``scheduled_games``: one row per event (commence_time in UTC).
  * ``historical_games``: inserted with NULL scores when first seen; scores
    filled from ``/scores``. ``hca_eff`` = DEFAULT_HCA_EFF because the feed has
    no neutral-site flag (holiday tournaments get home-court; known bias).
  * ``odds_snapshots``: every pull taken before tipoff, per book, with the
    spread prices.
  * ``vegas_closing_odds``: refreshed from the latest pre-tipoff pull, so
    after tipoff it holds the closing line.

Market vs best line: ``market_line`` gives the consensus -- the median spread
and total across books in one pull, moneylines from the first book in BOOKS
order. ``best_line`` gives the most favorable spread for one side across
those books (line shopping); picks are chosen against the consensus and
graded at the best line.
Raw JSON responses are cached under ``data/raw_cache/``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from statistics import median
from datetime import date, datetime, timedelta, timezone
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
# Legal US sportsbooks, in preference order for moneylines (max 10 = one
# region's cost). Offshore books are deliberately excluded.
BOOKS = ("draftkings", "fanduel", "betmgm", "williamhill_us", "espnbet",
         "fanatics", "betrivers", "hardrockbet", "ballybet", "betparx")
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
              "williamhill_us": "Caesars", "espnbet": "ESPN BET", "fanatics": "Fanatics",
              "betrivers": "BetRivers", "hardrockbet": "Hard Rock", "ballybet": "Bally Bet",
              "betparx": "betPARX"}
# Best-line shopping ignores spreads priced worse than this (e.g. -3.5 at -140).
WORST_SPREAD_PRICE = -125.0
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
    """US/Eastern calendar day of a UTC timestamp."""
    return commence_utc.replace(tzinfo=timezone.utc).astimezone(EASTERN).date()


# Games tipping before this hour (ET) belong to the previous night's slate: a
# 9:30 pm PT tip is 12:30 am ET, after midnight but before any morning run, so
# dating it by the ET calendar day would leave it in no slate (it starts before
# that day's picks lock). ESPN's scoreboard dates these games the same way.
SLATE_CUTOFF_HOUR = 6


def slate_date(commence_utc: datetime) -> date:
    """The slate (game day) a tip belongs to: the ET date, with tips before
    SLATE_CUTOFF_HOUR ET counted as the previous night's games."""
    et = commence_utc.replace(tzinfo=timezone.utc).astimezone(EASTERN)
    return (et - timedelta(hours=SLATE_CUTOFF_HOUR)).date()


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
    spread_price_a: float | None = None
    spread_price_b: float | None = None


def _map_event(event: dict, mapper: TeamNameMapper, unmatched: set) -> ScheduledGame | None:
    home, away = mapper.to_kenpom(event.get("home_team")), mapper.to_kenpom(event.get("away_team"))
    for raw, kp in ((event.get("home_team"), home), (event.get("away_team"), away)):
        if kp is None:
            unmatched.add(raw)
    if home is None or away is None:
        return None
    commence = parse_utc(event["commence_time"])
    gd = slate_date(commence)
    return ScheduledGame(game_id_for(gd, home, away), event["id"], commence, gd, home, away)


def _book_snapshot(game: ScheduledGame, book: dict, home_raw: str, away_raw: str,
                   fetched_at: datetime) -> OddsSnapshot:
    spread_a = ml_a = ml_b = total = price_a = price_b = None
    for market in book.get("markets", []):
        outcomes = {o.get("name"): o for o in market.get("outcomes", [])}
        if market.get("key") == "spreads":
            h, a = outcomes.get(home_raw), outcomes.get(away_raw)
            if h and a and h.get("point") is not None and abs(h["point"] + a["point"]) < 1e-9:
                spread_a = float(h["point"])
                price_a = float(h["price"]) if h.get("price") is not None else None
                price_b = float(a["price"]) if a.get("price") is not None else None
        elif market.get("key") == "h2h":
            h, a = outcomes.get(home_raw), outcomes.get(away_raw)
            if h and a and abs(h["price"]) >= 100 and abs(a["price"]) >= 100:
                ml_a, ml_b = float(h["price"]), float(a["price"])
        elif market.get("key") == "totals":
            over = outcomes.get("Over")
            if over and over.get("point") is not None:
                total = float(over["point"])
    return OddsSnapshot(game.game_id, fetched_at, book["key"], spread_a, ml_a, ml_b, total,
                        price_a, price_b)


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
        con.execute("INSERT INTO odds_snapshots (game_id, fetched_at, book, spread_a, ml_a, ml_b, total, "
                    "spread_price_a, spread_price_b) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    [s.game_id, s.fetched_at, s.book, s.spread_a, s.ml_a, s.ml_b, s.total,
                     s.spread_price_a, s.spread_price_b])


def market_line(con: duckdb.DuckDBPyConnection, game_id: str, latest_at: datetime | None = None,
                earliest: bool = False) -> dict:
    """Consensus market for a game from odds_snapshots.

    Uses the latest pull at or before ``latest_at`` (default: any), or the
    earliest when ``earliest``, that has the field: the median spread and total
    across books in that pull (not rounded: with an even number of books the
    median can be a quarter point, and rounding would arbitrarily pick a side),
    and moneylines from the first book in BOOKS order. ``lines`` holds that pull's per-book
    spreads as {book: (spread_a, price_a, price_b)} for ``best_line``.
    """
    order = "ASC" if earliest else "DESC"
    rows = con.execute(
        f"""SELECT fetched_at, book, spread_a, ml_a, ml_b, total, spread_price_a, spread_price_b
            FROM odds_snapshots
            WHERE game_id = ? AND (? IS NULL OR fetched_at <= ?)
            ORDER BY fetched_at {order}""",
        [game_id, latest_at, latest_at]).fetchall()
    pulls: dict[datetime, list] = {}
    for r in rows:                      # insertion order = preference order of pulls
        pulls.setdefault(r[0], []).append(r)
    rank = {b: i for i, b in enumerate(BOOKS)}
    out = {"spread_a": None, "ml_a": None, "ml_b": None, "total": None, "lines": {}}
    for pull in pulls.values():
        pull = sorted(pull, key=lambda r: rank.get(r[1], len(BOOKS)))
        spreads = [r for r in pull if r[2] is not None]
        if out["spread_a"] is None and spreads:
            out["spread_a"] = float(median(r[2] for r in spreads))
            out["lines"] = {r[1]: (r[2], r[6], r[7]) for r in spreads}
        if out["ml_a"] is None:
            ml = next((r for r in pull if r[3] is not None and r[4] is not None), None)
            if ml:
                out["ml_a"], out["ml_b"] = ml[3], ml[4]
        totals = [r[5] for r in pull if r[5] is not None]
        if out["total"] is None and totals:
            out["total"] = float(median(totals))
    return out


def best_line(lines: dict, side: str | None, fallback: float | None = None
              ) -> tuple[float | None, str | None, float | None]:
    """Most favorable spread for ``side`` ("A" or "B") across books, as
    (spread_a, book, price). Side A wants the highest spread_a (more points),
    side B the lowest; ties go to the better price, then BOOKS order. Spreads
    priced worse than WORST_SPREAD_PRICE are skipped. With no usable book,
    returns (fallback, None, None)."""
    if side not in ("A", "B") or not lines:
        return fallback, None, None
    rank = {b: i for i, b in enumerate(BOOKS)}
    cands = []
    for book, (spread_a, price_a, price_b) in lines.items():
        price = price_a if side == "A" else price_b
        if price is not None and price < WORST_SPREAD_PRICE:
            continue
        points = spread_a if side == "A" else -spread_a
        cands.append((-points, -(price if price is not None else -110.0), rank.get(book, len(BOOKS)),
                      spread_a, book, price))
    if not cands:
        return fallback, None, None
    _, _, _, spread_a, book, price = min(cands)
    return spread_a, book, price


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


def canonical_game(con: duckdb.DuckDBPyConnection, g: ScheduledGame) -> tuple[str, bool]:
    """(game_id to use, flipped) for a feed game. The Odds API keeps one event id
    per game, but its odds and scores feeds (or two pulls of the same feed) can
    disagree on which team is home -- typically neutral-site tournament games.
    Building the id from (date, home, away) would then create a second, phantom
    game and the locked pick would never be graded. Reuse the stored game for
    the event id instead; ``flipped`` = the feed's home team is our team_b."""
    row = con.execute(
        """SELECT s.game_id, s.team_a FROM scheduled_games s
           WHERE s.source_event_id = ?
           ORDER BY EXISTS (SELECT 1 FROM model_predictions p WHERE p.game_id = s.game_id) DESC, s.game_id
           LIMIT 1""", [g.source_event_id]).fetchone()
    if row is None or row[0] == g.game_id:
        return g.game_id, False
    return row[0], row[1] != g.team_a


def reconcile(con: duckdb.DuckDBPyConnection, games: list, snaps: list) -> tuple[list, list]:
    """Map feed games/snapshots onto already-stored games with the same event id
    (flipping spread and moneylines when the feed swapped home and away)."""
    keep, remap = [], {}
    for g in games:
        cid, flipped = canonical_game(con, g)
        if cid == g.game_id:
            keep.append(g)
        else:
            remap[g.game_id] = (cid, flipped)
    out = []
    for snap in snaps:
        if snap.game_id not in remap:
            out.append(snap)
            continue
        cid, flipped = remap[snap.game_id]
        if flipped:
            snap = replace(snap, spread_a=None if snap.spread_a is None else -snap.spread_a,
                           ml_a=snap.ml_b, ml_b=snap.ml_a)
        out.append(replace(snap, game_id=cid))
    return keep, out


def store_finals(con: duckdb.DuckDBPyConnection, finals: list) -> int:
    for g, a, b in finals:
        cid, flipped = canonical_game(con, g)
        if cid == g.game_id:
            store_games(con, [g])
        elif flipped:
            a, b = b, a
        con.execute("UPDATE historical_games SET actual_score_a = ?, actual_score_b = ? "
                    "WHERE game_id = ?", [a, b, cid])
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
    scores: bool = True,
) -> UpdateStats:
    """Pull odds (+ scores unless ``scores=False``) once and store everything
    (5 credits; 3 for an odds-only pre-tip pull)."""
    session = session or requests.Session()
    key = key or get_api_key()
    now = now or utc_now()
    stats = UpdateStats()
    mapper = TeamNameMapper(current_teams(con, eastern_date(now)))

    events = fetch_odds(session, key, stats.quota)
    fetch_scores_too = scores
    scores = fetch_scores(session, key, stats.quota, scores_days) if fetch_scores_too else []
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    (cache_dir / f"odds_{stamp}.json").write_text(json.dumps(events))
    if fetch_scores_too:
        (cache_dir / f"scores_{stamp}.json").write_text(json.dumps(scores))

    games, snaps, unmatched = parse_odds(events, mapper, now)
    finals, unmatched_scores = parse_scores(scores, mapper)
    games, snaps = reconcile(con, games, snaps)
    store_games(con, games)
    store_snapshots(con, snaps)
    stats.closing_lines = refresh_closing_odds(con, sorted({s.game_id for s in snaps}))
    stats.finals = store_finals(con, finals)
    stats.events, stats.games, stats.snapshots = len(events), len(games), len(snaps)
    stats.unmatched = unmatched | unmatched_scores
    return stats
