"""Live picks: lock pre-game predictions and assemble the Daily / Historical tabs.

Picks are stored in ``model_predictions`` under ``LIVE_MODEL_VERSION`` with
``created_at`` = pick time (naive UTC), plus one ``pick_lines`` row with the
line they were locked against. Both are written once, only for games that
haven't tipped, and never overwritten -- the Historical Picks ledger is
walk-forward by construction. Readers accept every ``live-*`` version, so
picks locked by an older model keep showing after a version bump.

Line conventions (team A = home in the odds feed; spreads are team A's):
  * market line  = consensus (median across books) at pick time
  * model side   = A if model margin + market line > 0, B if < 0, none if equal
  * pick line    = best available line for the model side at pick time
                   (line shopping), stored in pick_lines
  * closing line = consensus at the latest pre-tipoff pull
  * ATS          = model side graded against the pick line
  * CLV          = points the pick line beat the closing line by
                   (side A: pick - closing; side B: closing - pick)
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone

import duckdb

from src.odds_api import EASTERN, best_line, market_line, utc_now
from src.simulate_game import DEFAULT_N_SIMS, SimulationError, simulate_game
from src.validate_model import (
    DEFAULT_MODEL_VERSION,
    PREDICTION_COLUMNS,
    devig_moneylines,
    prediction_row,
)

LIVE_MODEL_VERSION = f"live-{DEFAULT_MODEL_VERSION}"
LIVE_VERSIONS = "live-%"          # LIKE pattern: picks from every live model version
LEGACY_BOOKS = ("draftkings", "fanduel")


@dataclass
class PickRun:
    created: int = 0
    already_locked: int = 0
    started: int = 0
    no_ratings: list[str] | None = None
    held_back: list | None = None      # sanity.Issue errors: games not locked


def slate(con: duckdb.DuckDBPyConnection, day: date) -> list[tuple]:
    """(game_id, commence_utc, team_a, team_b, hca_eff) for games on ET date ``day``."""
    rows = con.execute(
        """SELECT s.game_id, s.commence_time, s.team_a, s.team_b, g.hca_eff
           FROM scheduled_games s JOIN historical_games g USING (game_id)
           WHERE g.game_date = ? ORDER BY s.commence_time, s.game_id""", [day]).fetchall()
    return rows


def unlock_unstarted(con: duckdb.DuckDBPyConnection, day: date, now: datetime | None = None) -> list[str]:
    """Release ``day``'s locked live picks for games that have NOT tipped yet, so
    they can be re-locked after a fix (e.g. venue data). Started games are never
    touched, so the ledger stays a pre-game record. Returns the released ids."""
    now = now or utc_now()
    ids = [r[0] for r in con.execute(
        """SELECT p.game_id FROM model_predictions p
           JOIN scheduled_games s USING (game_id) JOIN historical_games g USING (game_id)
           WHERE p.model_version LIKE ? AND g.game_date = ? AND s.commence_time > ?""",
        [LIVE_VERSIONS, day, now]).fetchall()]
    if ids:
        marks = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM model_predictions WHERE model_version LIKE ? AND game_id IN ({marks})",
                    [LIVE_VERSIONS, *ids])
        con.execute(f"DELETE FROM pick_lines WHERE game_id IN ({marks})", ids)
    return ids


def legacy_line(con: duckdb.DuckDBPyConnection, game_id: str, latest_at: datetime) -> dict:
    """The line a pre-line-shopping pick was graded against: per field, the latest
    DraftKings snapshot <= ``latest_at``, else FanDuel's."""
    rows = con.execute(
        """SELECT book, spread_a, ml_a, ml_b, total FROM odds_snapshots
           WHERE game_id = ? AND fetched_at <= ? ORDER BY fetched_at DESC""",
        [game_id, latest_at]).fetchall()
    out = {"spread_a": None, "book": None, "ml_a": None, "ml_b": None, "total": None}
    for book in LEGACY_BOOKS:
        for _, spread, ml_a, ml_b, total in (r for r in rows if r[0] == book):
            if out["spread_a"] is None and spread is not None:
                out["spread_a"], out["book"] = spread, book
            if out["ml_a"] is None and ml_a is not None and ml_b is not None:
                out["ml_a"], out["ml_b"] = ml_a, ml_b
            if out["total"] is None and total is not None:
                out["total"] = total
    return out


