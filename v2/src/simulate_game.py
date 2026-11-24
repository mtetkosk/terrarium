"""Vectorized Monte Carlo game simulator with 3PA-scaled efficiency variance.

Latent means (ARCHITECTURE.md §2.1):
    E[T]     = AdjT_A * AdjT_B / T_bar
    E[Eff_A] = AdjO_A * AdjD_B / E_bar + HCA
    E[Eff_B] = AdjO_B * AdjD_A / E_bar - HCA

T_bar / E_bar are the D-I means of AdjT / AdjO on the same snapshot date as
the ratings (leak-safe, and they drift ~67-71 / ~105-109 within and across
seasons). The spec's fixed 67.8 / 105.4 are only a fallback.

Early-season stretch (calibrated in Milestone 4; DISABLED in lean-v1.4): the
idea is that KenPom ratings are pulled toward the mean early in the season, so
neutral-court margins are too compressed, and the neutral efficiency gap is
multiplied by
    b(t) = min(cap, 1 + 0.48 * max(0, 1 - days_since_Nov1 / 70))
before HCA (expected total unchanged). Fitted on 2025-26, where market lines
start on Dec 5 and the stretch helped in December (actual margin ~1.21 x
unstretched model). November was never tested. The first 171 live games of
2026-27 (Nov 20-23) said the opposite: actual margin ~0.82 x the model at the
1.25 cap and ~1.02 x unstretched, with picks on favorites 45-63. lean-v1.4
sets the cap to 1.0 (no stretch); the machinery stays so it can be re-enabled
if this season's ratings turn out compressed in December.

Totals recalibration (lean-v1.3): the multiplicative formulas make projected
totals too extreme (2025-26: actual total ~ 0.78 x model total). After the
means above, the game's tempo and the efficiency midpoint are shrunk toward
the league averages,
    T' = T_bar + 0.85 (T - T_bar),   mid' = E_bar + 0.67 (mid - E_bar),
and the efficiency gap is rescaled by T / T' so the expected MARGIN is exactly
unchanged (spreads were already calibrated, slope ~0.99). Fitted on 2025-26
totals (3,383 games); out of sample (split halves) it cuts total RMSE by
0.1-0.2 pts, still ~0.15-0.2 behind the market's totals.

Variance (lean-v1.3): sigma_T = 3.5 possessions and sigma_Eff = 14.1 for every
team, with the two teams' efficiency draws correlated (rho = 0.30: pace,
officiating and shooting conditions are shared by both teams). Fitted by
maximum likelihood on 2025-26 margin and total residuals: it reproduces both
the margin spread (11.4 pts) and the much wider total spread (17.3 pts),
which independent draws cannot. The spec's 3PA-scaled sigma
(7.2 + 8.5 * 3PA) is disabled (slope 0): the backtest season has no 3PA data,
so the term was never tested. Scores are round(T * Eff / 100), and regulation
ties go to 5-minute overtimes.

Leakage: ratings are always the latest snapshot with snapshot_date <= as_of.
Pass the game date as ``as_of`` for any historical simulation.

Sign conventions: team A is the home side when ``hca_eff > 0``; margins are
A minus B; ``spread_a`` uses the betting convention (negative = A favored).
"""

from __future__ import annotations

import argparse
import difflib
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from src.db import get_db_connection

log = logging.getLogger(__name__)

