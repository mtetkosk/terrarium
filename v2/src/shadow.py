"""Shadow models: alternative pick rules evaluated live without affecting picks.

Every shadow model locks a side on each game right after the live picks, using
the SAME consensus line the live pick was locked against (``pick_lines.market_
line_a``), and is graded at that line. Rows live in ``shadow_picks`` and are
never read by the live pipeline, the sheet's pick columns or the ledger.

A shadow model only has to say what margin it expects for team A; edges,
sides, tiers and grading are shared, so every model is judged the same way.
Margins use the closed-form expectation (no simulation), which matches the
simulated mean to within overtime noise.

    python cli.py shadow                         # lock today's shadow picks
    python cli.py shadow --backfill 2026-11-20   # past days, flagged as backfill
    python cli.py shadow --report                # graded records per model

Backfilled rows use only the ratings and lines available on each day, but the
model itself was chosen later, so the report keeps them apart from picks
locked on the day ("live").

To add a model: subclass ``ShadowModel``, implement ``margins``, and append it
to ``REGISTRY`` with today's date as ``started``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import duckdb
import numpy as np

from src import simulate_game as sg

STRONG_EDGE = 3.0
LEAN_EDGE = 1.5


@dataclass(frozen=True)
class SlateGame:
    game_id: str
    game_date: date
    team_a: str
    team_b: str
    hca_eff: float
    market_line_a: float          # consensus spread for team A at lock (negative = A favored)

    @property
    def market_margin_a(self) -> float:
        return -self.market_line_a


def closed_form_margin(con: duckdb.DuckDBPyConnection, g: SlateGame, stretch: float = 1.0) -> float:
    """Expected margin for team A (ratings as of the game's date, no look-ahead)."""
    ra = sg.get_team_ratings(con, g.team_a, g.game_date)
    rb = sg.get_team_ratings(con, g.team_b, g.game_date)
    league = sg.get_league_averages(con, min(ra.snapshot_date, rb.snapshot_date))
    tempo, eff_a, eff_b = sg.expected_means(ra, rb, g.hca_eff or 0.0, league, stretch)
    return tempo * (eff_a - eff_b) / 100.0


@dataclass
class ShadowModel:
    name: str
    description: str
    started: date

    def margins(self, con: duckdb.DuckDBPyConnection, day: date, games: list[SlateGame]) -> dict[str, float]:
        raise NotImplementedError


@dataclass
class Baseline(ShadowModel):
    """The live model's margins, priced the shadow way (closed form, consensus line)."""

    def margins(self, con, day, games):
        stretch = sg.early_season_stretch(day)
        return {g.game_id: closed_form_margin(con, g, stretch) for g in games}


@dataclass
class FixedStretch(ShadowModel):
    """The v1.1-v1.3 early-season stretch schedule with a given cap."""
    cap: float = 1.25

    def stretch(self, day: date) -> float:
        days = max(0, (day - sg.season_start(day)).days)
        return min(self.cap, 1.0 + sg.EARLY_STRETCH_K * max(0.0, 1.0 - days / sg.EARLY_STRETCH_DAYS))

    def margins(self, con, day, games):
        return {g.game_id: closed_form_margin(con, g, self.stretch(day)) for g in games}


@dataclass
class ScaleMatched(ShadowModel):
    """Live-model margins mapped onto the market's scale before measuring edges.

    Each day, market margin ~ k * model margin + c is fit by least squares over
    the games of the trailing ``window`` days (lines at lock, including today's
    slate; no results are used). The pick compares k * model + c with the
    market line, so a disagreement about how wide spreads run in general (the
    early-season stretch question) can't flip a whole slate; only game-specific
    disagreements create edges. 2025-26 backtest (7-day window): Strong 62.7%
    on 263 vs 59.0% on 344 for raw lean-v1.4 edges.
    """
    window: int = 7
    min_games: int = 20

    def margins(self, con, day, games):
        model = {g.game_id: closed_form_margin(con, g, sg.early_season_stretch(g.game_date)) for g in games}
        history = [g for g in locked_slate(con, day - timedelta(days=self.window - 1), day - timedelta(days=1))]
        pairs = [(model[g.game_id], g.market_margin_a) for g in games]
        pairs += [(closed_form_margin(con, g, sg.early_season_stretch(g.game_date)), g.market_margin_a)
                  for g in history]
        if len(pairs) < self.min_games:
            return model                          # too few games to fit a scale: fall back to raw
        x, y = np.array(pairs).T
        k, c = np.polyfit(x, y, 1)
        return {gid: float(k * m + c) for gid, m in model.items()}


# Active shadow models. Retire one by removing it here; its rows stay graded.
REGISTRY: list[ShadowModel] = [
    Baseline("baseline-v1.4", "Live lean-v1.4 margins, closed form, graded at the consensus line",
             date(2026, 11, 24)),
    ScaleMatched("scale-match-7d", "lean-v1.4 margins mapped to the market's scale (7-day fit)",
                 date(2026, 11, 24), window=7),
    FixedStretch("stretch-1.25", "lean-v1.3's early-season stretch (cap 1.25): does it start helping in December?",
                 date(2026, 11, 24), cap=1.25),
]


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------

def locked_slate(con: duckdb.DuckDBPyConnection, start: date, end: date) -> list[SlateGame]:
    """Games in [start, end] with a live pick, and the consensus line it was locked against."""
    rows = con.execute(
        """SELECT h.game_id, h.game_date, h.team_a, h.team_b, coalesce(h.hca_eff, 0), p.market_line_a
           FROM pick_lines p JOIN historical_games h USING (game_id)
           WHERE h.game_date BETWEEN ? AND ? AND p.market_line_a IS NOT NULL
           ORDER BY h.game_date, h.game_id""", [start, end]).fetchall()
    return [SlateGame(*r) for r in rows]


def edge_and_side(margin_a: float, line_a: float) -> tuple[float, str | None]:
    e = margin_a + line_a                   # > 0: team A beats the line in expectation
    return abs(e), ("a" if e > 0 else "b" if e < 0 else None)


def tier(edge: float) -> str:
    return "Strong" if edge >= STRONG_EDGE else "Lean" if edge >= LEAN_EDGE else "None"


@dataclass
class ShadowRun:
    locked: dict[str, int] = field(default_factory=dict)
    already: dict[str, int] = field(default_factory=dict)


def lock(con: duckdb.DuckDBPyConnection, day: date, models: list[ShadowModel] | None = None,
         now: datetime | None = None, backfill: bool = False) -> ShadowRun:
    """Lock each model's side on ``day``'s live-picked games (never overwrites)."""
    models = REGISTRY if models is None else models
    now = now or datetime.now(dt.timezone.utc).replace(tzinfo=None)
    games = locked_slate(con, day, day)
    run = ShadowRun()
    for m in models:
        have = {r[0] for r in con.execute("SELECT game_id FROM shadow_picks WHERE model = ? AND game_date = ?",
                                          [m.name, day]).fetchall()}
        todo = [g for g in games if g.game_id not in have]
        run.already[m.name] = len(have)
        if not todo:
            run.locked[m.name] = 0
            continue
        margins = m.margins(con, day, todo)
        rows = []
        for g in todo:
            margin = margins.get(g.game_id)
            if margin is None:
                continue
            edge, side = edge_and_side(margin, g.market_line_a)
            rows.append([g.game_id, m.name, day, now, round(margin, 3), g.market_line_a, side, round(edge, 3),
                         backfill])
        con.executemany("INSERT INTO shadow_picks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        run.locked[m.name] = len(rows)
    return run


def backfill(con: duckdb.DuckDBPyConnection, start: date, end: date,
             models: list[ShadowModel] | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    d = start
    while d <= end:
        for name, n in lock(con, d, models, backfill=True).locked.items():
            out[name] = out.get(name, 0) + n
        d += timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

@dataclass
class Record:
    covers: int = 0
    losses: int = 0
    pushes: int = 0
    clv: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return self.covers + self.losses

    @property
    def pct(self) -> float | None:
        return self.covers / self.n if self.n else None

    @property
    def units(self) -> float:
        return self.covers * 1.0 - self.losses * 1.1

    def text(self, short: bool = False) -> str:
        if not self.n and not self.pushes:
            return "—"
        p = (f" {self.pct:.0%}" if short else f" ({self.pct:.1%})") if self.n else ""
        return f"{self.covers}-{self.losses}" + (f"-{self.pushes}" if self.pushes else "") + p


@dataclass
class ModelReport:
    model: str
    all: Record
    strong: Record
    lean: Record
    strong_fav: Record
    strong_dog: Record
    picks: int
    pending: int


def report(con: duckdb.DuckDBPyConnection, since: date | None = None,
           include_backfill: bool = False) -> list[ModelReport]:
    """Graded records per shadow model (pushes excluded from percentages)."""
    rows = con.execute(
        """SELECT s.model, s.side, s.edge, s.market_line_a, v.closing_spread_a,
                  h.actual_score_a - h.actual_score_b
           FROM shadow_picks s JOIN historical_games h USING (game_id)
           LEFT JOIN vegas_closing_odds v USING (game_id)
           WHERE (? IS NULL OR s.game_date >= ?) AND (? OR NOT s.backfill)
           ORDER BY s.model""", [since, since, include_backfill]).fetchall()
    out: dict[str, ModelReport] = {}
    for model, side, edge, line, close, margin in rows:
        r = out.setdefault(model, ModelReport(model, Record(), Record(), Record(), Record(), Record(), 0, 0))
        r.picks += 1
        if side is None:
            continue
        if margin is None:
            r.pending += 1
            continue
        res = margin + line
        sign = 1 if side == "a" else -1
        t = tier(edge)
        fav = (line < 0) == (side == "a") and line != 0
        buckets = [r.all] + ([r.strong, r.strong_fav if fav else r.strong_dog] if t == "Strong" else []) \
            + ([r.lean] if t == "Lean" else [])
        clv = None if close is None else sign * (line - close)
        for b in buckets:
            if res == 0:
                b.pushes += 1
            elif sign * res > 0:
                b.covers += 1
            else:
                b.losses += 1
            if clv is not None:
                b.clv.append(clv)
    order = {m.name: i for i, m in enumerate(REGISTRY)}
    return sorted(out.values(), key=lambda r: (order.get(r.model, 99), r.model))
