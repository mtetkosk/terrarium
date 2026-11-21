"""Daily 1-N power rankings by Neutral Dominance Index (ARCHITECTURE.md §8.3).

NDI_i = mean over all other D-I teams j of P(i beats j on a neutral floor).

Win probabilities use the simulator's own inputs (snapshot league averages,
early-season margin stretch, 3PA-scaled efficiency sigma) in closed form
instead of 10k Monte Carlo trials per pair (~66k pairs/day):

    margin_ij ~ N(mu, sd^2),  mu = T_ij * stretch * (E_ij - E_ji) / 100
    sd^2 = (T_ij/100)^2 (sigma_i^2 + sigma_j^2) + (SIGMA_TEMPO * stretch * (E_ij - E_ji) / 100)^2

which matches the simulator's win probability to within ~1 pt (see tests).
The spec's 21-day recency decay is deferred: it needs per-game efficiencies,
and only daily KenPom snapshots are stored.

Rankings are point-in-time: the ranking for date D uses the latest snapshot
<= D, so past ladders (D-1, D-2, D-7) can be rebuilt on demand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import numpy as np

from src import ingest_kenpom as ik
from src.simulate_game import (
    LEAGUE_AVG_THREE_PA_RATE,
    SIGMA_TEMPO,
    early_season_stretch,
    season_start,
    efficiency_sigma,
    get_league_averages,
)

_erf = np.vectorize(math.erf, otypes=[float])


@dataclass(frozen=True)
class Ranking:
    rank: int
    team: str
    ndi: float
    adj_em: float
    snapshot_date: date


def _snapshot_for(con: duckdb.DuckDBPyConnection, as_of: date) -> date | None:
    return con.execute("SELECT max(snapshot_date) FROM kenpom_snapshots WHERE snapshot_date <= ?",
                       [as_of]).fetchone()[0]


def _team_inputs(con, snapshot: date, as_of: date):
    rows = con.execute(
        """
        SELECT s.team, s.adj_em, s.adj_o, s.adj_d, s.adj_t,
               coalesce(s.three_pa_rate,
                        (SELECT k.three_pa_rate FROM kenpom_snapshots k
                          WHERE k.team = s.team AND k.snapshot_date <= ? AND k.three_pa_rate IS NOT NULL
                          ORDER BY k.snapshot_date DESC LIMIT 1),
                        ?) AS rate
        FROM kenpom_snapshots s WHERE s.snapshot_date = ? ORDER BY s.team
        """, [as_of, LEAGUE_AVG_THREE_PA_RATE, snapshot]).fetchall()
    teams = [r[0] for r in rows]
    arr = np.array([r[1:] for r in rows], dtype=float)
    return teams, arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]


def win_prob_matrix(adj_o, adj_d, adj_t, three_pa, league_tempo, league_eff, stretch=1.0):
    """P[i, j] = P(team i beats team j on a neutral floor); diagonal = 0.5."""
    tempo = np.outer(adj_t, adj_t) / league_tempo
    eff_ij = np.outer(adj_o, adj_d) / league_eff          # i's offense vs j's defense
    gap = stretch * (eff_ij - eff_ij.T)
    mu = tempo * gap / 100.0
    sigma = np.array([efficiency_sigma(r) for r in three_pa])
    var = (tempo / 100.0) ** 2 * (sigma[:, None] ** 2 + sigma[None, :] ** 2) \
        + (SIGMA_TEMPO * gap / 100.0) ** 2
    p = 0.5 * (1.0 + _erf(mu / np.sqrt(2.0 * var)))
    np.fill_diagonal(p, 0.5)
    return p


def compute_rankings(con: duckdb.DuckDBPyConnection, as_of: date,
                     early_stretch: bool = True) -> list[Ranking]:
    snapshot = _snapshot_for(con, as_of)
    if snapshot is None:
        return []
    teams, adj_em, adj_o, adj_d, adj_t, rate = _team_inputs(con, snapshot, as_of)
    league = get_league_averages(con, snapshot)
    stretch = early_season_stretch(as_of) if early_stretch else 1.0
    p = win_prob_matrix(adj_o, adj_d, adj_t, rate, league.tempo, league.efficiency, stretch)
    n = len(teams)
    ndi = (p.sum(axis=1) - 0.5) / (n - 1) if n > 1 else np.full(n, 0.5)
    order = sorted(range(n), key=lambda i: (-ndi[i], -adj_em[i], teams[i]))
    return [Ranking(rank, teams[i], float(ndi[i]), float(adj_em[i]), snapshot)
            for rank, i in enumerate(order, start=1)]


def store_rankings(con: duckdb.DuckDBPyConnection, ranking_date: date, rankings: list[Ranking]) -> None:
    con.execute("DELETE FROM power_rankings WHERE ranking_date = ?", [ranking_date])
    if rankings:
        con.executemany("INSERT INTO power_rankings VALUES (?, ?, ?, ?, ?, ?)",
                        [(ranking_date, r.team, r.rank, r.ndi, r.adj_em, r.snapshot_date)
                         for r in rankings])


def load_rankings(con: duckdb.DuckDBPyConnection, ranking_date: date) -> list[Ranking]:
    rows = con.execute("SELECT rank, team, ndi, adj_em, snapshot_date FROM power_rankings "
                       "WHERE ranking_date = ? ORDER BY rank", [ranking_date]).fetchall()
    return [Ranking(*r) for r in rows]


def ensure_rankings(con: duckdb.DuckDBPyConnection, ranking_date: date,
                    recompute: bool = False) -> list[Ranking]:
    """Stored ranking for the date, computing (point-in-time) and storing if missing."""
    if not recompute:
        existing = load_rankings(con, ranking_date)
        if existing:
            return existing
    rankings = compute_rankings(con, ranking_date)
    store_rankings(con, ranking_date, rankings)
    return rankings


def parse_records(html: str) -> dict[str, str]:
    """{team: 'W-L'} from a cached KenPom ratings page (live pages only)."""
    try:
        table, headers = ik._find_table_and_headers(html)
    except ik.KenPomParseError:
        return {}
    if "w-l" not in headers:
        return {}
    idx = headers.index("w-l")
    team_idx = headers.index("team")
    return {team: cells[idx] for team, cells in ik._team_rows(table, team_idx) if len(cells) > idx}


def records_for(snapshot: date, cache_dir=ik.DEFAULT_CACHE_DIR) -> dict[str, str]:
    path = ik.ratings_cache_path(cache_dir, snapshot)
    return parse_records(path.read_text(encoding="utf-8")) if path.exists() else {}


@dataclass(frozen=True)
class LadderRow:
    rank: int
    today: Ranking
    record: str
    d1: str
    d2: str
    d7: str
    delta_7d: int | None   # positive = moved up


LADDER_OFFSETS = {"d1": 1, "d2": 2, "d7": 7}


def ladder(con: duckdb.DuckDBPyConnection, today: date, recompute_today: bool = True,
           cache_dir=ik.DEFAULT_CACHE_DIR) -> list[LadderRow]:
    current = ensure_rankings(con, today, recompute=recompute_today)
    past = {k: ensure_rankings(con, today - timedelta(days=d)) for k, d in LADDER_OFFSETS.items()}
    by_rank = {k: {r.rank: r.team for r in v} for k, v in past.items()}
    rank_d7 = {r.team: r.rank for r in past["d7"]}
    records = records_for(current[0].snapshot_date, cache_dir) if current else {}
    return [
        LadderRow(
            rank=r.rank, today=r, record=records.get(r.team, ""),
            d1=by_rank["d1"].get(r.rank, ""), d2=by_rank["d2"].get(r.rank, ""),
            d7=by_rank["d7"].get(r.rank, ""),
            delta_7d=(rank_d7[r.team] - r.rank) if r.team in rank_d7 else None,
        )
        for r in current
    ]


def rank_history(con: duckdb.DuckDBPyConnection, today: date,
                 max_days: int = 30) -> list[tuple[date, dict[str, int]]]:
    """[(day, {team: rank})] for today-1, today-2, ... (newest first), stopping at
    ``max_days`` or at the start of the season -- a day whose latest snapshot is
    from before this season's Nov 1 would show last season's ladder."""
    start = season_start(today)
    out = []
    for back in range(1, max_days + 1):
        day = today - timedelta(days=back)
        snap = _snapshot_for(con, day)
        if day < start or snap is None or snap < start:
            break
        out.append((day, {r.team: r.rank for r in ensure_rankings(con, day)}))
    return out


def delta_text(delta: int | None) -> str:
    if delta is None:
        return "new"
    return "NC" if delta == 0 else f"{delta:+d}"
