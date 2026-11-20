"""Import finished games and closing odds from the v1 SQLite database.

Source: ``v1/data/db/terrarium.db`` (2025-26 season, collected by v1).
  * ``games`` (status FINAL): ``result`` JSON with home/away names and scores,
    ``venue``, ``game_time_est``.
  * ``betting_lines``: snapshots per (book, bet_type, team) with timestamps.

Targets: ``historical_games`` and ``vegas_closing_odds`` with KenPom team
names (see ``src.team_names``). Games involving a non-D-I team are skipped.

Data-quality rules (v1 reused game ids at some point, so some lines are
attached to the wrong game):
  * Only lines whose ``team`` label maps to one of the game's two teams are
    used; unlabeled lines are ignored.
  * The closing line is the latest snapshot strictly before ``game_time_est``
    (timestamps are assumed to share that clock). Both sides must be present
    within 60 s of each other; spreads must mirror (a == -b).
  * Books are tried in order (draftkings, then fanduel).

Venue: team A is the home side. A game is treated as neutral (hca_eff = 0)
when it is played away from the home team's modal venue for the season
(requires >= 3 games at that venue); otherwise hca_eff = DEFAULT_HCA_EFF.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from src.db import PROJECT_ROOT, get_db_connection
from src.simulate_game import DEFAULT_HCA_EFF
from src.team_names import TeamNameMapper

log = logging.getLogger(__name__)

DEFAULT_V1_DB = PROJECT_ROOT.parent / "v1" / "data" / "db" / "terrarium.db"
BOOK_PRIORITY = ("draftkings", "fanduel")
MAX_SIDE_SKEW_SECONDS = 60
MIN_HOME_VENUE_GAMES = 3
MAX_ABS_SPREAD = 60.0


@dataclass(frozen=True)
class V1Game:
    v1_id: int
    game_date: date
    tipoff: datetime | None
    venue: str | None
    home_name: str
    away_name: str
    home_score: int
    away_score: int


@dataclass(frozen=True)
class Line:
    book: str
    bet_type: str
    line: float
    odds: float
    ts: datetime
    team: str  # KenPom name


@dataclass
class ImportStats:
    final_games: int = 0
    non_d1_skipped: int = 0
    duplicates: int = 0
    imported: int = 0
    neutral: int = 0
    with_spread: int = 0
    with_moneyline: int = 0
    unmatched_names: Counter = field(default_factory=Counter)


def game_id_for(game_date: date, team_a: str, team_b: str) -> str:
    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return f"{game_date.isoformat()}_{slug(team_a)}_{slug(team_b)}"


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_v1_games(con: sqlite3.Connection) -> list[V1Game]:
    games = []
    for v1_id, d, tip, venue, result in con.execute(
        "SELECT id, date, game_time_est, venue, result FROM games WHERE status = 'FINAL' ORDER BY id"
    ):
        try:
            r = json.loads(result) if result else None
        except json.JSONDecodeError:
            r = None
        if not r or r.get("home_score") is None or r.get("away_score") is None:
            continue
        games.append(V1Game(v1_id, date.fromisoformat(d), _parse_ts(tip), venue,
                            r.get("home_team"), r.get("away_team"),
                            int(r["home_score"]), int(r["away_score"])))
    return games


def load_v1_lines(con: sqlite3.Connection, mapper: TeamNameMapper) -> dict[int, list[Line]]:
    lines: dict[int, list[Line]] = defaultdict(list)
    for game_id, book, bet_type, line, odds, ts, team in con.execute(
        "SELECT game_id, book, bet_type, line, odds, timestamp, team FROM betting_lines "
        "WHERE bet_type IN ('SPREAD', 'MONEYLINE') AND team IS NOT NULL AND team != ''"
    ):
        kp = mapper.to_kenpom(team)
        if kp is None or ts is None:
            continue
        lines[game_id].append(Line(book, bet_type, float(line), float(odds), _parse_ts(ts), kp))
    return lines


def _latest_pair(lines: list[Line], team_a: str, team_b: str) -> tuple[Line, Line] | None:
    """Latest line per side; both sides must be from the same snapshot."""
    side_a = max((ln for ln in lines if ln.team == team_a), key=lambda ln: ln.ts, default=None)
    side_b = max((ln for ln in lines if ln.team == team_b), key=lambda ln: ln.ts, default=None)
    if side_a is None or side_b is None:
        return None
    if abs((side_a.ts - side_b.ts).total_seconds()) > MAX_SIDE_SKEW_SECONDS:
        return None
    return side_a, side_b


def closing_odds(
    lines: list[Line], team_a: str, team_b: str, tipoff: datetime
) -> tuple[float | None, float | None, float | None]:
    """(closing_spread_a, ml_a, ml_b) from the latest valid pre-tipoff lines."""
    pre = [ln for ln in lines if ln.ts < tipoff and ln.team in (team_a, team_b)]
    spread_a = ml_a = ml_b = None
    for book in BOOK_PRIORITY:
        book_lines = [ln for ln in pre if ln.book == book]
        if spread_a is None:
            pair = _latest_pair([ln for ln in book_lines if ln.bet_type == "SPREAD"], team_a, team_b)
            if pair and abs(pair[0].line + pair[1].line) < 1e-9 and abs(pair[0].line) <= MAX_ABS_SPREAD:
                spread_a = pair[0].line
        if ml_a is None:
            pair = _latest_pair([ln for ln in book_lines if ln.bet_type == "MONEYLINE"], team_a, team_b)
            if pair and all(abs(ln.odds) >= 100 for ln in pair):
                ml_a, ml_b = pair[0].odds, pair[1].odds
    return spread_a, ml_a, ml_b


def home_venues(games: list[tuple[V1Game, str]]) -> dict[str, str]:
    """Modal venue per home team (KenPom name), if seen >= MIN_HOME_VENUE_GAMES times."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for g, home in games:
        if g.venue:
            counts[home][g.venue] += 1
    modal = {}
    for team, c in counts.items():
        venue, n = c.most_common(1)[0]
        if n >= MIN_HOME_VENUE_GAMES:
            modal[team] = venue
    return modal


