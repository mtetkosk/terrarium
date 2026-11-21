"""Live picks: lock pre-game predictions and assemble the Daily / Historical tabs.

Picks are stored in ``model_predictions`` under ``LIVE_MODEL_VERSION`` with
``created_at`` = pick time (naive UTC). They are written once, only for games
that haven't tipped, and never overwritten -- the Historical Picks ledger is
walk-forward by construction.

Line conventions (team A = home in the odds feed; spreads are team A's):
  * pick line    = market line at pick time (latest snapshot <= created_at)
  * closing line = latest pre-tipoff snapshot
  * model side   = A if model margin + pick line > 0, B if < 0, none if equal
  * ATS          = model side graded against the pick line
  * CLV          = points the line moved toward the model side by close
                   (side A: pick - closing; side B: closing - pick)
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone

import duckdb

from src.odds_api import EASTERN, market_line, utc_now
from src.simulate_game import DEFAULT_N_SIMS, SimulationError, simulate_game
from src.validate_model import (
    DEFAULT_MODEL_VERSION,
    PREDICTION_COLUMNS,
    devig_moneylines,
    prediction_row,
)

LIVE_MODEL_VERSION = f"live-{DEFAULT_MODEL_VERSION}"


@dataclass
class PickRun:
    created: int = 0
    already_locked: int = 0
    started: int = 0
    no_ratings: list[str] | None = None


def slate(con: duckdb.DuckDBPyConnection, day: date) -> list[tuple]:
    """(game_id, commence_utc, team_a, team_b, hca_eff) for games on ET date ``day``."""
    rows = con.execute(
        """SELECT s.game_id, s.commence_time, s.team_a, s.team_b, g.hca_eff
           FROM scheduled_games s JOIN historical_games g USING (game_id)
           WHERE g.game_date = ? ORDER BY s.commence_time, s.game_id""", [day]).fetchall()
    return rows


def make_picks(
    con: duckdb.DuckDBPyConnection,
    day: date,
    now: datetime | None = None,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int | None = 0,
) -> PickRun:
    """Simulate and lock picks for every not-yet-started game on ``day``."""
    now = now or utc_now()
    run = PickRun(no_ratings=[])
    locked = {r[0] for r in con.execute(
        "SELECT game_id FROM model_predictions WHERE model_version = ?", [LIVE_MODEL_VERSION]).fetchall()}
    insert = (f"INSERT INTO model_predictions ({', '.join(PREDICTION_COLUMNS)}, created_at) "
              f"VALUES ({', '.join('?' for _ in PREDICTION_COLUMNS)}, ?) ON CONFLICT DO NOTHING")
    for game_id, commence, team_a, team_b, hca in slate(con, day):
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
        cover = r.cover_prob_a(line["spread_a"]) if line["spread_a"] is not None else None
        con.execute(insert, [*prediction_row(game_id, LIVE_MODEL_VERSION, day, r, cover,
                                             hca or 0.0, n_sims), now])
        run.created += 1
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
    picks = []
    preds = {r[0]: r[1:] for r in con.execute(
        """SELECT game_id, pred_score_a, pred_score_b, pred_spread_a, pred_total, win_prob_a,
                  prob_a_by_5, prob_a_by_10, prob_b_by_5, prob_b_by_10, created_at
           FROM model_predictions WHERE model_version = ?""", [LIVE_MODEL_VERSION]).fetchall()}
    for game_id, commence, a, b, hca in slate(con, day):
        if game_id not in preds:
            continue
        sa, sb, spread, total, wp, a5, a10, b5, b10, _ = preds[game_id]
        m = market_line(con, game_id)
        venue = "vs" if not hca else "@"
        edge_pts = None if m["spread_a"] is None else m["spread_a"] - spread
        edge = "—" if edge_pts is None else ("—" if abs(edge_pts) < 0.05 else
                                           f"{a if edge_pts > 0 else b} {abs(edge_pts):.1f}")
        mkt_win = "—"
        if m["ml_a"] is not None:
            mkt_win = f"{devig_moneylines(m['ml_a'], m['ml_b'])[0]:.0%}"
        fav_a = spread <= 0
        picks.append(DailyPick(
            tip_et=_tip_et(commence),
            matchup=f"{b} {venue} {a}",
            proj_score=f"{a} {sa:.0f}, {b} {sb:.0f}",
            model_line=_line_text(a, b, round(spread * 2) / 2),
            market_line=_line_text(a, b, m["spread_a"]),
            edge=edge,
            model_win=f"{a} {wp:.0%}",
            market_win=f"{a} {mkt_win}" if mkt_win != "—" else "—",
            proj_total=f"{total:.1f}",
            market_total="—" if m["total"] is None else f"{m['total']:.1f}",
            fav_by_5=f"{(a5 if fav_a else b5):.0%}" if a5 is not None else "—",
            fav_by_10=f"{(a10 if fav_a else b10):.0%}" if a10 is not None else "—",
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
    market_line: float | None
    edge: float | None
    pick: str | None
    home_win: float
    market_home_win: float | None
    buckets: tuple[float, ...] | None

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
    """Locked picks for ET date ``day`` in tip-off order, as numbers."""
    preds = {r[0]: r[1:] for r in con.execute(
        """SELECT game_id, pred_score_a, pred_score_b, pred_spread_a, pred_total, win_prob_a,
                  prob_a_by_5, prob_a_by_10, prob_b_by_5, prob_b_by_10
           FROM model_predictions WHERE model_version = ?""", [LIVE_MODEL_VERSION]).fetchall()}
    out = []
    for game_id, commence, a, b, hca in slate(con, day):
        if game_id not in preds:
            continue
        sa, sb, spread, total, wp, a5, a10, b5, b10 = preds[game_id]
        m = market_line(con, game_id)
        edge_pts = None if m["spread_a"] is None else m["spread_a"] - spread
        pick = None if edge_pts is None or abs(edge_pts) < 0.05 else (a if edge_pts > 0 else b)
        out.append(SlateGame(
            game_id=game_id,
            tip_et=commence.replace(tzinfo=timezone.utc).astimezone(EASTERN),
            away=b, home=a, neutral=not hca,
            away_pts=sb, home_pts=sa, total=total, market_total=m["total"],
            model_line=round(spread, 1), market_line=m["spread_a"],
            edge=None if edge_pts is None else abs(edge_pts), pick=pick,
            home_win=wp,
            market_home_win=(devig_moneylines(m["ml_a"], m["ml_b"])[0]
                             if m["ml_a"] is not None and m["ml_b"] is not None else None),
            buckets=_buckets(wp, a5, a10, b5, b10),
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
    pick_line: float | None       # home spread when the pick was locked
    closing_line: float | None    # home spread at tip-off
    away_pts: int
    home_pts: int
    su: str                       # "W" | "L" (model's winner)
    ats: str | None               # "Cover" | "Loss" | "Push" | None (no line / no side)
    clv: float | None

    @property
    def final_margin(self) -> int:
        return self.home_pts - self.away_pts


def ledger_games(con: duckdb.DuckDBPyConnection, limit: int | None = None) -> list[LedgerGame]:
    """Completed live picks, newest first."""
    rows = con.execute(
        """SELECT g.game_id, g.game_date, g.team_a, g.team_b, g.hca_eff,
                  g.actual_score_a, g.actual_score_b, p.pred_score_a - p.pred_score_b, p.created_at
           FROM model_predictions p JOIN historical_games g USING (game_id)
           WHERE p.model_version = ? AND g.actual_score_a IS NOT NULL
           ORDER BY g.game_date DESC, g.game_id""" + (f" LIMIT {int(limit)}" if limit else ""),
        [LIVE_MODEL_VERSION]).fetchall()
    out = []
    for gid, gd, a, b, hca, sa, sb, pred_margin, created in rows:
        pick = market_line(con, gid, latest_at=created)["spread_a"]
        close = market_line(con, gid)["spread_a"]
        side = model_side(pred_margin, pick)
        actual = sa - sb
        ats = ats_result(side, actual, pick)
        out.append(LedgerGame(
            game_id=gid, game_date=gd, away=b, home=a, neutral=not hca, pred_margin=pred_margin,
            pick=None if side is None else (a if side == "A" else b),
            pick_line=pick, closing_line=close, away_pts=sb, home_pts=sa,
            su="W" if (pred_margin > 0) == (actual > 0) else "L",
            ats=None if ats == "—" else ats, clv=clv(side, pick, close),
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

