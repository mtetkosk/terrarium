"""Milestone 3 gate: vectorized Monte Carlo simulator."""

import time
from datetime import date

import numpy as np
import pytest

from src import db
from src import simulate_game as sg

D_EARLY, D_LATE = date(2026, 11, 10), date(2026, 11, 20)

# ARCHITECTURE.md §6.2 worked example (Duke with -1.2 starter penalty applied).
DUKE = sg.TeamRatings("Duke", D_LATE, adj_o=113.0, adj_d=96.5, adj_t=67.8, three_pa_rate=0.40)
MSU = sg.TeamRatings("Michigan St.", D_LATE, adj_o=117.5, adj_d=94.8, adj_t=69.2, three_pa_rate=0.40)


def team(name="T", adj_o=110.0, adj_d=100.0, adj_t=68.0, rate=0.40):
    return sg.TeamRatings(name, D_LATE, adj_o, adj_d, adj_t, rate)


# --- latent means & variance --------------------------------------------------


def test_expected_means_match_worked_example():
    tempo, eff_duke, eff_msu = sg.expected_means(DUKE, MSU)
    assert tempo == pytest.approx(69.2)
    assert eff_duke == pytest.approx(113.0 * 94.8 / 105.4)   # ~101.6
    assert eff_msu == pytest.approx(117.5 * 96.5 / 105.4)    # ~107.6
    # Spec rounds efficiencies to 0.1 before multiplying, hence abs=0.1.
    assert tempo * eff_duke / 100 == pytest.approx(70.3, abs=0.1)
    assert tempo * eff_msu / 100 == pytest.approx(74.5, abs=0.1)


def test_hca_shifts_efficiencies_symmetrically():
    _, a0, b0 = sg.expected_means(DUKE, MSU, 0.0)
    _, a1, b1 = sg.expected_means(DUKE, MSU, 3.3)
    assert a1 - a0 == pytest.approx(3.3)
    assert b0 - b1 == pytest.approx(3.3)


@pytest.mark.parametrize("rate, sigma", [(0.0, 7.2), (0.40, 10.6), (0.48, 11.28)])
def test_efficiency_sigma_formula(rate, sigma):
    assert sg.efficiency_sigma(rate) == pytest.approx(sigma)
    assert team(rate=rate).sigma_eff == pytest.approx(sigma)


# --- simulation outputs -------------------------------------------------------


@pytest.fixture(scope="module")
def worked():
    return sg.simulate_matchup(DUKE, MSU, hca_eff=0.0, n_sims=50_000, seed=7)


def test_worked_example_results_plausible(worked):
    # Spec: MSU -4.2, total ~144.5, Duke win 36.4%.
    assert worked.spread_a == pytest.approx(4.1, abs=0.3)        # Duke is a 4-pt dog
    # Regulation total is analytic; overtimes add ~12.5% of a game's points.
    tempo, eff_a, eff_b = sg.expected_means(DUKE, MSU)
    regulation = tempo * (eff_a + eff_b) / 100                    # ~144.8
    ot_points = sg.OT_TEMPO_FRACTION * regulation                 # ~18.1
    assert worked.expected_total == pytest.approx(regulation + worked.ot_prob * ot_points, abs=0.4)
    assert 0.30 < worked.win_prob_a < 0.42
    ot_scale = 1 + worked.ot_prob * sg.OT_TEMPO_FRACTION
    assert worked.expected_score_a == pytest.approx(tempo * eff_a / 100 * ot_scale, abs=0.25)
    assert worked.expected_score_b == pytest.approx(tempo * eff_b / 100 * ot_scale, abs=0.25)


def test_probabilities_are_consistent(worked):
    r = worked
    assert r.win_prob_a + r.win_prob_b == pytest.approx(1.0)   # no ties survive
    for p in (r.win_prob_a, r.win_prob_b, r.prob_a_by_5, r.prob_a_by_10,
              r.prob_b_by_5, r.prob_b_by_10, r.ot_prob):
        assert 0.0 <= p <= 1.0
    assert r.prob_a_by_10 <= r.prob_a_by_5 <= r.win_prob_a
    assert r.prob_b_by_10 <= r.prob_b_by_5 <= r.win_prob_b
    assert r.expected_margin_a == pytest.approx(r.expected_score_a - r.expected_score_b)
    assert r.spread_a == -r.expected_margin_a