def backfill_pick_lines(con: duckdb.DuckDBPyConnection) -> int:
    """Give every live pick locked before ``pick_lines`` existed the exact line
    it was shown and graded against (DraftKings, else FanDuel, at lock time)."""
    rows = con.execute(
        """SELECT p.game_id, p.model_version, p.created_at, p.pred_score_a - p.pred_score_b
           FROM model_predictions p LEFT JOIN pick_lines l USING (game_id)
           WHERE p.model_version LIKE ? AND l.game_id IS NULL""", [LIVE_VERSIONS]).fetchall()
    for gid, version, created, margin in rows:
        m = legacy_line(con, gid, created)
        side = model_side(margin, m["spread_a"])
        con.execute("INSERT INTO pick_lines VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                    [gid, version, created, side, m["spread_a"], m["book"] if side else None,
                     m["spread_a"], m["total"], m["ml_a"], m["ml_b"]])
    return len(rows)


def pick_lines(con: duckdb.DuckDBPyConnection, game_ids: list[str]) -> dict[str, tuple]:
    """{game_id: (side, line_a, book, price, market_line_a, market_total, ml_a, ml_b)}."""
    if not game_ids:
        return {}
    marks = ",".join("?" for _ in game_ids)
    return {r[0]: r[1:] for r in con.execute(
        f"""SELECT game_id, side, line_a, book, price, market_line_a, market_total, ml_a, ml_b
            FROM pick_lines WHERE game_id IN ({marks})""", list(game_ids)).fetchall()}


def _stored_line(con, game_id: str, locked_at: datetime, stored: dict) -> tuple:
    """pick_lines row for a game, or (read-only fallback) its legacy line."""
    if game_id in stored:
        return stored[game_id]
    m = legacy_line(con, game_id, locked_at)
    return (None, m["spread_a"], m["book"], None, m["spread_a"], m["total"], m["ml_a"], m["ml_b"])


def make_picks(
    con: duckdb.DuckDBPyConnection,
    day: date,
    now: datetime | None = None,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int | None = 0,
) -> PickRun:
    """Simulate and lock picks for every not-yet-started game on ``day``."""
    now = now or utc_now()
    from src import power_rankings as pr   # local: power_rankings is heavier and not needed elsewhere
    from src import sanity

    run = PickRun(no_ratings=[], held_back=[])
    backfill_pick_lines(con)
    games = slate(con, day)
    duplicates = sanity.duplicate_teams(games)
    ranks = {r.team: r.rank for r in pr.ensure_rankings(con, day)}
    locked = {r[0] for r in con.execute(
        "SELECT game_id FROM model_predictions WHERE model_version LIKE ?", [LIVE_VERSIONS]).fetchall()}
    insert = (f"INSERT INTO model_predictions ({', '.join(PREDICTION_COLUMNS)}, created_at) "
              f"VALUES ({', '.join('?' for _ in PREDICTION_COLUMNS)}, ?) ON CONFLICT DO NOTHING")
    for game_id, commence, team_a, team_b, hca in games:
        if game_id in locked:
            run.already_locked += 1
            continue
        if commence <= now:
            run.started += 1
            continue
        game_seed = None if seed is None else seed ^ zlib.crc32(game_id.encode())
        try:
            r = simulate_game(team_a, team_b, as_of=day, hca_eff=hca or 0.0, n_sims=n_sims,
                              seed=game_seed, con=con)
        except SimulationError:
            run.no_ratings.append(game_id)
            continue
        line = market_line(con, game_id, latest_at=now)
        errors = [i for i in sanity.game_issues(game_id, team_a, team_b, r, line["spread_a"], ranks, duplicates, day)
                  if i.severity == "error"]
        if errors:                       # hold back: not locked, re-checked on the next run
            run.held_back.extend(errors)
            continue
        side = model_side(r.expected_margin_a, line["spread_a"])
        pick_a, book, price = best_line(line["lines"], side, fallback=line["spread_a"])
        cover = r.cover_prob_a(pick_a) if pick_a is not None else None
        con.execute(insert, [*prediction_row(game_id, LIVE_MODEL_VERSION, day, r, cover,
                                             hca or 0.0, n_sims), now])
        con.execute("INSERT INTO pick_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    [game_id, LIVE_MODEL_VERSION, now, side, pick_a, book if side else None, price,
                     line["spread_a"], line["total"], line["ml_a"], line["ml_b"]])
        run.created += 1
    sanity.store(con, day, "picks", run.held_back, now)
    return run


