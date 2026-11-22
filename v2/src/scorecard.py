"""Model scorecard vs. Vegas for the Model Performance tab.

Every metric is paired with a benchmark (Vegas favorite, closing spread,
de-vigged moneyline, ATS break-even) and graded three ways:

  * status    -- Good / OK / Poor from the model's *advantage* over the
                 benchmark (sign-normalized so positive is always better);
                 "early" until the sample reaches MIN_SAMPLE games
  * weekly    -- per-week advantage (or value, for metrics without a
                 benchmark) for a trend chart
  * direction -- the last RECENT_DAYS pooled vs. the whole sample

All inputs are graded games with stored predictions (point-in-time ratings),
so nothing here can see the future.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import duckdb
import numpy as np

from src import validate_model as vm

ATS_BREAK_EVEN = 110 / 210          # 52.38%: win rate needed at -110 juice
MIN_SAMPLE = 100                    # games before a status grade is shown
MIN_WEEK_GAMES = 10                 # weeks with fewer graded games are skipped
MIN_TREND_WEEKS = 3
RECENT_DAYS = 21
CALIBRATION_BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001))

_ROWS_SQL = """
    SELECT g.game_date, g.actual_score_a, g.actual_score_b, g.hca_eff,
           p.pred_score_a, p.pred_score_b, p.win_prob_a,
           o.closing_spread_a, o.vegas_ml_a, o.vegas_ml_b
    FROM historical_games g
    JOIN model_predictions p ON p.game_id = g.game_id AND p.model_version LIKE ?
    LEFT JOIN vegas_closing_odds o ON o.game_id = g.game_id
    WHERE g.game_date BETWEEN ? AND ?
      AND g.actual_score_a IS NOT NULL AND g.actual_score_b IS NOT NULL
    ORDER BY g.game_date, g.game_id