def test_simulated_scores_are_integers_without_ties(worked):
    assert worked.margins.dtype.kind == "i"
    assert worked.margins.size == worked.n_sims
    assert not (worked.margins == 0).any()
    assert (worked.totals > 0).all()


def test_overtime_occurs_and_resolves_for_even_matchup():
    even = sg.simulate_matchup(team("A"), team("B"), n_sims=50_000, seed=1)
    assert 0.02 < even.ot_prob < 0.10
    assert even.win_prob_a + even.win_prob_b == pytest.approx(1.0)
    assert even.win_prob_a == pytest.approx(0.5, abs=0.02)


def test_neutral_swap_mirrors_results():
    ab = sg.simulate_matchup(DUKE, MSU, n_sims=50_000, seed=3)
    ba = sg.simulate_matchup(MSU, DUKE, n_sims=50_000, seed=4)
    assert ab.win_prob_a == pytest.approx(ba.win_prob_b, abs=0.015)
    assert ab.spread_a == pytest.approx(-ba.spread_a, abs=0.2)


def test_home_court_helps_team_a():
    neutral = sg.simulate_matchup(DUKE, MSU, 0.0, n_sims=20_000, seed=5)
    home = sg.simulate_matchup(DUKE, MSU, sg.DEFAULT_HCA_EFF, n_sims=20_000, seed=5)
    away = sg.simulate_matchup(DUKE, MSU, -sg.DEFAULT_HCA_EFF, n_sims=20_000, seed=5)
    assert away.win_prob_a < neutral.win_prob_a < home.win_prob_a


def test_higher_three_pa_rate_widens_margin_distribution():
    low = sg.simulate_matchup(team("A", rate=0.25), team("B", rate=0.25), n_sims=50_000, seed=9)
    high = sg.simulate_matchup(team("A", rate=0.55), team("B", rate=0.55), n_sims=50_000, seed=9)
    assert high.margins.std() > low.margins.std() * 1.15
    # Mean is unaffected by variance.
    assert high.expected_margin_a == pytest.approx(low.expected_margin_a, abs=0.3)


def test_heavy_favorite_wins_by_10_often():
    fav = sg.simulate_matchup(team("Fav", adj_o=125, adj_d=90), team("Dog", adj_o=98, adj_d=112),
                              n_sims=20_000, seed=2)
    assert fav.win_prob_a > 0.97
    assert fav.prob_a_by_10 > 0.8
    assert fav.fair_ml_a < -1000 and fav.fair_ml_b > 1000


def test_seed_is_reproducible():
    r1 = sg.simulate_matchup(DUKE, MSU, n_sims=5_000, seed=42)
    r2 = sg.simulate_matchup(DUKE, MSU, n_sims=5_000, seed=42)
    assert r1 == r2
    assert np.array_equal(r1.margins, r2.margins)


def test_cover_and_over_probabilities(worked):
    # Duke +4.5 covers when margin_a > -4.5.
    assert worked.cover_prob_a(4.5) == pytest.approx((worked.margins > -4.5).mean())
    assert worked.cover_prob_a(-100) == 0.0 and worked.cover_prob_a(100) == 1.0
    assert 0.4 < worked.over_prob(worked.expected_total) < 0.6


@pytest.mark.parametrize("p, ml", [(0.5, -100.0), (0.75, -300.0), (0.2, 400.0)])
def test_prob_to_american(p, ml):
    assert sg.prob_to_american(p) == pytest.approx(ml)


def test_ten_thousand_sims_are_fast():
    sg.simulate_matchup(DUKE, MSU, n_sims=10_000, seed=0)  # warm-up
    t0 = time.perf_counter()
    sg.simulate_matchup(DUKE, MSU, n_sims=10_000, seed=0)
    assert time.perf_counter() - t0 < 0.25


def test_invalid_inputs():
    with pytest.raises(sg.SimulationError):
        sg.simulate_matchup(DUKE, MSU, n_sims=0)


# --- DB lookup & leakage ------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "cbb.duckdb"
    rows = [
        # snapshot_date, team, adj_em, adj_o, adj_d, adj_t, three_pa_rate
        (D_EARLY, "Duke", 20.0, 115.0, 95.0, 68.0, 0.36),
        (D_LATE, "Duke", 26.0, 120.0, 94.0, 70.0, 0.38),
        (D_EARLY, "Michigan St.", 18.0, 113.0, 95.0, 66.0, None),
        (D_LATE, "Michigan St.", 21.0, 116.0, 95.0, 67.0, None),
        (D_EARLY, "Houston", 25.0, 118.0, 93.0, 64.0, 0.30),
        (D_LATE, "Houston", 27.0, 119.0, 92.0, 64.5, None),
    ]
    with db.get_db_connection(db_path=path) as con:
        con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return path