# --------------------------------------------------------------------------
# Tab rows
# --------------------------------------------------------------------------


def _tip_et(commence_utc: datetime) -> str:
    return commence_utc.replace(tzinfo=timezone.utc).astimezone(EASTERN).strftime("%-I:%M %p")


def _line_text(team_a: str, team_b: str, spread_a: float | None) -> str:
    if spread_a is None:
        return "—"
    if spread_a == 0:
        return "PK"
    return f"{team_a} {spread_a:+.1f}" if spread_a < 0 else f"{team_b} {-spread_a:+.1f}"


def model_side(pred_margin_a: float, line_a: float | None) -> str | None:
    if line_a is None:
        return None
    edge = pred_margin_a + line_a
    return "A" if edge > 0 else "B" if edge < 0 else None


def ats_result(side: str | None, actual_margin_a: float, line_a: float | None) -> str:
    if side is None or line_a is None:
        return "—"
    res = actual_margin_a + line_a
    if res == 0:
        return "Push"
    return "Cover" if (res > 0) == (side == "A") else "Loss"


def clv(side: str | None, pick_line_a: float | None, close_line_a: float | None) -> float | None:
    if side is None or pick_line_a is None or close_line_a is None:
        return None
    return (pick_line_a - close_line_a) if side == "A" else (close_line_a - pick_line_a)


@dataclass(frozen=True)
class DailyPick:
    tip_et: str
    matchup: str
    proj_score: str
    model_line: str
    market_line: str
    edge: str
    model_win: str
    market_win: str
    proj_total: str
    market_total: str
    fav_by_5: str
    fav_by_10: str


def daily_picks(con: duckdb.DuckDBPyConnection, day: date) -> list[DailyPick]:
    """Terminal view of ``day``'s locked picks (same numbers as the sheet)."""
    picks = []
    for g in slate_games(con, day):
        a, b = g.home, g.away
        fav_home = g.model_line <= 0
        fav5 = fav10 = "—"
        if g.buckets:
            b10, b5_9, _, _, a5_9, a10 = g.buckets
            fav5 = f"{(a5_9 + a10) if fav_home else (b10 + b5_9):.0%}"
            fav10 = f"{a10 if fav_home else b10:.0%}"
        picks.append(DailyPick(
            tip_et=g.tip_et.strftime("%-I:%M %p"),
            matchup=f"{b} {'vs' if g.neutral else '@'} {a}",
            proj_score=f"{a} {g.home_pts:.0f}, {b} {g.away_pts:.0f}",
            model_line=_line_text(a, b, round(g.model_line * 2) / 2),
            market_line=_line_text(a, b, g.market_line),
            edge="—" if g.pick is None else f"{g.pick} {g.edge:.1f}",
            model_win=f"{a} {g.home_win:.0%}",
            market_win="—" if g.market_home_win is None else f"{a} {g.market_home_win:.0%}",
            proj_total=f"{g.total:.1f}",
            market_total="—" if g.market_total is None else f"{g.market_total:.1f}",
            fav_by_5=fav5, fav_by_10=fav10,
        ))
    return picks


