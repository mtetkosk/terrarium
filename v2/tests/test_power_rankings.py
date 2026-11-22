"""Milestone 6 gate: Neutral Dominance Index power rankings."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from src import db
from src import power_rankings as pr
from src import simulate_game as sg

D = date(2026, 1, 20)   # mid-season: no early-season stretch
FIXTURES = Path(__file__).parent / "fixtures"


# --- pairwise win probabilities ------------------------------------------------------


def test_win_prob_matrix_is_consistent():
    o = np.array([120.0, 110.0, 100.0]); d = np.array([92.0, 100.0, 108.0])
    t = np.array([70.0, 66.0, 68.0]); r = np.array([0.30, 0.40, 0.50])
    p = pr.win_prob_matrix(o, d, t, r, 68.0, 106.0)
    assert np.allclose(np.diag(p), 0.5)
    off = ~np.eye(3, dtype=bool)
    assert np.allclose((p + p.T)[off], 1.0)
    assert p[0, 1] > 0.5 and p[1, 2] > 0.5 and p[0, 2] > p[0, 1]


@pytest.mark.parametrize("a, b, stretch", [
    ((118.0, 95.0, 70.0, 0.45), (112.0, 98.0, 66.0, 0.30), 1.0),
    ((110.0, 100.0, 68.0, 0.40), (104.0, 104.0, 72.0, 0.50), 1.3),
])
def test_closed_form_matches_simulator(a, b, stretch):
    league = sg.LeagueAverages(68.0, 106.0)
    ta = sg.TeamRatings("A", D, a[0], a[1], a[2], a[3])
    tb = sg.TeamRatings("B", D, b[0], b[1], b[2], b[3])
    sim = sg.simulate_matchup(ta, tb, n_sims=200_000, seed=11, league=league, margin_stretch=stretch)
    p = pr.win_prob_matrix(np.array([a[0], b[0]]), np.array([a[1], b[1]]), np.array([a[2], b[2]]),
                           np.array([a[3], b[3]]), 68.0, 106.0, stretch)
    assert p[0, 1] == pytest.approx(sim.win_prob_a, abs=0.015)


def test_three_pa_rate_does_not_change_win_probabilities():
    # lean-v1.2: the untested 3PA variance term is off, so identical ratings with
    # different 3PA rates get identical probabilities.
    o = np.array([115.0, 115.0, 100.0, 98.0]); d = np.array([95.0, 95.0, 106.0, 108.0])
    t = np.full(4, 68.0); r = np.array([0.25, 0.55, 0.40, 0.40])
    p = pr.win_prob_matrix(o, d, t, r, 68.0, 106.0)
    assert p[0, 2] == pytest.approx(p[1, 2]) and p[0, 3] == pytest.approx(p[1, 3])


def test_correlation_lowers_margin_variance():
    # rho > 0 shrinks Var(Eff_i - Eff_j), so a favorite is MORE likely to win than
    # with independent draws at the same sigma.
    import src.simulate_game as sg
    o = np.array([115.0, 100.0]); d = np.array([95.0, 106.0]); t = np.full(2, 68.0); r = np.full(2, 0.4)
    with_rho = pr.win_prob_matrix(o, d, t, r, 68.0, 106.0)[0, 1]
    orig = pr.EFF_CORRELATION
    try:
        pr.EFF_CORRELATION = 0.0
        no_rho = pr.win_prob_matrix(o, d, t, r, 68.0, 106.0)[0, 1]
    finally:
        pr.EFF_CORRELATION = orig
    assert sg.EFF_CORRELATION == orig > 0
    assert with_rho > no_rho


# --- rankings from the database ----------------------------------------------------------

TEAMS = {  # team: (adj_o, adj_d, adj_t, 3pa)
    "Alpha": (122.0, 92.0, 69.0, 0.35),
    "Bravo": (116.0, 96.0, 67.0, 0.42),
    "Charlie": (108.0, 101.0, 70.0, 0.38),
    "Delta": (101.0, 107.0, 66.0, None),
}


def insert_snapshot(con, day, teams=TEAMS, boost=None):
    rows = []
    for team, (o, d, t, r) in teams.items():
        if boost and team in boost:
            o += boost[team]
        rows.append((day, team, o - d, o, d, t, r))
    con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=path) as con:
        insert_snapshot(con, D - timedelta(days=7))
        insert_snapshot(con, D - timedelta(days=1), boost={"Charlie": 32.0})   # Charlie surges
        insert_snapshot(con, D, boost={"Charlie": 32.0})
        insert_snapshot(con, D + timedelta(days=5), boost={"Delta": 40.0})     # future: never used
    return path


def test_compute_rankings_orders_by_ndi(db_path):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        r = pr.compute_rankings(con, D - timedelta(days=7))
    assert [x.team for x in r] == ["Alpha", "Bravo", "Charlie", "Delta"]
    assert [x.rank for x in r] == [1, 2, 3, 4]
    assert all(0 < x.ndi < 1 for x in r)
    assert np.mean([x.ndi for x in r]) == pytest.approx(0.5)   # pairwise probs sum to n(n-1)/2


def test_rankings_are_point_in_time(db_path):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        today = pr.compute_rankings(con, D + timedelta(days=2))     # uses D's snapshot
        assert today[0].snapshot_date == D
        assert today[-1].team == "Delta"                              # future boost ignored
        assert pr.compute_rankings(con, date(2025, 1, 1)) == []


def test_ladder_history_and_deltas(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        rows = pr.ladder(con, D)
        stored = con.execute("SELECT DISTINCT ranking_date FROM power_rankings ORDER BY 1").fetchall()
    assert [r.today.team for r in rows] == ["Charlie", "Alpha", "Bravo", "Delta"]
    charlie = rows[0]
    assert charlie.delta_7d == 2                                      # 3rd -> 1st
    assert rows[1].delta_7d == -1 and rows[3].delta_7d == 0
    assert [r.d7 for r in rows] == ["Alpha", "Bravo", "Charlie", "Delta"]
    assert [r.d1 for r in rows] == ["Charlie", "Alpha", "Bravo", "Delta"]
    # D-2 had no snapshot of its own -> rebuilt from the D-7 snapshot (latest <= D-2).
    assert [r.d2 for r in rows] == ["Alpha", "Bravo", "Charlie", "Delta"]
    assert {d for (d,) in stored} == {D, D - timedelta(days=1), D - timedelta(days=2), D - timedelta(days=7)}


def test_ensure_rankings_reuses_and_recomputes(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        first = pr.ensure_rankings(con, D)
        con.execute("UPDATE power_rankings SET ndi = 0 WHERE ranking_date = ?", [D])
        assert pr.ensure_rankings(con, D)[0].ndi == 0                 # stored copy reused
        assert pr.ensure_rankings(con, D, recompute=True)[0].ndi == pytest.approx(first[0].ndi)


@pytest.mark.parametrize("delta, text", [(None, "new"), (0, "NC"), (5, "+5"), (-3, "-3")])
def test_delta_text(delta, text):
    assert pr.delta_text(delta) == text


def test_parse_records_from_cached_ratings_page():
    records = pr.parse_records((FIXTURES / "kenpom_ratings.html").read_text())
    assert records["Duke"] == "4-0" and records["Texas A&M"] == "2-2"
    assert pr.parse_records("<html></html>") == {}


def test_rank_history_stops_at_season_start(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        hist = pr.rank_history(con, D, max_days=30)
        # Snapshots exist from D-7 on; before that there is nothing this season.
        assert [d for d, _ in hist] == [D - timedelta(days=k) for k in range(1, 8)]
        assert hist[0][1]["Charlie"] == 1 and hist[-1][1]["Charlie"] == 3
        assert len(pr.rank_history(con, D, max_days=3)) == 3
        # A season-opening day whose latest snapshot is from last season gets no history.
        con.execute("INSERT INTO kenpom_snapshots SELECT DATE '2026-03-15', team, adj_em, adj_o, adj_d, adj_t, "
                    "three_pa_rate FROM kenpom_snapshots WHERE snapshot_date = ?", [D])
        assert pr.rank_history(con, date(2026, 11, 2)) == []