def build_rows(
    games: list[V1Game],
    lines: dict[int, list[Line]],
    mapper: TeamNameMapper,
    start: date | None = None,
    end: date | None = None,
) -> tuple[list[tuple], list[tuple], ImportStats]:
    stats = ImportStats()
    mapped: list[tuple[V1Game, str, str]] = []
    for g in games:
        stats.final_games += 1
        home, away = mapper.to_kenpom(g.home_name), mapper.to_kenpom(g.away_name)
        if home is None or away is None:
            stats.non_d1_skipped += 1
            for name, kp in ((g.home_name, home), (g.away_name, away)):
                if kp is None:
                    stats.unmatched_names[name] += 1
            continue
        mapped.append((g, home, away))

    modal = home_venues([(g, home) for g, home, _ in mapped])  # whole season, before date filter

    game_rows, odds_rows, seen = [], [], set()
    for g, home, away in mapped:
        if (start and g.game_date < start) or (end and g.game_date > end):
            continue
        gid = game_id_for(g.game_date, home, away)
        if gid in seen:
            stats.duplicates += 1
            continue
        seen.add(gid)

        neutral = bool(g.venue) and home in modal and g.venue != modal[home]
        stats.neutral += neutral
        hca = 0.0 if neutral else DEFAULT_HCA_EFF
        game_rows.append((gid, g.game_date, home, away, g.home_score, g.away_score, g.venue, hca))

        if g.tipoff is not None:
            spread_a, ml_a, ml_b = closing_odds(lines.get(g.v1_id, []), home, away, g.tipoff)
            if spread_a is not None or ml_a is not None:
                odds_rows.append((gid, spread_a, ml_a, ml_b))
                stats.with_spread += spread_a is not None
                stats.with_moneyline += ml_a is not None
    stats.imported = len(game_rows)
    return game_rows, odds_rows, stats


def import_v1(
    v1_path: Path = DEFAULT_V1_DB,
    db_path: str | Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> ImportStats:
    if not Path(v1_path).is_file():
        raise FileNotFoundError(f"v1 database not found: {v1_path}")
    with get_db_connection(db_path=db_path) as con:
        kenpom_names = [r[0] for r in con.execute(
            "SELECT DISTINCT team FROM kenpom_snapshots").fetchall()]
        if not kenpom_names:
            raise RuntimeError("kenpom_snapshots is empty; ingest KenPom ratings first")
        mapper = TeamNameMapper(kenpom_names)

        src = sqlite3.connect(f"file:{v1_path}?mode=ro", uri=True)
        try:
            games, lines = load_v1_games(src), load_v1_lines(src, mapper)
        finally:
            src.close()

        game_rows, odds_rows, stats = build_rows(games, lines, mapper, start, end)
        con.execute("BEGIN TRANSACTION")
        try:
            # DuckDB's executemany rejects an empty parameter list.
            if game_rows:
                con.executemany(
                    "INSERT OR REPLACE INTO historical_games VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    game_rows)
            if odds_rows:
                con.executemany("INSERT OR REPLACE INTO vegas_closing_odds VALUES (?, ?, ?, ?)",
                                odds_rows)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import v1 games and closing odds")
    parser.add_argument("--v1-db", type=Path, default=DEFAULT_V1_DB)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    s = import_v1(args.v1_db, args.db_path, args.start, args.end)
    print(f"v1 FINAL games: {s.final_games}; skipped non-D-I: {s.non_d1_skipped}; "
          f"duplicates: {s.duplicates}")
    print(f"Imported {s.imported} games ({s.neutral} neutral-site); closing spread for "
          f"{s.with_spread}, moneyline for {s.with_moneyline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