@dataclass(frozen=True)
class SlateGame:
    """One locked pick as raw numbers (home = team A). Spreads are the home
    team's (negative = home favored); ``edge`` is |market - model| in points
    toward ``pick``; ``buckets`` is P(margin) for Away 10+, Away 5-9,
    Away 1-4, Home 1-4, Home 5-9, Home 10+ (sums to 1)."""
    game_id: str
    tip_et: datetime
    away: str
    home: str
    neutral: bool
    away_pts: float
    home_pts: float
    total: float
    market_total: float | None
    model_line: float
    market_line: float | None         # consensus home spread at pick time
    edge: float | None                # |pick line - model line|, toward ``pick``
    pick: str | None
    home_win: float
    market_home_win: float | None
    buckets: tuple[float, ...] | None
    venue: str | None = None
    city: str | None = None
    site_known: bool = False          # venue data found (else neutral falls back to hca == 0)
    pick_line: float | None = None    # best home spread available for the pick (graded line)
    pick_book: str | None = None

    @property
    def pick_points(self) -> float | None:
        """The pick line from the picked team's side (+6.5 = getting 6.5)."""
        if self.pick is None or self.pick_line is None:
            return None
        return self.pick_line if self.pick == self.home else -self.pick_line

    @property
    def away_win(self) -> float:
        return 1.0 - self.home_win

    @property
    def model_fav(self) -> str:
        return self.home if self.home_win >= 0.5 else self.away


def _buckets(wp: float, a5, a10, b5, b10) -> tuple[float, ...] | None:
    if None in (a5, a10, b5, b10):
        return None
    raw = (b10, b5 - b10, (1 - wp) - b5, wp - a5, a5 - a10, a10)
    return tuple(max(0.0, x) for x in raw)


def slate_games(con: duckdb.DuckDBPyConnection, day: date) -> list[SlateGame]:
    """Locked picks for ET date ``day`` in tip-off order, as numbers. Market line,
    pick line, total and moneylines are the ones stored when each pick was
    locked, so the Pick and Edge shown are exactly what the ledger later grades."""
    preds = {r[0]: r[1:] for r in con.execute(
        """SELECT game_id, pred_score_a, pred_score_b, pred_spread_a, pred_total, win_prob_a,
                  prob_a_by_5, prob_a_by_10, prob_b_by_5, prob_b_by_10, created_at
           FROM model_predictions WHERE model_version LIKE ?""", [LIVE_VERSIONS]).fetchall()}
    from src.venues import venue_map
    games = slate(con, day)
    sites = venue_map(con, [g[0] for g in games])
    stored = pick_lines(con, [g[0] for g in games if g[0] in preds])
    out = []
    for game_id, commence, a, b, hca in games:
        if game_id not in preds:
            continue
        sa, sb, spread, total, wp, a5, a10, b5, b10, locked_at = preds[game_id]
        neutral, venue, city = sites.get(game_id, (not hca, None, None))
        # The lines the pick was locked against (and is graded against), not the current ones.
        side, line_a, book, _, mkt_a, mkt_total, ml_a, ml_b = _stored_line(con, game_id, locked_at, stored)
        if side is None:
            side = model_side(-spread, line_a)
        edge_pts = None if line_a is None or side is None else abs(line_a - spread)
        pick = None if side is None or edge_pts is None or edge_pts < 0.05 else (a if side == "A" else b)
        out.append(SlateGame(
            game_id=game_id,
            tip_et=commence.replace(tzinfo=timezone.utc).astimezone(EASTERN),
            away=b, home=a, neutral=neutral,
            away_pts=sb, home_pts=sa, total=total, market_total=mkt_total,
            model_line=round(spread, 1), market_line=mkt_a,
            edge=edge_pts, pick=pick,
            home_win=wp,
            market_home_win=(devig_moneylines(ml_a, ml_b)[0]
                             if ml_a is not None and ml_b is not None else None),
            buckets=_buckets(wp, a5, a10, b5, b10),
            venue=venue, city=city, site_known=game_id in sites,
            pick_line=line_a if pick else None, pick_book=book if pick else None,
        ))
    return out