# Spec baselines; used only when no usable snapshot averages exist.
LEAGUE_AVG_TEMPO = 67.8        # T_bar, possessions per 40 minutes
LEAGUE_AVG_EFFICIENCY = 105.4  # E_bar, points per 100 possessions
# A snapshot needs at least this many teams for its means to be trusted.
MIN_TEAMS_FOR_LEAGUE_AVG = 300
SIGMA_TEMPO = 3.5
# sigma_Eff = BASE + SLOPE * 3PA. lean-v1.1 used the spec's 7.2 + 8.5 * 3PA;
# lean-v1.2 refit BASE and turned the untested 3PA term off (see docstring).
SIGMA_EFF_BASE = 14.1
SIGMA_EFF_3PA_SLOPE = 0.0
# Correlation of the two teams' efficiency draws within a game (lean-v1.3 fit).
EFF_CORRELATION = 0.30
# Totals recalibration (lean-v1.3): shrink toward league averages, margin kept.
TOTAL_TEMPO_SHRINK = 0.85
TOTAL_EFF_SHRINK = 0.67
# Fallback when a team has no known point-in-time 3PA rate (e.g. archive
# backfill rows). Roughly the D-I mean (0.41 observed on 2026-11-20).
LEAGUE_AVG_THREE_PA_RATE = 0.40
# Home-court advantage in points per 100 possessions, applied +HCA to A and
# -HCA to B (§2.1), so the margin swing is ~2 * HCA * T / 100 (~2.6 pts at
# 1.9). Spec value 3.3 over-credited home teams by 1.6 pts/game in the
# 2025-26 backtest; 1.9 is the joint fit with the early-season stretch.
SPEC_HCA_EFF = 3.3
DEFAULT_HCA_EFF = 1.9
EARLY_STRETCH_K = 0.48
EARLY_STRETCH_DAYS = 70
EARLY_STRETCH_MAX = 1.0        # lean-v1.4: stretch off (was 1.25 in v1.2-v1.3)
OT_TEMPO_FRACTION = 5 / 40     # 5-minute overtime = 12.5% of game tempo
MAX_OVERTIMES = 4
DEFAULT_N_SIMS = 10_000


class SimulationError(RuntimeError):
    """Ratings unavailable or invalid simulation inputs."""


@dataclass(frozen=True)
class TeamRatings:
    team: str
    snapshot_date: date
    adj_o: float
    adj_d: float
    adj_t: float
    three_pa_rate: float
    three_pa_imputed: bool = False

    @property
    def sigma_eff(self) -> float:
        return efficiency_sigma(self.three_pa_rate)


@dataclass(frozen=True)
class LeagueAverages:
    tempo: float = LEAGUE_AVG_TEMPO
    efficiency: float = LEAGUE_AVG_EFFICIENCY
    snapshot_date: date | None = None  # None = spec constants


SPEC_LEAGUE_AVERAGES = LeagueAverages()


@dataclass(frozen=True)
class SimulationResult:
    team_a: str
    team_b: str
    n_sims: int
    hca_eff: float
    expected_possessions: float
    expected_eff_a: float
    expected_eff_b: float
    sigma_eff_a: float
    sigma_eff_b: float
    expected_score_a: float
    expected_score_b: float
    expected_margin_a: float
    spread_a: float
    expected_total: float
    win_prob_a: float
    win_prob_b: float
    prob_a_by_5: float
    prob_a_by_10: float
    prob_b_by_5: float
    prob_b_by_10: float
    ot_prob: float
    margin_stretch: float = 1.0
    league_tempo: float = LEAGUE_AVG_TEMPO
    league_efficiency: float = LEAGUE_AVG_EFFICIENCY
    league_snapshot_date: date | None = None
    snapshot_date_a: date | None = None
    snapshot_date_b: date | None = None
    three_pa_imputed_a: bool = False
    three_pa_imputed_b: bool = False
    margins: np.ndarray = field(default=None, repr=False, compare=False)
    totals: np.ndarray = field(default=None, repr=False, compare=False)

    def cover_prob_a(self, spread_a: float) -> float:
        """P(A covers spread_a), pushes excluded from numerator and denominator."""
        adj = self.margins + spread_a
        decided = adj != 0
        return float((adj[decided] > 0).mean()) if decided.any() else 0.5

    def over_prob(self, total: float) -> float:
        """P(total > line), pushes excluded."""
        decided = self.totals != total
        return float((self.totals[decided] > total).mean()) if decided.any() else 0.5

    @property
    def fair_ml_a(self) -> float:
        return prob_to_american(self.win_prob_a)

    @property
    def fair_ml_b(self) -> float:
        return prob_to_american(self.win_prob_b)


