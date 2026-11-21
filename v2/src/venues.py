"""Venues and neutral sites from ESPN's public scoreboard.

The Odds API has no venue or neutral-site flag, so every game used to be
priced with home-court advantage for whichever team the odds feed listed as
home. In November that's wrong for roughly a quarter of the slate (multi-team
events: 15 of 59 D-I games on 2026-11-20), and the odds and scores feeds
often disagree on who is "home" for those games.

ESPN's scoreboard (no key; one request per day, cached to
data/raw_cache/espn_YYYY-MM-DD.json) gives each game's venue and a
``neutralSite`` flag. ``update`` matches ESPN games to our scheduled games by
date + team pair (either orientation), stores them in ``game_venues`` and, for
neutral sites, sets ``historical_games.hca_eff = 0`` so picks that are not yet
locked are simulated with no home-court edge. Already-locked picks are never
changed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
import requests

from src.ingest_kenpom import DEFAULT_CACHE_DIR
from src.odds_api import current_teams, utc_now
from src.team_names import TeamNameMapper

log = logging.getLogger(__name__)

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
USER_AGENT = "Mozilla/5.0 (compatible; cbb-engine/1.0)"


@dataclass(frozen=True)
class Venue:
    home: str               # KenPom names, ESPN orientation
    away: str
    neutral: bool
    venue: str | None
    city: str | None        # "Nassau, Bahamas" / "Charleston, SC"


@dataclass
class VenueRun:
    events: int = 0
    matched: int = 0
    neutral: int = 0
    hca_zeroed: int = 0
    unmatched_games: int = 0


def fetch_scoreboard(day: date, session: requests.Session | None = None,
                     cache_dir: Path = DEFAULT_CACHE_DIR, refresh: bool = True) -> dict:
    path = cache_dir / f"espn_{day:%Y-%m-%d}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    session = session or requests.Session()
    resp = session.get(SCOREBOARD_URL, params={"dates": f"{day:%Y%m%d}", "groups": "50", "limit": "500"},
                       headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text)
    return resp.json()


def _city(address: dict) -> str | None:
    city, region = address.get("city"), address.get("state") or address.get("country")
    return ", ".join(x for x in (city, region) if x) or None


def parse_scoreboard(data: dict, mapper: TeamNameMapper) -> list[Venue]:
    out = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        teams = {}
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            name = (mapper.to_kenpom(t.get("displayName", "")) or mapper.to_kenpom(t.get("location", ""))
                    or mapper.to_kenpom(t.get("shortDisplayName", "")))
            teams[c.get("homeAway")] = name
        if not teams.get("home") or not teams.get("away"):
            continue
        v = comp.get("venue", {})
        out.append(Venue(teams["home"], teams["away"], bool(comp.get("neutralSite")),
                         v.get("fullName"), _city(v.get("address", {}))))
    return out


def store(con: duckdb.DuckDBPyConnection, day: date, venues: list[Venue],
          now: datetime | None = None) -> VenueRun:
    """Match venues to ``day``'s scheduled games and store them."""
    now = now or utc_now()
    run = VenueRun(events=len(venues))
    by_pair = {frozenset((v.home, v.away)): v for v in venues}
    locked = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM model_predictions "
                                        "WHERE model_version LIKE 'live-%'").fetchall()}
    games = con.execute("SELECT s.game_id, s.team_a, s.team_b, g.hca_eff FROM scheduled_games s "
                        "JOIN historical_games g USING (game_id) WHERE g.game_date = ?", [day]).fetchall()
    for game_id, a, b, hca in games:
        v = by_pair.get(frozenset((a, b)))
        if v is None:
            run.unmatched_games += 1
            continue
        run.matched += 1
        run.neutral += v.neutral
        con.execute("INSERT OR REPLACE INTO game_venues VALUES (?, ?, ?, ?, ?, ?)",
                    [game_id, v.neutral, v.venue, v.city, "espn", now])
        con.execute("UPDATE historical_games SET venue = ? WHERE game_id = ?", [v.venue, game_id])
        if v.neutral and hca and game_id not in locked:
            con.execute("UPDATE historical_games SET hca_eff = 0 WHERE game_id = ?", [game_id])
            run.hca_zeroed += 1
    return run


def update(con: duckdb.DuckDBPyConnection, day: date, session: requests.Session | None = None,
           cache_dir: Path = DEFAULT_CACHE_DIR, now: datetime | None = None) -> VenueRun:
    mapper = TeamNameMapper(current_teams(con, day))
    return store(con, day, parse_scoreboard(fetch_scoreboard(day, session, cache_dir), mapper), now)


def venue_map(con: duckdb.DuckDBPyConnection, game_ids: list[str]) -> dict[str, tuple[bool, str | None, str | None]]:
    """{game_id: (neutral, venue, city)} for games with a stored venue."""
    if not game_ids:
        return {}
    marks = ",".join("?" for _ in game_ids)
    rows = con.execute(f"SELECT game_id, neutral, venue, city FROM game_venues WHERE game_id IN ({marks})",
                       list(game_ids)).fetchall()
    return {r[0]: (bool(r[1]), r[2], r[3]) for r in rows}
