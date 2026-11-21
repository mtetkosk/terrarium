"""Market de-vigging, walk-forward predictions, and stratified backtest audit.

Pipeline:
  1. ``generate_predictions`` simulates every ``historical_games`` row using
     ratings with ``snapshot_date <= game_date`` (Cardinal Leakage Rule) and
     stores results in ``model_predictions`` under a ``model_version``.
  2. ``run_backtest_audit`` joins games, predictions and ``vegas_closing_odds``
     and scores the model (ARCHITECTURE.md §7):
       SU accuracy, ATS accuracy (pushes excluded), team/total RMSE,
       Brier score and Brier Skill Score vs de-vigged closing moneylines,
       with BSS/ATS stratified into spread tranches.

Conventions: team A is the home side when ``hca_eff > 0``; margins are A - B;
``closing_spread_a`` uses the betting convention (negative = A favored), so A
covers when ``margin_a + closing_spread_a > 0``.
"""

from __future__ import annotations

import argparse
import logging
import math
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np

from src.db import get_db_connection
from src.simulate_game import DEFAULT_N_SIMS, SimulationError, simulate_game

log = logging.getLogger(__name__)

# lean-v1 = spec HCA 3.3, no stretch (initial backtest). lean-v1.1 = HCA 1.9 +
# early-season margin stretch (Milestone 4 calibration).
DEFAULT_MODEL_VERSION = "lean-v1.1"
# Skip games whose ratings snapshot is older than this (stale, e.g. a March
# game priced off a February snapshot when the archive has a gap).
DEFAULT_MAX_STALENESS_DAYS = 3

TOSS_UP, COMPETITIVE, HEAVY_FAVORITE = "toss_up", "competitive", "heavy_favorite"
TRANCHES = (TOSS_UP, COMPETITIVE, HEAVY_FAVORITE)
TRANCHE_LABELS = {
    TOSS_UP: "Toss-ups [-3, +3]",
    COMPETITIVE: "Competitive [-3.5, -9.5]",
    HEAVY_FAVORITE: "Heavy favorites (<= -10)",
}


# --------------------------------------------------------------------------
# Odds & de-vigging
# --------------------------------------------------------------------------


def american_to_prob(ml: float) -> float:
    """Raw (vigged) implied probability of American odds."""
    if ml == 0 or (-100 < ml < 100):
        raise ValueError(f"Invalid American odds: {ml}")
    return -ml / (-ml + 100.0) if ml < 0 else 100.0 / (ml + 100.0)


def devig_odds_power_method(
    prob_a_raw: float, prob_b_raw: float, tol: float = 1e-12, max_iter: int = 200
) -> tuple[float, float]:
    """Remove the bookmaker margin with the power method (§7.2).

    Binary-searches the exponent k with prob_a_raw**k + prob_b_raw**k = 1 and
    returns (p_a, p_b) = (prob_a_raw**k, prob_b_raw**k). Relative to simple
    normalization this shifts more of the margin onto the longshot, which
    matches the favorite-longshot bias in betting markets.
    """
    for p in (prob_a_raw, prob_b_raw):
        if not 0.0 < p < 1.0:
            raise ValueError(f"Raw probabilities must be in (0, 1); got {p}")

    def total(k: float) -> float:
        return prob_a_raw**k + prob_b_raw**k

    # total(k) is strictly decreasing in k; bracket the root.
    lo, hi = 1e-6, 1.0
    while total(hi) > 1.0:
        lo, hi = hi, hi * 2.0
        if hi > 1e6:
            raise ValueError("Could not bracket power-method exponent")
    if total(lo) < 1.0:
        raise ValueError("Could not bracket power-method exponent")

    for _ in range(max_iter):
        k = 0.5 * (lo + hi)
        if total(k) > 1.0:
            lo = k
        else:
            hi = k
        if hi - lo < tol:
            break
    k = 0.5 * (lo + hi)
    p_a, p_b = prob_a_raw**k, prob_b_raw**k
    s = p_a + p_b  # remove residual bisection error
    return p_a / s, p_b / s


def devig_moneylines(ml_a: float, ml_b: float) -> tuple[float, float]:
    return devig_odds_power_method(american_to_prob(ml_a), american_to_prob(ml_b))