def efficiency_sigma(three_pa_rate: float) -> float:
    return SIGMA_EFF_BASE + SIGMA_EFF_3PA_SLOPE * three_pa_rate


def prob_to_american(p: float) -> float:
    """No-vig American odds for win probability p."""
    if p <= 0.0:
        return float("inf")
    if p >= 1.0:
        return float("-inf")
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


# --------------------------------------------------------------------------
# Ratings lookup (point-in-time)
# --------------------------------------------------------------------------

_RATINGS_SQL = """
    SELECT s.snapshot_date, s.adj_o, s.adj_d, s.adj_t, s.three_pa_rate,
           (SELECT k.three_pa_rate FROM kenpom_snapshots k
             WHERE k.team = s.team AND k.snapshot_date <= ?
               AND k.three_pa_rate IS NOT NULL
             ORDER BY k.snapshot_date DESC LIMIT 1) AS last_known_3pa
    FROM kenpom_snapshots s
    WHERE s.team = ? AND s.snapshot_date <= ?
    ORDER BY s.snapshot_date DESC
    LIMIT 1
"""


def get_team_ratings(
    con: duckdb.DuckDBPyConnection, team: str, as_of: date
) -> TeamRatings:
    """Latest ratings with snapshot_date <= as_of (Cardinal Leakage Rule).

    A NULL 3PA rate falls back to the team's latest known rate (also
    <= as_of), then to LEAGUE_AVG_THREE_PA_RATE.
    """
    row = con.execute(_RATINGS_SQL, [as_of, team, as_of]).fetchone()
    if row is None:
        known = [r[0] for r in con.execute(
            "SELECT DISTINCT team FROM kenpom_snapshots").fetchall()]
        if team in known:
            raise SimulationError(f"No ratings for {team!r} on or before {as_of}")
        hint = difflib.get_close_matches(team, known, n=3)
        raise SimulationError(
            f"Unknown team {team!r}" + (f"; did you mean {hint}?" if hint else "")
        )

    snapshot_date, adj_o, adj_d, adj_t, rate, last_known = row
    imputed = rate is None
    if imputed:
        rate = last_known if last_known is not None else LEAGUE_AVG_THREE_PA_RATE
    return TeamRatings(team, snapshot_date, adj_o, adj_d, adj_t, rate, imputed)


def get_league_averages(
    con: duckdb.DuckDBPyConnection,
    snapshot_date: date,
    min_teams: int | None = None,
) -> LeagueAverages:
    """D-I mean AdjT and AdjO on ``snapshot_date``; spec constants if the
    snapshot has fewer than ``min_teams`` teams."""
    min_teams = MIN_TEAMS_FOR_LEAGUE_AVG if min_teams is None else min_teams
    n, tempo, eff = con.execute(
        "SELECT count(*), avg(adj_t), avg(adj_o) FROM kenpom_snapshots "
        "WHERE snapshot_date = ?",
        [snapshot_date],
    ).fetchone()
    if n < min_teams or tempo is None or eff is None:
        log.warning("Snapshot %s has %d teams (< %d); using spec league averages",
                    snapshot_date, n, min_teams)
        return SPEC_LEAGUE_AVERAGES
    return LeagueAverages(float(tempo), float(eff), snapshot_date)


# --------------------------------------------------------------------------
# Simulation core
# --------------------------------------------------------------------------


def season_start(d: date) -> date:
    """Nov 1 of the season containing ``d`` (seasons roll over on Jul 1)."""
    return date(d.year if d.month >= 7 else d.year - 1, 11, 1)


def early_season_stretch(as_of: date) -> float:
    """Margin stretch b(t) for games on ``as_of`` (see module docstring)."""
    days = max(0, (as_of - season_start(as_of)).days)
    return min(EARLY_STRETCH_MAX, 1.0 + EARLY_STRETCH_K * max(0.0, 1.0 - days / EARLY_STRETCH_DAYS))


