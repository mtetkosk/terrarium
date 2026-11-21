"""Sanity checks that stop obviously-wrong output from being locked or published.

Two gates (both objective -- nothing here second-guesses the model's opinion):

* **Before a pick is locked** (``game_issues``, called from ``make_picks``):
  a game with an *error* is held back -- not locked, not shown -- and logged.
  It is re-checked on the next run, so fixing a team alias and re-running
  ``picks`` before tip-off recovers it. Checks:
    - numbers are finite and in range (scores, totals, probabilities, tails)
    - internal consistency (favorite by margin == favorite by win prob)
    - team plays twice on the same day (two feed names mapped to one team)
    - a team's ratings are more than a week old (KenPom renamed/dropped it,
      so the feed name matched last season's team)
    - model vs. market: favorite flipped (market >= 4.5 one way, model >= 3
      the other) or a gap > 12 pts. On 2025-26 these flagged 16 of 2,496
      games, 12 of them odds attached to the wrong team in the v1 import
    - rank inversion: a team ranked 150+ places higher is a 6+ point market
      underdog (the market's teams are not the ones we matched)

* **Before publishing** (``publish_issues``): errors block the publish (the
  sheet keeps the previous day's version):
    - ratings snapshot missing or more than 2 days old
    - rankings: fewer than 300 teams, gaps/duplicates in ranks, NDI out of
      (0, 1) or not sorted, mean NDI far from 0.5
    - any published cell holding NaN / infinity
  and warnings (logged and shown on the sheet, publish continues):
    - feed team names that matched no KenPom team today (usually non-D-I
      opponents; a D-I school here needs an alias in team_names.py)
    - no odds pulled today; locked picks without a market line

Every run's issues are stored in ``sanity_issues`` (per date and stage).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from src.ingest_kenpom import DEFAULT_CACHE_DIR
from src.odds_api import current_teams, eastern_date, parse_utc, utc_now
from src.team_names import TeamNameMapper

MAX_LINE_GAP = 12.0            # |model spread - market spread|, points
FLIP_MARKET, FLIP_MODEL = 4.5, 3.0
RANK_GAP, RANK_UNDERDOG = 150, 6.0
SCORE_RANGE, TOTAL_RANGE = (35.0, 130.0), (90.0, 210.0)
MIN_RANKED_TEAMS = 300
MAX_SNAPSHOT_AGE_DAYS = 2
MAX_TEAM_RATING_AGE_DAYS = 7     # a team's own ratings older than this -> held back
CACHE_DIR = DEFAULT_CACHE_DIR          # where odds_*.json pulls are cached (tests point this elsewhere)


@dataclass(frozen=True)
class Issue:
    severity: str          # "error" | "warning"
    check: str
    message: str
    game_id: str | None = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.message}"


def _finite(*xs) -> bool:
    return all(x is not None and math.isfinite(x) for x in xs)


def _label(team_a: str, team_b: str) -> str:
    return f"{team_b} @ {team_a}"


# --------------------------------------------------------------------------
# Per game, before locking
# --------------------------------------------------------------------------


def duplicate_teams(slate_rows: list[tuple]) -> dict[str, list[str]]:
    """{team: [game_ids]} for teams scheduled more than once on the slate."""
    seen: dict[str, list[str]] = {}
    for game_id, _, team_a, team_b, _ in slate_rows:
        for t in (team_a, team_b):
            seen.setdefault(t, []).append(game_id)
    return {t: g for t, g in seen.items() if len(g) > 1}


def game_issues(game_id: str, team_a: str, team_b: str, sim, market_spread_a: float | None,
                ranks: dict[str, int] | None = None,
                duplicates: dict[str, list[str]] | None = None,
                day: date | None = None) -> list[Issue]:
    """Objective problems with one simulated game (team_a = home)."""
    out: list[Issue] = []
    label = _label(team_a, team_b)

    def err(check, msg):
        out.append(Issue("error", check, f"{label}: {msg}", game_id))

    for t in (team_a, team_b):
        if duplicates and t in duplicates:
            err("duplicate_team", f"{t} is scheduled {len(duplicates[t])} times today "
                                  "(two feed names matched one team?)")

    if day is not None:
        for t, snap in ((team_a, getattr(sim, "snapshot_date_a", None)), (team_b, getattr(sim, "snapshot_date_b", None))):
            if snap is not None and (day - snap).days > MAX_TEAM_RATING_AGE_DAYS:
                err("stale_team_ratings", f"{t}'s latest ratings are from {snap} ({(day - snap).days} days old): "
                                          "renamed or dropped by KenPom? check team_names.py")

    sa, sb, wp = sim.expected_score_a, sim.expected_score_b, sim.win_prob_a
    tails = (sim.prob_a_by_5, sim.prob_a_by_10, sim.prob_b_by_5, sim.prob_b_by_10)
    if not _finite(sa, sb, wp, *tails):
        err("non_finite", "projection contains NaN/inf")
        return out
    if not all(SCORE_RANGE[0] <= s <= SCORE_RANGE[1] for s in (sa, sb)):
        err("score_range", f"projected score {sa:.0f}-{sb:.0f} is outside {SCORE_RANGE}")
    if not TOTAL_RANGE[0] <= sa + sb <= TOTAL_RANGE[1]:
        err("total_range", f"projected total {sa + sb:.0f} is outside {TOTAL_RANGE}")
    a5, a10, b5, b10 = tails
    if not (0 <= a10 <= a5 <= wp <= 1 and 0 <= b10 <= b5 <= 1 - wp + 1e-9):
        err("probabilities", f"win/tail probabilities are inconsistent "
                             f"(win {wp:.3f}, A5 {a5:.3f}, A10 {a10:.3f}, B5 {b5:.3f}, B10 {b10:.3f})")
    margin = sa - sb
    if abs(margin) >= 1.0 and (margin > 0) != (wp > 0.5):
        err("favorite_mismatch", f"projected margin {margin:+.1f} but home win chance {wp:.0%}")

    model_spread = sim.spread_a
    if market_spread_a is not None:
        gap = abs(model_spread - market_spread_a)
        if gap > MAX_LINE_GAP:
            err("market_gap", f"model {model_spread:+.1f} vs market {market_spread_a:+.1f} "
                              f"({gap:.1f} pts apart): check the team names / line orientation")
        elif (abs(market_spread_a) >= FLIP_MARKET and abs(model_spread) >= FLIP_MODEL
              and (market_spread_a > 0) != (model_spread > 0)):
            fav_mkt = team_a if market_spread_a < 0 else team_b
            fav_mod = team_a if model_spread < 0 else team_b
            err("favorite_flip", f"market favors {fav_mkt} by {abs(market_spread_a):.1f}, model favors "
                                 f"{fav_mod} by {abs(model_spread):.1f}: check the team names / line orientation")

    if ranks and team_a in ranks and team_b in ranks:
        for better, worse, spread_better in ((team_a, team_b, market_spread_a),
                                             (team_b, team_a, None if market_spread_a is None
                                              else -market_spread_a)):
            gap = ranks[worse] - ranks[better]
            if gap < RANK_GAP:
                continue
            for who, line in (("market", spread_better),
                              ("model", model_spread if better == team_a else -model_spread)):
                if line is not None and line >= RANK_UNDERDOG:
                    err("rank_inversion", f"#{ranks[better]} {better} is a {line:.1f}-pt {who} underdog "
                                          f"to #{ranks[worse]} {worse}")
    return out


# --------------------------------------------------------------------------
# Before publishing
# --------------------------------------------------------------------------


def snapshot_issues(con: duckdb.DuckDBPyConnection, day: date) -> list[Issue]:
    snap = con.execute("SELECT max(snapshot_date) FROM kenpom_snapshots WHERE snapshot_date <= ?",
                       [day]).fetchone()[0]
    if snap is None:
        return [Issue("error", "stale_ratings", "no KenPom snapshot on or before today")]
    if (day - snap).days > MAX_SNAPSHOT_AGE_DAYS:
        return [Issue("error", "stale_ratings", f"latest KenPom snapshot is {snap} "
                                                f"({(day - snap).days} days old): did ingest fail?")]
    return []


def rankings_issues(rankings: list) -> list[Issue]:
    def err(msg):
        return [Issue("error", "rankings", msg)]

    if len(rankings) < MIN_RANKED_TEAMS:
        return err(f"only {len(rankings)} teams ranked (expected >= {MIN_RANKED_TEAMS})")
    if [r.rank for r in rankings] != list(range(1, len(rankings) + 1)):
        return err("ranks are not 1..N in order")
    if len({r.team for r in rankings}) != len(rankings):
        return err("a team appears more than once")
    ndi = [r.ndi for r in rankings]
    if not all(_finite(x) and 0 < x < 1 for x in ndi):
        return err("an NDI is outside (0, 1) or not a number")
    if any(a < b - 1e-12 for a, b in zip(ndi, ndi[1:])):
        return err("NDI is not sorted from best to worst")
    if abs(sum(ndi) / len(ndi) - 0.5) > 0.01:
        return err(f"mean NDI is {sum(ndi) / len(ndi):.3f}, expected 0.500")
    return []


def tab_issues(tabs: list) -> list[Issue]:
    """NaN / inf anywhere in the cells about to be published."""
    out = []
    for tab in tabs:
        for r, row in enumerate(tab.layout.rows):
            for c, cell in enumerate(row):
                v = cell.get("userEnteredValue", {})
                x = v.get("numberValue")
                text = v.get("stringValue", "")
                if (x is not None and not math.isfinite(x)) or text.strip().lower() in ("nan", "inf", "-inf",
                                                                                         "none"):
                    out.append(Issue("error", "bad_cell", f"{tab.title} row {r + 1} col {c + 1} holds {x or text!r}"))
    return out[:20]


def unmatched_names(con: duckdb.DuckDBPyConnection, day: date,
                    cache_dir: Path | None = None) -> list[str]:
    """Feed team names for ``day``'s events that matched no KenPom team (latest odds pull)."""
    cache_dir = cache_dir or CACHE_DIR
    files = sorted(cache_dir.glob(f"odds_{day:%Y-%m-%d}T*.json")) or sorted(
        cache_dir.glob(f"odds_{(day - timedelta(days=1)):%Y-%m-%d}T*.json"))
    if not files:
        return []
    events = json.loads(files[-1].read_text())
    mapper = TeamNameMapper(current_teams(con, day))
    names = set()
    for e in events:
        if eastern_date(parse_utc(e["commence_time"])) != day:
            continue
        for raw in (e.get("home_team"), e.get("away_team")):
            if raw and mapper.to_kenpom(raw) is None:
                names.add(raw)
    return sorted(names)