def spread_tranche(closing_spread_a: float) -> str:
    """Tranche by the favorite's spread (-|spread|).

    Toss-up: [-3, 0]; competitive: (-10, -3) i.e. -3.5..-9.5 on half-point
    lines; heavy favorite: <= -10.
    """
    fav = -abs(closing_spread_a)
    if fav >= -3.0:
        return TOSS_UP
    if fav > -10.0:
        return COMPETITIVE
    return HEAVY_FAVORITE


# --------------------------------------------------------------------------
# Metrics (numpy arrays in, floats out; NaN when undefined)
# --------------------------------------------------------------------------


def _nan_if_empty(n: int, value: float) -> float:
    return float(value) if n else float("nan")


def su_accuracy(win_prob_a: np.ndarray, actual_margin_a: np.ndarray) -> float:
    """Share of games where the side with p > 0.5 won (p == 0.5 excluded)."""
    decided = win_prob_a != 0.5
    correct = (win_prob_a[decided] > 0.5) == (actual_margin_a[decided] > 0)
    return _nan_if_empty(decided.sum(), correct.mean() if decided.any() else 0)


def ats_accuracy(
    pred_margin_a: np.ndarray, actual_margin_a: np.ndarray, closing_spread_a: np.ndarray
) -> tuple[float, int]:
    """(accuracy, n) picking A when the model margin beats the line.

    Excludes pushes (actual margin lands on the line) and no-pick games
    (model margin exactly on the line).
    """
    model_edge = pred_margin_a + closing_spread_a
    result = actual_margin_a + closing_spread_a
    decided = (model_edge != 0) & (result != 0)
    n = int(decided.sum())
    correct = (model_edge[decided] > 0) == (result[decided] > 0)
    return _nan_if_empty(n, correct.mean() if n else 0), n


def rmse_team(pred_a, pred_b, act_a, act_b) -> float:
    n = len(pred_a)
    sq = (act_a - pred_a) ** 2 + (act_b - pred_b) ** 2
    return _nan_if_empty(n, math.sqrt(sq.sum() / (2 * n)) if n else 0)


def rmse_total(pred_a, pred_b, act_a, act_b) -> float:
    n = len(pred_a)
    err = (act_a + act_b) - (pred_a + pred_b)
    return _nan_if_empty(n, math.sqrt((err**2).mean()) if n else 0)


def brier_score(p: np.ndarray, y: np.ndarray) -> float:
    return _nan_if_empty(len(p), ((p - y) ** 2).mean() if len(p) else 0)


def brier_skill_score(bs_model: float, bs_reference: float) -> float:
    if not bs_reference or math.isnan(bs_reference) or math.isnan(bs_model):
        return float("nan")
    return 1.0 - bs_model / bs_reference


# --------------------------------------------------------------------------
# Report structures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketComparison:
    """Model vs de-vigged closing moneyline on identical games."""
    n: int
    su_model: float
    su_vegas: float
    brier_model: float
    brier_vegas: float
    bss: float


@dataclass(frozen=True)
class TrancheMetrics:
    n_spread: int
    ats: float
    ats_n: int
    market: MarketComparison


@dataclass(frozen=True)
class BacktestReport:
    season: int | None
    model_version: str
    start: date
    end: date
    n_games: int
    su_model: float
    rmse_team: float
    rmse_total: float
    n_spread: int
    ats: float
    ats_n: int
    market: MarketComparison
    tranches: dict[str, TrancheMetrics] = field(default_factory=dict)
    # HCA diagnostics: mean (actual - predicted) margin for A. Negative on
    # home games means the model over-credits home court.
    n_home: int = 0
    margin_residual_home: float = float("nan")
    n_neutral: int = 0
    margin_residual_neutral: float = float("nan")


# --------------------------------------------------------------------------
# Season windows
# --------------------------------------------------------------------------


def season_window(season: int) -> tuple[date, date]:
    """Season 2026 = the 2025-26 season (Jul 1 2025 .. Jun 30 2026)."""
    return date(season - 1, 7, 1), date(season, 6, 30)