@dataclass(frozen=True)
class LedgerRow:
    game_date: date
    matchup: str
    pred_margin: str
    pick_line: str
    closing_line: str
    final: str
    su: str
    ats: str
    clv: str


@dataclass(frozen=True)
class LedgerGame:
    """One graded live pick as raw values (home = team A; spreads and margins
    are the home team's: -4.5 line = home favored, +6 margin = home by 6)."""
    game_id: str
    game_date: date
    away: str
    home: str
    neutral: bool
    pred_margin: float            # model's home margin
    pick: str | None              # team the model took against the pick-time line
    pick_line: float | None       # home spread the pick was locked at (best available line)
    closing_line: float | None    # consensus home spread at tip-off
    away_pts: int
    home_pts: int
    su: str                       # "W" | "L" (model's winner)
    ats: str | None               # "Cover" | "Loss" | "Push" | None (no line / no side)
    clv: float | None
    model_version: str = ""       # version that locked the pick, without the "live-" prefix
    venue: str | None = None
    city: str | None = None
    site_known: bool = False
    pick_book: str | None = None

    @property
    def pick_points(self) -> float | None:
        """The pick line from the picked team's side (+6.5 = getting 6.5)."""
        if self.pick is None or self.pick_line is None:
            return None
        return self.pick_line if self.pick == self.home else -self.pick_line

    @property
    def final_margin(self) -> int:
        return self.home_pts - self.away_pts


def ledger_games(con: duckdb.DuckDBPyConnection, limit: int | None = None) -> list[LedgerGame]:
    """Completed live picks, newest first."""
    rows = con.execute(
        """SELECT g.game_id, g.game_date, g.team_a, g.team_b, g.hca_eff,
                  g.actual_score_a, g.actual_score_b, p.pred_score_a - p.pred_score_b, p.created_at,
                  p.model_version
           FROM model_predictions p JOIN historical_games g USING (game_id)
           WHERE p.model_version LIKE 'live-%' AND g.actual_score_a IS NOT NULL
           ORDER BY g.game_date DESC, g.game_id""" + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    from src.venues import venue_map
    sites = venue_map(con, [r[0] for r in rows])
    stored = pick_lines(con, [r[0] for r in rows])
    out = []
    for gid, gd, a, b, hca, sa, sb, pred_margin, created, version in rows:
        neutral, venue, city = sites.get(gid, (not hca, None, None))
        side, pick, book, *_ = _stored_line(con, gid, created, stored)
        if side is None:
            side = model_side(pred_margin, pick)
        close = market_line(con, gid)["spread_a"]
        actual = sa - sb
        ats = ats_result(side, actual, pick)
        out.append(LedgerGame(
            game_id=gid, game_date=gd, away=b, home=a, neutral=neutral, pred_margin=pred_margin,
            pick=None if side is None else (a if side == "A" else b),
            pick_line=pick, closing_line=close, away_pts=sb, home_pts=sa,
            su="W" if (pred_margin > 0) == (actual > 0) else "L",
            ats=None if ats == "—" else ats, clv=clv(side, pick, close),
            model_version=version.removeprefix("live-"),
            venue=venue, city=city, site_known=gid in sites,
            pick_book=book if side else None,
        ))
    return out


def ledger(con: duckdb.DuckDBPyConnection, limit: int | None = None) -> list[LedgerRow]:
    """Completed live picks, newest first, as display text (terminal output)."""
    out = []
    for g in ledger_games(con, limit):
        a, b = g.home, g.away
        out.append(LedgerRow(
            game_date=g.game_date,
            matchup=f"{b} {'vs' if g.neutral else '@'} {a}",
            pred_margin=f"{a if g.pred_margin >= 0 else b} by {abs(g.pred_margin):.1f}",
            pick_line=_line_text(a, b, g.pick_line),
            closing_line=_line_text(a, b, g.closing_line),
            final=f"{a} {g.home_pts}, {b} {g.away_pts}",
            su=g.su,
            ats="—" if g.ats is None else f"{g.ats} ({g.pick})",
            clv="—" if g.clv is None else f"{g.clv:+.1f}",
        ))
    return out