def expected_means(
    a: TeamRatings,
    b: TeamRatings,
    hca_eff: float = 0.0,
    league: LeagueAverages = SPEC_LEAGUE_AVERAGES,
    margin_stretch: float = 1.0,
) -> tuple[float, float, float]:
    """(E[T], E[Eff_A], E[Eff_B]) per §2.1, with the neutral efficiency gap
    scaled by ``margin_stretch`` about its midpoint before HCA."""
    tempo = a.adj_t * b.adj_t / league.tempo
    eff_a = a.adj_o * b.adj_d / league.efficiency
    eff_b = b.adj_o * a.adj_d / league.efficiency
    mid = 0.5 * (eff_a + eff_b)
    eff_a = mid + margin_stretch * (eff_a - mid)
    eff_b = mid + margin_stretch * (eff_b - mid)
    return tempo, eff_a + hca_eff, eff_b - hca_eff


def recalibrate_total(tempo: float, eff_a: float, eff_b: float,
                      league: LeagueAverages) -> tuple[float, float, float]:
    """Shrink a game's projected tempo and scoring level toward the league
    averages (totals recalibration, see module docstring) while keeping the
    expected margin tempo * (eff_a - eff_b) / 100 exactly unchanged."""
    new_tempo = league.tempo + TOTAL_TEMPO_SHRINK * (tempo - league.tempo)
    if new_tempo <= 0:
        return tempo, eff_a, eff_b
    mid = league.efficiency + TOTAL_EFF_SHRINK * (0.5 * (eff_a + eff_b) - league.efficiency)
    half_gap = 0.5 * (eff_a - eff_b) * tempo / new_tempo
    return new_tempo, mid + half_gap, mid - half_gap