def resolve_window(
    season: int | None, start: date | None, end: date | None
) -> tuple[date, date]:
    lo, hi = season_window(season) if season is not None else (date.min, date.max)
    lo = max(lo, start) if start else lo
    hi = min(hi, end) if end else hi
    if lo > hi:
        raise ValueError(f"Empty date window {lo}..{hi}")
    return lo, hi


# --------------------------------------------------------------------------
# Walk-forward predictions
# --------------------------------------------------------------------------


@dataclass
class PredictionRun:
    model_version: str
    written: int = 0
    skipped_no_ratings: list[str] = field(default_factory=list)
    skipped_stale: list[str] = field(default_factory=list)


PREDICTION_COLUMNS = (
    "game_id", "model_version", "as_of", "snapshot_date", "pred_score_a", "pred_score_b",
    "pred_spread_a", "pred_total", "win_prob_a", "cover_prob_a", "hca_eff", "n_sims",
    "prob_a_by_5", "prob_a_by_10", "prob_b_by_5", "prob_b_by_10",
)
_PREDICTION_UPSERT = (
    f"INSERT OR REPLACE INTO model_predictions ({', '.join(PREDICTION_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in PREDICTION_COLUMNS)})"
)


def prediction_row(game_id: str, model_version: str, game_date: date, r, cover: float | None,
                   hca: float, n_sims: int) -> tuple:
    """model_predictions row (PREDICTION_COLUMNS order) from a SimulationResult."""
    return (
        game_id, model_version, game_date, min(r.snapshot_date_a, r.snapshot_date_b),
        r.expected_score_a, r.expected_score_b, r.spread_a, r.expected_total,
        r.win_prob_a, cover, hca, n_sims,
        r.prob_a_by_5, r.prob_a_by_10, r.prob_b_by_5, r.prob_b_by_10,
    )


def default_model_version(hca_override: float | None, early_stretch: bool = True) -> str:
    version = DEFAULT_MODEL_VERSION
    if hca_override is not None:
        version += f"-hca{hca_override:g}"
    if not early_stretch:
        version += "-nostretch"
    return version


def generate_predictions(
    con: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    model_version: str | None = None,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int | None = 0,
    hca_override: float | None = None,
    max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS,
    early_stretch: bool = True,
) -> PredictionRun:
    """Simulate every historical game in [start, end] with point-in-time ratings.

    ``hca_override`` replaces the stored ``hca_eff`` on non-neutral games
    (neutral games stay 0) so HCA values can be compared by model_version.
    Seeds are derived per game from ``seed`` and the game_id, so results don't
    depend on which other games are in the run.
    """
    model_version = model_version or default_model_version(hca_override, early_stretch)
    run = PredictionRun(model_version)
    games = con.execute(
        """
        SELECT g.game_id, g.game_date, g.team_a, g.team_b, g.hca_eff, o.closing_spread_a
        FROM historical_games g
        LEFT JOIN vegas_closing_odds o USING (game_id)
        WHERE g.game_date BETWEEN ? AND ?
        ORDER BY g.game_date, g.game_id
        """,
        [start, end],
    ).fetchall()

    rows = []
    for game_id, game_date, team_a, team_b, hca_stored, closing_spread in games:
        hca = hca_stored or 0.0
        if hca_override is not None and hca != 0.0:
            hca = hca_override if hca > 0 else -hca_override
        game_seed = None if seed is None else (seed ^ zlib.crc32(game_id.encode()))
        try:
            r = simulate_game(team_a, team_b, as_of=game_date, hca_eff=hca,
                              n_sims=n_sims, seed=game_seed, con=con,
                              early_stretch=early_stretch)
        except SimulationError as exc:
            log.debug("Skipping %s: %s", game_id, exc)
            run.skipped_no_ratings.append(game_id)
            continue

        snapshot = min(r.snapshot_date_a, r.snapshot_date_b)
        if game_date - snapshot > timedelta(days=max_staleness_days):
            run.skipped_stale.append(game_id)
            continue

        cover = r.cover_prob_a(closing_spread) if closing_spread is not None else None
        rows.append(prediction_row(game_id, model_version, game_date, r, cover, hca, n_sims))

    if rows:
        con.executemany(_PREDICTION_UPSERT, rows)
    run.written = len(rows)
    return run


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