"""


def graded_rows(con: duckdb.DuckDBPyConnection, season: int, model_version: str) -> list[tuple]:
    """(game_date, act_a, act_b, hca, pred_a, pred_b, p_win_a, spread_a, ml_a, ml_b) per graded game.
    ``model_version`` is a LIKE pattern (an exact name matches only itself)."""
    lo, hi = vm.season_window(season)
    return con.execute(_ROWS_SQL, [model_version, lo, hi]).fetchall()


@dataclass(frozen=True)
class Scorecard:
    n: int
    n_ml: int
    su_model: float          # on games with a moneyline (same games as su_vegas)
    su_vegas: float
    ats: float
    ats_n: int
    n_spread: int
    margin_rmse_model: float  # on games with a closing spread
    margin_rmse_vegas: float
    total_rmse: float
    brier_model: float
    brier_vegas: float
    bss: float
    n_home: int
    home_bias: float          # mean (actual - predicted) home margin


def _rmse(err: np.ndarray) -> float:
    return math.sqrt(float((err ** 2).mean())) if len(err) else float("nan")


def scorecard(rows: list[tuple]) -> Scorecard:
    col = lambda i: vm._col(rows, i)   # noqa: E731
    act_a, act_b, hca = col(1), col(2), col(3)
    pred_a, pred_b, p_win = col(4), col(5), col(6)
    spread, ml_a, ml_b = col(7), col(8), col(9)
    margin, pred_margin = act_a - act_b, pred_a - pred_b
    market = vm._market_comparison(p_win, margin, ml_a, ml_b)
    has_spread = ~np.isnan(spread)
    ats, ats_n = vm.ats_accuracy(pred_margin[has_spread], margin[has_spread], spread[has_spread])
    home = np.nan_to_num(hca) != 0
    resid = margin - pred_margin
    return Scorecard(
        n=len(rows), n_ml=market.n, su_model=market.su_model, su_vegas=market.su_vegas,
        ats=ats, ats_n=ats_n, n_spread=int(has_spread.sum()),
        margin_rmse_model=_rmse(resid[has_spread]),
        margin_rmse_vegas=_rmse((margin + spread)[has_spread]),   # Vegas margin = -spread
        total_rmse=vm.rmse_total(pred_a, pred_b, act_a, act_b) if len(rows) else float("nan"),
        brier_model=market.brier_model, brier_vegas=market.brier_vegas, bss=market.bss,
        n_home=int(home.sum()),
        home_bias=float(resid[home].mean()) if home.any() else float("nan"),
    )


def weekly(rows: list[tuple]) -> list[tuple[date, Scorecard]]:
    """Scorecards per Monday-starting week (weeks under MIN_WEEK_GAMES dropped)."""
    groups: dict[date, list[tuple]] = {}
    for r in rows:
        groups.setdefault(r[0] - timedelta(days=r[0].weekday()), []).append(r)
    return [(wk, scorecard(g)) for wk, g in sorted(groups.items()) if len(g) >= MIN_WEEK_GAMES]


def recent(rows: list[tuple], days: int = RECENT_DAYS) -> list[tuple]:
    if not rows:
        return []
    cutoff = max(r[0] for r in rows) - timedelta(days=days - 1)
    return [r for r in rows if r[0] >= cutoff]


# --------------------------------------------------------------------------
# Metric definitions and grading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    better: str                    # "high" | "low" | "zero"
    unit: str                      # "pct" | "pts" | "brier" | "bss" | "signed_pts"
    benchmark_label: str
    note: str
    ok_band: float                 # advantage >= -ok_band -> OK (zero-metrics: |x| <= ok_band)
    good_band: float               # advantage >= -good_band -> Good (usually 0)
    trend_band: float              # recent - season advantage beyond this -> improving / slipping
    value: Callable[[Scorecard], float]
    benchmark: Callable[[Scorecard], float] | None
    sample: Callable[[Scorecard], int]


METRICS = (
    Metric("su", "Picks the winner (straight up)", "high", "pct", "Vegas favorite",
           "Share of games where the model's favorite won, vs. the Vegas moneyline favorite on the "
           "same games.", 0.02, 0.0, 0.01,
           lambda s: s.su_model, lambda s: s.su_vegas, lambda s: s.n_ml),
    Metric("ats", "Beats the closing spread (ATS)", "high", "pct", "Break-even at −110",
           "Share of games where the model's side covered the closing spread (pushes excluded). "
           "52.4% is what a bettor needs to break even paying -110.", 0.024, 0.0, 0.01,
           lambda s: s.ats, lambda s: ATS_BREAK_EVEN, lambda s: s.ats_n),
    Metric("margin", "Margin error (RMSE, points)", "low", "pts", "Vegas closing spread",
           "Typical miss on the final margin (root-mean-square, points). Vegas = how far the "
           "closing spread missed on the same games.", 0.5, 0.0, 0.25,
           lambda s: s.margin_rmse_model, lambda s: s.margin_rmse_vegas, lambda s: s.n_spread),
    Metric("brier", "Win-probability error (Brier)", "low", "brier", "Vegas moneyline",
           "Mean squared error of the win probability (0 = perfect, 0.25 = coin flip). Vegas = "
           "closing moneyline with the bookmaker's margin removed.", 0.005, 0.0, 0.002,
           lambda s: s.brier_model, lambda s: s.brier_vegas, lambda s: s.n_ml),
    Metric("bss", "Brier Skill Score vs. Vegas", "high", "bss", "Vegas (0 by definition)",
           "1 - model Brier / Vegas Brier. Above 0 = better-calibrated win probabilities than the "
           "closing moneyline.", 0.02, 0.0, 0.01,
           lambda s: s.bss, lambda s: 0.0, lambda s: s.n_ml),
    Metric("total", "Total points error (RMSE)", "low", "pts", "",
           "Typical miss on the combined final score (root-mean-square, points). No Vegas closing "
           "totals are stored for the backtest, so there is no benchmark.", 0.0, 0.0, 0.25,
           lambda s: s.total_rmse, None, lambda s: s.n),
    Metric("bias", "Home-court bias (points)", "zero", "signed_pts", "Unbiased (0.0)",
           "Average (actual - predicted) home margin. Positive = home teams beat the projection; "
           "within ±1 is good.", 2.0, 1.0, 0.5,
           lambda s: s.home_bias, lambda s: 0.0, lambda s: s.n_home),
)


def _finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def advantage(m: Metric, s: Scorecard) -> float | None:
    """Model minus benchmark, signed so positive = model better. Metrics with
    no benchmark use -value for lower-is-better (so 'up' is still good)."""
    v = m.value(s)
    if not _finite(v):
        return None
    if m.better == "zero":
        return -abs(v)
    if m.benchmark is None:
        return -v if m.better == "low" else v
    b = m.benchmark(s)
    if not _finite(b):
        return None
    return v - b if m.better == "high" else b - v


def status(m: Metric, s: Scorecard) -> str | None:
    """'good' | 'ok' | 'poor' | 'early' (sample too small) | None (no benchmark)."""
    if m.benchmark is None:
        return None
    adv = advantage(m, s)
    if adv is None:
        return None
    if m.sample(s) < MIN_SAMPLE:
        return "early"
    if adv >= -m.good_band:
        return "good"
    return "ok" if adv >= -m.ok_band else "poor"


def direction(m: Metric, season: Scorecard, recent_sc: Scorecard, n_weeks: int) -> str | None:
    """'up' | 'flat' | 'down' for the recent window vs. the whole sample."""
    if n_weeks < MIN_TREND_WEEKS:
        return None
    a, b = advantage(m, recent_sc), advantage(m, season)
    if a is None or b is None:
        return None
    if a - b > m.trend_band:
        return "up"
    return "down" if b - a > m.trend_band else "flat"


def weekly_series(m: Metric, weeks: list[tuple[date, Scorecard]]) -> list[float]:
    """Per-week advantage (benchmarked metrics) or raw value (others)."""
    out = []
    for _, s in weeks:
        x = advantage(m, s) if m.benchmark is not None else m.value(s)
        if _finite(x):
            out.append(float(x))
    return out


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRow:
    lo: float
    hi: float
    n: int
    model_said: float      # mean model win chance for the model's favorite
    vegas_said: float      # mean de-vigged Vegas chance for that same team
    actual: float          # how often that team won


def calibration(rows: list[tuple]) -> list[CalibrationRow]:
    """Games with a moneyline, bucketed by the model's favorite's win chance."""
    out = []
    data = []
    for r in rows:
        if r[8] is None or r[9] is None:
            continue
        p = r[6]
        fav_is_a = p >= 0.5
        vegas_a = vm.devig_moneylines(r[8], r[9])[0]
        margin = r[1] - r[2]
        data.append((p if fav_is_a else 1 - p, vegas_a if fav_is_a else 1 - vegas_a,
                     1.0 if (margin > 0) == fav_is_a else 0.0))
    arr = np.array(data, dtype=float).reshape(-1, 3)
    for lo, hi in CALIBRATION_BUCKETS:
        m = (arr[:, 0] >= lo) & (arr[:, 0] < hi)
        if m.any():
            out.append(CalibrationRow(lo, min(hi, 1.0), int(m.sum()), float(arr[m, 0].mean()),
                                      float(arr[m, 1].mean()), float(arr[m, 2].mean())))
    return out