def simulate_matchup(
    a: TeamRatings,
    b: TeamRatings,
    hca_eff: float = 0.0,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int | np.random.Generator | None = None,
    league: LeagueAverages = SPEC_LEAGUE_AVERAGES,
    margin_stretch: float = 1.0,
) -> SimulationResult:
    if n_sims <= 0:
        raise SimulationError("n_sims must be positive")
    rng = np.random.default_rng(seed)
    mu_t, mu_a, mu_b = recalibrate_total(*expected_means(a, b, hca_eff, league, margin_stretch), league)
    sd_a, sd_b = a.sigma_eff, b.sigma_eff

    tempo = np.maximum(rng.normal(mu_t, SIGMA_TEMPO, n_sims), 1.0)

    rho = EFF_CORRELATION
    rho_c = (1.0 - rho * rho) ** 0.5

    def points(poss: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        z_a = rng.standard_normal(poss.size)
        z_b = rho * z_a + rho_c * rng.standard_normal(poss.size)
        eff_a = np.maximum(mu_a + sd_a * z_a, 0.0)
        eff_b = np.maximum(mu_b + sd_b * z_b, 0.0)
        return np.rint(poss * eff_a / 100).astype(np.int64), \
            np.rint(poss * eff_b / 100).astype(np.int64)

    score_a, score_b = points(tempo)
    went_to_ot = score_a == score_b

    ot_tempo = tempo * OT_TEMPO_FRACTION
    for _ in range(MAX_OVERTIMES):
        tied = np.flatnonzero(score_a == score_b)
        if tied.size == 0:
            break
        add_a, add_b = points(ot_tempo[tied])
        score_a[tied] += add_a
        score_b[tied] += add_b

    # Sudden death for anything still tied: one point to a side weighted by
    # expected efficiency.
    tied = np.flatnonzero(score_a == score_b)
    if tied.size:
        p_a = mu_a / (mu_a + mu_b) if (mu_a + mu_b) > 0 else 0.5
        a_wins = rng.random(tied.size) < p_a
        score_a[tied[a_wins]] += 1
        score_b[tied[~a_wins]] += 1

    margins = score_a - score_b
    totals = score_a + score_b
    mean_margin = float(margins.mean())

    return SimulationResult(
        team_a=a.team,
        team_b=b.team,
        n_sims=n_sims,
        hca_eff=hca_eff,
        expected_possessions=mu_t,
        expected_eff_a=mu_a,
        expected_eff_b=mu_b,
        sigma_eff_a=sd_a,
        sigma_eff_b=sd_b,
        expected_score_a=float(score_a.mean()),
        expected_score_b=float(score_b.mean()),
        expected_margin_a=mean_margin,
        spread_a=-mean_margin,
        expected_total=float(totals.mean()),
        win_prob_a=float((margins > 0).mean()),
        win_prob_b=float((margins < 0).mean()),
        prob_a_by_5=float((margins >= 5).mean()),
        prob_a_by_10=float((margins >= 10).mean()),
        prob_b_by_5=float((margins <= -5).mean()),
        prob_b_by_10=float((margins <= -10).mean()),
        ot_prob=float(went_to_ot.mean()),
        margin_stretch=margin_stretch,
        league_tempo=league.tempo,
        league_efficiency=league.efficiency,
        league_snapshot_date=league.snapshot_date,
        snapshot_date_a=a.snapshot_date,
        snapshot_date_b=b.snapshot_date,
        three_pa_imputed_a=a.three_pa_imputed,
        three_pa_imputed_b=b.three_pa_imputed,
        margins=margins,
        totals=totals,
    )


def simulate_game(
    team_a: str,
    team_b: str,
    as_of: date | None = None,
    hca_eff: float = 0.0,
    n_sims: int = DEFAULT_N_SIMS,
    seed: int | None = None,
    db_path: str | Path | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
    early_stretch: bool = True,
) -> SimulationResult:
    """Simulate A vs B using ratings known on ``as_of`` (default: today).

    Pass an open ``con`` to reuse a connection across many games (backtests).
    ``early_stretch`` applies the early-season margin stretch for ``as_of``.
    """
    if team_a == team_b:
        raise SimulationError("team_a and team_b must differ")
    as_of = as_of or date.today()
    if con is None:
        with get_db_connection(read_only=True, db_path=db_path) as own:
            return simulate_game(team_a, team_b, as_of, hca_eff, n_sims, seed,
                                 con=own, early_stretch=early_stretch)

    ra, rb = get_team_ratings(con, team_a, as_of), get_team_ratings(con, team_b, as_of)
    if ra.snapshot_date != rb.snapshot_date:
        log.warning("Snapshot dates differ: %s=%s, %s=%s",
                    team_a, ra.snapshot_date, team_b, rb.snapshot_date)
    # Both dates are <= as_of, so the later one is still leak-safe.
    league = get_league_averages(con, max(ra.snapshot_date, rb.snapshot_date))
    stretch = early_season_stretch(as_of) if early_stretch else 1.0
    return simulate_matchup(ra, rb, hca_eff, n_sims, seed, league, stretch)


# --------------------------------------------------------------------------
# Venue resolution
# --------------------------------------------------------------------------


def home_venue(con: duckdb.DuckDBPyConnection, team: str, min_games: int = 3) -> str | None:
    """A team's modal home venue from ``historical_games`` (home = team_a with
    hca_eff > 0), if it hosted at least ``min_games`` there."""
    row = con.execute(
        """
        SELECT venue, count(*) AS n FROM historical_games
        WHERE team_a = ? AND hca_eff > 0 AND venue IS NOT NULL
        GROUP BY venue ORDER BY n DESC, venue LIMIT 1
        """,
        [team],
    ).fetchone()
    return row[0] if row and row[1] >= min_games else None


def resolve_venue_hca(
    con: duckdb.DuckDBPyConnection,
    team_a: str,
    team_b: str,
    venue: str,
    hca_eff: float = DEFAULT_HCA_EFF,
) -> tuple[float, str]:
    """Map a venue spec to signed HCA for team A plus a human description.

    ``venue`` is "home" (A hosts), "away" (B hosts), "neutral", or an arena
    name matched case-insensitively against each team's modal home venue;
    unknown arenas are treated as neutral.
    """
    v = venue.strip()
    key = v.lower()
    if key == "home":
        return hca_eff, f"{team_a} home"
    if key == "away":
        return -hca_eff, f"{team_b} home"
    if key == "neutral":
        return 0.0, "neutral"
    for team, sign in ((team_a, 1.0), (team_b, -1.0)):
        arena = home_venue(con, team)
        if arena and arena.lower() == key:
            return sign * hca_eff, f"{team} home ({arena})"
    return 0.0, f"neutral ({v}: not a known home arena for either team)"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _fmt_ml(ml: float) -> str:
    return f"{ml:+.0f}" if np.isfinite(ml) else "n/a"


def format_result(r: SimulationResult) -> str:
    fav = r.team_a if r.spread_a <= 0 else r.team_b
    venue = "neutral" if r.hca_eff == 0 else (
        f"{r.team_a} home (HCA {r.hca_eff:+.1f}/100)" if r.hca_eff > 0
        else f"{r.team_b} home (HCA {-r.hca_eff:+.1f}/100)")
    notes = []
    for team, imputed in ((r.team_a, r.three_pa_imputed_a), (r.team_b, r.three_pa_imputed_b)):
        if imputed:
            notes.append(f"{team} 3PA rate imputed")
    lines = [
        f"{r.team_a} vs {r.team_b} — {venue}, {r.n_sims:,} sims",
        f"Ratings as of: {r.team_a} {r.snapshot_date_a}, {r.team_b} {r.snapshot_date_b}"
        + (f" ({'; '.join(notes)})" if notes else ""),
        f"League avg ({r.league_snapshot_date or 'spec constants'}): "
        f"tempo {r.league_tempo:.1f}, eff {r.league_efficiency:.1f}"
        + (f" | early-season margin stretch x{r.margin_stretch:.2f}"
           if r.margin_stretch != 1.0 else ""),
        f"E[poss] {r.expected_possessions:.1f} | E[eff] {r.team_a} {r.expected_eff_a:.1f} "
        f"(σ {r.sigma_eff_a:.1f}), {r.team_b} {r.expected_eff_b:.1f} (σ {r.sigma_eff_b:.1f})",
        "",
        f"Predicted score: {r.team_a} {r.expected_score_a:.1f} - {r.team_b} {r.expected_score_b:.1f}",
        f"Predicted line:  {fav} {-abs(r.spread_a):.1f}  (total {r.expected_total:.1f})",
        f"{r.team_a}: win {r.win_prob_a:.1%} (fair ML {_fmt_ml(r.fair_ml_a)}) | "
        f"by 5+ {r.prob_a_by_5:.1%} | by 10+ {r.prob_a_by_10:.1%}",
        f"{r.team_b}: win {r.win_prob_b:.1%} (fair ML {_fmt_ml(r.fair_ml_b)}) | "
        f"by 5+ {r.prob_b_by_5:.1%} | by 10+ {r.prob_b_by_10:.1%}",
        f"Overtime: {r.ot_prob:.1%}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monte Carlo simulation of one game")
    parser.add_argument("--team1", required=True, help="Team A (home unless --neutral)")
    parser.add_argument("--team2", required=True, help="Team B")
    venue = parser.add_mutually_exclusive_group()
    venue.add_argument("--neutral", action="store_true", help="Neutral court (HCA 0)")
    venue.add_argument("--hca", type=float, default=None,
                       help=f"HCA for team1 in pts/100 poss (default {DEFAULT_HCA_EFF}; "
                       "negative = team2 home)")
    parser.add_argument("--no-early-stretch", action="store_true",
                        help="Disable the early-season margin stretch")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="Game date; uses ratings on or before it (default: today)")
    parser.add_argument("--sims", type=int, default=DEFAULT_N_SIMS)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    hca = 0.0 if args.neutral else (args.hca if args.hca is not None else DEFAULT_HCA_EFF)
    try:
        result = simulate_game(args.team1, args.team2, args.date, hca, args.sims,
                               args.seed, args.db_path,
                               early_stretch=not args.no_early_stretch)
    except (SimulationError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