_AUDIT_SQL = """
    SELECT g.actual_score_a, g.actual_score_b, g.hca_eff,
           p.pred_score_a, p.pred_score_b, p.win_prob_a,
           o.closing_spread_a, o.vegas_ml_a, o.vegas_ml_b
    FROM historical_games g
    JOIN model_predictions p ON p.game_id = g.game_id AND p.model_version = ?
    LEFT JOIN vegas_closing_odds o ON o.game_id = g.game_id
    WHERE g.game_date BETWEEN ? AND ?
      AND g.actual_score_a IS NOT NULL AND g.actual_score_b IS NOT NULL
    ORDER BY g.game_date, g.game_id
"""


def _col(rows, i) -> np.ndarray:
    return np.array([np.nan if r[i] is None else r[i] for r in rows], dtype=float)


def _market_comparison(win_prob_a, actual_margin, ml_a, ml_b) -> MarketComparison:
    has_ml = ~np.isnan(ml_a) & ~np.isnan(ml_b)
    n = int(has_ml.sum())
    if n == 0:
        nan = float("nan")
        return MarketComparison(0, nan, nan, nan, nan, nan)
    p_model = win_prob_a[has_ml]
    p_vegas = np.array([devig_moneylines(a, b)[0] for a, b in zip(ml_a[has_ml], ml_b[has_ml])])
    margin = actual_margin[has_ml]
    y = (margin > 0).astype(float)
    bs_model, bs_vegas = brier_score(p_model, y), brier_score(p_vegas, y)
    return MarketComparison(
        n=n,
        su_model=su_accuracy(p_model, margin),
        su_vegas=su_accuracy(p_vegas, margin),
        brier_model=bs_model,
        brier_vegas=bs_vegas,
        bss=brier_skill_score(bs_model, bs_vegas),
    )


def audit_rows(rows: list[tuple], model_version: str, start: date, end: date,
               season: int | None = None) -> BacktestReport:
    act_a, act_b, hca = _col(rows, 0), _col(rows, 1), _col(rows, 2)
    pred_a, pred_b, p_win = _col(rows, 3), _col(rows, 4), _col(rows, 5)
    spread, ml_a, ml_b = _col(rows, 6), _col(rows, 7), _col(rows, 8)
    actual_margin, pred_margin = act_a - act_b, pred_a - pred_b

    has_spread = ~np.isnan(spread)
    ats, ats_n = ats_accuracy(pred_margin[has_spread], actual_margin[has_spread], spread[has_spread])

    tranches = {}
    tranche_of = np.array([spread_tranche(s) if not np.isnan(s) else "" for s in spread])
    for t in TRANCHES:
        m = tranche_of == t
        t_ats, t_ats_n = ats_accuracy(pred_margin[m], actual_margin[m], spread[m])
        tranches[t] = TrancheMetrics(
            n_spread=int(m.sum()), ats=t_ats, ats_n=t_ats_n,
            market=_market_comparison(p_win[m], actual_margin[m], ml_a[m], ml_b[m]),
        )

    residual = actual_margin - pred_margin
    home, neutral = np.nan_to_num(hca) != 0, np.nan_to_num(hca) == 0
    return BacktestReport(
        season=season,
        model_version=model_version,
        start=start,
        end=end,
        n_games=len(rows),
        su_model=su_accuracy(p_win, actual_margin),
        rmse_team=rmse_team(pred_a, pred_b, act_a, act_b),
        rmse_total=rmse_total(pred_a, pred_b, act_a, act_b),
        n_spread=int(has_spread.sum()),
        ats=ats,
        ats_n=ats_n,
        market=_market_comparison(p_win, actual_margin, ml_a, ml_b),
        tranches=tranches,
        n_home=int(home.sum()),
        margin_residual_home=_nan_if_empty(home.sum(), residual[home].mean() if home.any() else 0),
        n_neutral=int(neutral.sum()),
        margin_residual_neutral=_nan_if_empty(
            neutral.sum(), residual[neutral].mean() if neutral.any() else 0),
    )