def ratings(db_path, team_name, as_of):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        return sg.get_team_ratings(con, team_name, as_of)


def test_lookup_uses_latest_snapshot_on_or_before_as_of(db_path):
    assert ratings(db_path, "Duke", D_LATE).adj_o == 120.0
    mid = ratings(db_path, "Duke", date(2026, 11, 15))
    assert (mid.snapshot_date, mid.adj_o) == (D_EARLY, 115.0)   # never the future 11-20 row


def test_lookup_before_any_snapshot_raises(db_path):
    with pytest.raises(sg.SimulationError, match="on or before"):
        ratings(db_path, "Duke", date(2026, 11, 1))


def test_unknown_team_suggests_close_match(db_path):
    with pytest.raises(sg.SimulationError, match="Michigan St"):
        ratings(db_path, "Michigan State", D_LATE)


def test_null_three_pa_falls_back_point_in_time(db_path):
    # Houston 11-20 is NULL -> latest known (11-10) value.
    hou = ratings(db_path, "Houston", D_LATE)
    assert hou.three_pa_imputed and hou.three_pa_rate == 0.30 and hou.adj_o == 119.0
    # MSU never had a 3PA value -> league average.
    msu = ratings(db_path, "Michigan St.", D_LATE)
    assert msu.three_pa_imputed and msu.three_pa_rate == sg.LEAGUE_AVG_THREE_PA_RATE
    # Known value is used as-is.
    duke = ratings(db_path, "Duke", D_LATE)
    assert not duke.three_pa_imputed and duke.three_pa_rate == 0.38


def test_fallback_three_pa_respects_as_of(tmp_path):
    path = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=path) as con:
        con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (D_EARLY, "Kansas", 20.0, 115.0, 95.0, 68.0, None),
            (D_LATE, "Kansas", 22.0, 116.0, 94.0, 68.0, 0.45),   # known only later
        ])
    with db.get_db_connection(read_only=True, db_path=path) as con:
        early = sg.get_team_ratings(con, "Kansas", D_EARLY)
    assert early.three_pa_rate == sg.LEAGUE_AVG_THREE_PA_RATE   # not the future 0.45


def test_simulate_game_from_db(db_path):
    r = sg.simulate_game("Duke", "Michigan St.", as_of=D_LATE, n_sims=5_000, seed=1,
                         db_path=db_path)
    assert (r.team_a, r.team_b) == ("Duke", "Michigan St.")
    assert r.snapshot_date_a == r.snapshot_date_b == D_LATE
    assert r.three_pa_imputed_b and not r.three_pa_imputed_a
    assert r.win_prob_a > 0.5


def test_simulate_game_same_team_rejected(db_path):
    with pytest.raises(sg.SimulationError):
        sg.simulate_game("Duke", "Duke", as_of=D_LATE, db_path=db_path)