def feed_issues(con: duckdb.DuckDBPyConnection, day: date, cache_dir: Path | None = None,
                now: datetime | None = None) -> list[Issue]:
    out = []
    names = unmatched_names(con, day, cache_dir)
    if names:
        out.append(Issue("warning", "unmatched_names",
                         f"{len(names)} feed team name(s) matched no KenPom team, so those games are "
                         f"skipped (usually non-D-I opponents): {', '.join(names)}"))
    now = now or utc_now()
    latest = con.execute("SELECT max(fetched_at) FROM odds_snapshots").fetchone()[0]
    if latest is None or eastern_date(latest) != day and day == eastern_date(now):
        out.append(Issue("warning", "stale_odds", f"no odds pulled today (latest pull: {latest})"))
    return out


def publish_issues(con: duckdb.DuckDBPyConnection, day: date, rankings: list, tabs: list,
                   cache_dir: Path | None = None, now: datetime | None = None) -> list[Issue]:
    return [*snapshot_issues(con, day), *rankings_issues(rankings), *tab_issues(tabs),
            *feed_issues(con, day, cache_dir, now)]


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def store(con: duckdb.DuckDBPyConnection, day: date, stage: str, issues: list[Issue],
          now: datetime | None = None) -> None:
    """Replace ``stage``'s issues for ``day``."""
    con.execute("DELETE FROM sanity_issues WHERE check_date = ? AND stage = ?", [day, stage])
    if issues:
        now = now or utc_now()
        con.executemany("INSERT INTO sanity_issues VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(day, stage, i.severity, i.check, i.game_id, i.message, now) for i in issues])


def load(con: duckdb.DuckDBPyConnection, day: date) -> list[Issue]:
    rows = con.execute("SELECT severity, check_name, message, game_id FROM sanity_issues "
                       "WHERE check_date = ? ORDER BY stage DESC, severity, check_name, message",
                       [day]).fetchall()
    return [Issue(*r) for r in rows]