def run_backtest_audit(
    season: int | None,
    model_version: str = DEFAULT_MODEL_VERSION,
    start: date | None = None,
    end: date | None = None,
    db_path: str | Path | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> BacktestReport:
    """Score stored predictions for ``season`` (optionally narrowed by dates)."""
    lo, hi = resolve_window(season, start, end)
    if con is None:
        with get_db_connection(read_only=True, db_path=db_path) as own:
            rows = own.execute(_AUDIT_SQL, [model_version, lo, hi]).fetchall()
    else:
        rows = con.execute(_AUDIT_SQL, [model_version, lo, hi]).fetchall()
    return audit_rows(rows, model_version, lo, hi, season)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _pct(x: float) -> str:
    return "  n/a" if math.isnan(x) else f"{x:5.1%}"


def _num(x: float, fmt: str = ".3f") -> str:
    return "n/a" if math.isnan(x) else format(x, fmt)


def format_report(r: BacktestReport) -> str:
    m = r.market
    lines = [
        f"Backtest {r.model_version} | {r.start} .. {r.end} | {r.n_games} games",
        "",
        f"  SU accuracy (all games)        {_pct(r.su_model)}",
        f"  Team score RMSE                {_num(r.rmse_team, '.2f')}",
        f"  Game total RMSE                {_num(r.rmse_total, '.2f')}",
        f"  ATS accuracy                   {_pct(r.ats)}  (n={r.ats_n} of {r.n_spread} "
        f"with spreads; breakeven 52.4%)",
        "",
        f"  vs de-vigged closing ML (n={m.n}):   model   vegas",
        f"    SU accuracy                   {_pct(m.su_model)}   {_pct(m.su_vegas)}",
        f"    Brier score                   {_num(m.brier_model)}   {_num(m.brier_vegas)}",
        f"    Brier Skill Score             {_num(m.bss, '+.3f')}",
        "",
        "  By closing-spread tranche:",
    ]
    for t in TRANCHES:
        tm = r.tranches[t]
        lines.append(
            f"    {TRANCHE_LABELS[t]:26} n={tm.n_spread:4}  ATS {_pct(tm.ats)} (n={tm.ats_n})  "
            f"BSS {_num(tm.market.bss, '+.3f')} (n={tm.market.n})"
        )
    lines += [
        "",
        "  HCA check, mean (actual - predicted) margin for team A:",
        f"    home games    {_num(r.margin_residual_home, '+.2f')} (n={r.n_home})",
        f"    neutral games {_num(r.margin_residual_neutral, '+.2f')} (n={r.n_neutral})",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the simulator against closing lines")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("predict", "audit"):
        p = sub.add_parser(name)
        p.add_argument("--season", type=int, default=None,
                       help="Season end year (2026 = 2025-26)")
        p.add_argument("--start", type=date.fromisoformat, default=None)
        p.add_argument("--end", type=date.fromisoformat, default=None)
        p.add_argument("--model-version", default=None)
        p.add_argument("--db-path", type=Path, default=None)
        if name == "predict":
            p.add_argument("--hca", type=float, default=None,
                           help="Override HCA (pts/100 poss) for non-neutral games")
            p.add_argument("--sims", type=int, default=DEFAULT_N_SIMS)
            p.add_argument("--seed", type=int, default=0)
            p.add_argument("--max-staleness", type=int, default=DEFAULT_MAX_STALENESS_DAYS)
            p.add_argument("--no-early-stretch", action="store_true",
                           help="Disable the early-season margin stretch")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        lo, hi = resolve_window(args.season, args.start, args.end)
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "predict":
        with get_db_connection(db_path=args.db_path) as con:
            run = generate_predictions(con, lo, hi, args.model_version, args.sims, args.seed,
                                       args.hca, args.max_staleness,
                                       early_stretch=not args.no_early_stretch)
        print(f"{run.model_version}: wrote {run.written} predictions for {lo}..{hi}; "
              f"skipped {len(run.skipped_no_ratings)} without ratings, "
              f"{len(run.skipped_stale)} with snapshots > {args.max_staleness}d old")
        return 0

    report = run_backtest_audit(args.season, args.model_version or DEFAULT_MODEL_VERSION,
                                args.start, args.end, db_path=args.db_path)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