def test_cli_neutral(db_path, capsys):
    code = sg.main(["--team1", "Duke", "--team2", "Michigan St.", "--neutral",
                    "--date", "2026-11-20", "--seed", "1", "--db-path", str(db_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "neutral" in out and "Predicted line:  Duke" in out
    assert "Michigan St. 3PA rate imputed" in out


def test_cli_unknown_team_fails(db_path):
    assert sg.main(["--team1", "Dook", "--team2", "Duke", "--db-path", str(db_path)]) == 1


# --- point-in-time league averages ---------------------------------------------


def test_league_averages_from_snapshot(db_path):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        lg = sg.get_league_averages(con, D_LATE, min_teams=3)
    assert lg.snapshot_date == D_LATE
    assert lg.tempo == pytest.approx((70.0 + 67.0 + 64.5) / 3)
    assert lg.efficiency == pytest.approx((120.0 + 116.0 + 119.0) / 3)


def test_league_averages_fall_back_when_snapshot_sparse(db_path):
    with db.get_db_connection(read_only=True, db_path=db_path) as con:
        assert sg.get_league_averages(con, D_LATE) is sg.SPEC_LEAGUE_AVERAGES   # 3 < 300
        assert sg.get_league_averages(con, date(2026, 1, 1), min_teams=1) is sg.SPEC_LEAGUE_AVERAGES


def test_league_averages_scale_expected_means():
    fast = sg.LeagueAverages(tempo=70.0, efficiency=110.0)
    tempo, eff_a, eff_b = sg.expected_means(DUKE, MSU, league=fast)
    assert tempo == pytest.approx(67.8 * 69.2 / 70.0)
    assert eff_a == pytest.approx(113.0 * 94.8 / 110.0)
    assert eff_b == pytest.approx(117.5 * 96.5 / 110.0)


def test_simulate_game_uses_snapshot_league_averages(db_path, monkeypatch):
    monkeypatch.setattr(sg, "MIN_TEAMS_FOR_LEAGUE_AVG", 3)
    mid = date(2026, 11, 15)   # both teams' latest snapshot is D_EARLY
    r = sg.simulate_game("Duke", "Michigan St.", as_of=mid, n_sims=2_000, seed=1, db_path=db_path)
    assert r.league_snapshot_date == D_EARLY                       # never the future D_LATE
    assert r.league_tempo == pytest.approx((68.0 + 66.0 + 64.0) / 3)
    assert r.expected_possessions == pytest.approx(68.0 * 66.0 / r.league_tempo)


def test_simulate_game_mixed_snapshot_dates_use_later_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "MIN_TEAMS_FOR_LEAGUE_AVG", 1)
    path = tmp_path / "cbb.duckdb"
    with db.get_db_connection(db_path=path) as con:
        con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (D_EARLY, "A", 10.0, 110.0, 100.0, 66.0, 0.4),
            (D_EARLY, "B", 0.0, 104.0, 104.0, 66.0, 0.4),
            (D_LATE, "A", 12.0, 112.0, 100.0, 72.0, 0.4),   # B missing on D_LATE
        ])
    r = sg.simulate_game("A", "B", as_of=D_LATE, n_sims=1_000, seed=1, db_path=path)
    assert (r.snapshot_date_a, r.snapshot_date_b) == (D_LATE, D_EARLY)
    assert r.league_snapshot_date == D_LATE and r.league_tempo == 72.0


# --- early-season margin stretch ----------------------------------------------------


@pytest.mark.parametrize("as_of, expected", [
    (date(2025, 11, 1), 1.48), (date(2025, 10, 20), 1.48),          # season start / preseason clamp
    (date(2025, 11, 24), 1 + 0.48 * (1 - 23 / 70)),
    (date(2026, 1, 9), 1.0), (date(2026, 2, 15), 1.0),               # day 69 -> ~1.007; after day 70 -> 1
    (date(2026, 11, 20), 1 + 0.48 * (1 - 19 / 70)),                  # new season resets
])
def test_early_season_stretch_schedule(as_of, expected):
    assert sg.early_season_stretch(as_of) == pytest.approx(expected, abs=0.01)


def test_stretch_scales_neutral_gap_and_keeps_total():
    t0, a0, b0 = sg.expected_means(DUKE, MSU)
    t1, a1, b1 = sg.expected_means(DUKE, MSU, margin_stretch=1.3)
    assert t1 == t0
    assert a1 + b1 == pytest.approx(a0 + b0)
    assert a1 - b1 == pytest.approx(1.3 * (a0 - b0))
    # HCA is added after stretching, unscaled.
    _, a2, b2 = sg.expected_means(DUKE, MSU, hca_eff=2.0, margin_stretch=1.3)
    assert (a2 - b2) - (a1 - b1) == pytest.approx(4.0)


def test_simulate_game_applies_stretch_by_date(db_path):
    on = sg.simulate_game("Duke", "Michigan St.", as_of=D_LATE, n_sims=20_000, seed=1, db_path=db_path)
    off = sg.simulate_game("Duke", "Michigan St.", as_of=D_LATE, n_sims=20_000, seed=1, db_path=db_path,
                           early_stretch=False)
    assert on.margin_stretch == pytest.approx(sg.early_season_stretch(D_LATE)) and off.margin_stretch == 1.0
    assert on.expected_margin_a > off.expected_margin_a > 0
    assert on.expected_total == pytest.approx(off.expected_total, abs=0.5)


def test_default_hca_is_calibrated_value():
    assert sg.DEFAULT_HCA_EFF == 1.9 and sg.SPEC_HCA_EFF == 3.3
