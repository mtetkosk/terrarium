"""Milestone 4 gate: de-vigging, metrics, stratified audit, walk-forward predictions.

Uses synthetic games only. The six-game dataset below has hand-computed
expected metrics (see comments).
"""

import math
from datetime import date

import numpy as np
import pytest

from src import db
from src import validate_model as vm

# --- odds & de-vig -------------------------------------------------------------


@pytest.mark.parametrize("ml, p", [(-150, 0.6), (150, 0.4), (100, 0.5), (-100, 0.5), (-110, 110 / 210)])
def test_american_to_prob(ml, p):
    assert vm.american_to_prob(ml) == pytest.approx(p)


@pytest.mark.parametrize("ml", [0, 50, -99.5])
def test_american_to_prob_rejects_invalid(ml):
    with pytest.raises(ValueError):
        vm.american_to_prob(ml)


def test_devig_symmetric_line_is_fifty_fifty():
    raw = vm.american_to_prob(-110)
    assert vm.devig_odds_power_method(raw, raw) == pytest.approx((0.5, 0.5))


def test_devig_power_method_solves_exponent():
    pa_raw, pb_raw = 0.75, 0.30            # overround 1.05
    pa, pb = vm.devig_odds_power_method(pa_raw, pb_raw)
    assert pa + pb == pytest.approx(1.0, abs=1e-12)
    k = math.log(pa) / math.log(pa_raw)
    assert k > 1.0                          # vig present -> k > 1
    assert pb == pytest.approx(pb_raw**k, rel=1e-9)
    # Power method loads more margin onto the longshot than normalization does.
    assert pa > pa_raw / (pa_raw + pb_raw)


def test_devig_handles_underround():
    pa, pb = vm.devig_odds_power_method(0.60, 0.35)   # sum < 1 -> k < 1
    assert pa + pb == pytest.approx(1.0)
    assert pa > 0.6 and pb > 0.35


@pytest.mark.parametrize("pa, pb", [(0.0, 0.5), (0.5, 1.0), (1.2, 0.3)])
def test_devig_rejects_invalid(pa, pb):
    with pytest.raises(ValueError):
        vm.devig_odds_power_method(pa, pb)


def test_devig_moneylines_heavy_favorite():
    pa, pb = vm.devig_moneylines(-900, 600)
    assert pa + pb == pytest.approx(1.0)
    assert 0.85 < pa < 0.9


# --- tranches ------------------------------------------------------------------


@pytest.mark.parametrize("spread, tranche", [
    (0.0, vm.TOSS_UP), (-3.0, vm.TOSS_UP), (2.5, vm.TOSS_UP), (3.0, vm.TOSS_UP),
    (-3.5, vm.COMPETITIVE), (-9.5, vm.COMPETITIVE), (6.0, vm.COMPETITIVE),
    (-10.0, vm.HEAVY_FAVORITE), (12.0, vm.HEAVY_FAVORITE), (-25.5, vm.HEAVY_FAVORITE),
])
def test_spread_tranche(spread, tranche):
    assert vm.spread_tranche(spread) == tranche


# --- metric primitives ---------------------------------------------------------


def test_ats_excludes_pushes_and_no_picks():
    pred = np.array([5.0, 2.0, 4.0, 3.0])
    actual = np.array([10.0, 1.0, 3.0, -8.0])
    spread = np.array([-6.0, -2.0, -3.0, -1.0])
    # g1 edge -1 pick B, result +4 -> wrong; g2 edge 0 -> no pick;
    # g3 edge +1 pick A, result 0 -> push; g4 edge +2 pick A, result -9 -> wrong
    acc, n = vm.ats_accuracy(pred, actual, spread)
    assert (acc, n) == (0.0, 2)


def test_brier_and_skill():
    p, y = np.array([0.8, 0.3]), np.array([1.0, 0.0])
    assert vm.brier_score(p, y) == pytest.approx((0.04 + 0.09) / 2)
    assert vm.brier_skill_score(0.15, 0.20) == pytest.approx(0.25)
    assert math.isnan(vm.brier_skill_score(0.15, 0.0))


def test_empty_metrics_are_nan():
    e = np.array([])
    assert math.isnan(vm.su_accuracy(e, e))
    assert math.isnan(vm.rmse_team(e, e, e, e))
    acc, n = vm.ats_accuracy(e, e, e)
    assert math.isnan(acc) and n == 0


# --- audit on a synthetic slate ----------------------------------------------------

# (game_id, date, act_a, act_b, hca, pred_a, pred_b, win_prob_a, spread_a, ml_a, ml_b)
SLATE = [
    ("g1", date(2026, 1, 10), 80, 70, 3.3, 75.0, 70.0, 0.70, -6.0, -250, 210),   # competitive
    ("g2", date(2026, 1, 10), 65, 70, 3.3, 72.0, 70.0, 0.58, -2.0, -130, 110),   # toss-up, no ATS pick
    ("g3", date(2026, 1, 11), 60, 75, 0.0, 64.0, 74.0, 0.20, 12.0, 600, -900),   # heavy (A is dog)
    ("g4", date(2026, 1, 11), 77, 74, 3.3, 76.0, 71.0, 0.66, -3.0, -150, 130),   # toss-up, ATS push
    ("g5", date(2026, 1, 12), 90, 70, 3.3, 85.0, 72.0, 0.88, -11.0, None, None), # heavy, no ML
    ("g6", date(2026, 1, 12), 70, 68, 0.0, 70.0, 71.0, 0.45, None, None, None),  # no odds
]
VERSION = "test-v"


def audit_tuple(g):
    return (g[2], g[3], g[4], g[5], g[6], g[7], g[8], g[9], g[10])


@pytest.fixture(scope="module")
def report():
    return vm.audit_rows([audit_tuple(g) for g in SLATE], VERSION, date(2026, 1, 1), date(2026, 1, 31))


def test_audit_su_and_rmse(report):
    assert report.n_games == 6
    assert report.su_model == pytest.approx(4 / 6)                 # g2, g6 wrong
    assert report.rmse_team == pytest.approx(math.sqrt(139 / 12))
    assert report.rmse_total == pytest.approx(math.sqrt(117 / 6))


def test_audit_ats(report):
    # Decided: g1 wrong, g3 wrong, g5 right (g2 no pick, g4 push, g6 no line)
    assert report.n_spread == 5
    assert (report.ats, report.ats_n) == (pytest.approx(1 / 3), 3)


def test_audit_market_comparison(report):
    m = report.market
    assert m.n == 4                                                # g1-g4 have moneylines
    y = np.array([1.0, 0.0, 0.0, 1.0])
    p_model = np.array([0.70, 0.58, 0.20, 0.66])
    p_vegas = np.array([vm.devig_moneylines(a, b)[0]
                        for a, b in [(-250, 210), (-130, 110), (600, -900), (-150, 130)]])
    bs_model, bs_vegas = ((p_model - y) ** 2).mean(), ((p_vegas - y) ** 2).mean()
    assert m.brier_model == pytest.approx(bs_model) == pytest.approx(0.1455)
    assert m.brier_vegas == pytest.approx(bs_vegas)
    assert m.bss == pytest.approx(1 - bs_model / bs_vegas)
    assert (m.su_model, m.su_vegas) == (0.75, 0.75)


def test_audit_tranches(report):
    t = report.tranches
    assert (t[vm.TOSS_UP].n_spread, t[vm.TOSS_UP].ats_n) == (2, 0)
    assert math.isnan(t[vm.TOSS_UP].ats)
    assert t[vm.TOSS_UP].market.n == 2
    assert (t[vm.COMPETITIVE].n_spread, t[vm.COMPETITIVE].ats, t[vm.COMPETITIVE].ats_n) == (1, 0.0, 1)
    assert (t[vm.HEAVY_FAVORITE].n_spread, t[vm.HEAVY_FAVORITE].ats) == (2, 0.5)
    assert t[vm.HEAVY_FAVORITE].market.n == 1                      # g5 lacks ML
    assert sum(tm.n_spread for tm in t.values()) == report.n_spread


def test_audit_hca_residuals(report):
    assert (report.n_home, report.margin_residual_home) == (4, pytest.approx((5 - 7 - 2 + 7) / 4))
    assert (report.n_neutral, report.margin_residual_neutral) == (2, pytest.approx((-5 + 3) / 2))


def test_format_report_renders(report):
    text = vm.format_report(report)
    assert "Brier Skill Score" in text and "Heavy favorites" in text and "n/a" in text


# --- DB-backed audit ----------------------------------------------------------------


def seed_slate(con, model_version=VERSION, games=True):
    for g in SLATE:
        gid, d, aa, ab, hca, pa, pb, pw, spread, mla, mlb = g
        if games:
            con.execute("INSERT INTO historical_games VALUES (?, ?, 'A', 'B', ?, ?, 'arena', ?)",
                        [gid, d, aa, ab, hca])
            if spread is not None:
                con.execute("INSERT INTO vegas_closing_odds VALUES (?, ?, ?, ?)",
                            [gid, spread, mla, mlb])
        con.execute(
            "INSERT INTO model_predictions (game_id, model_version, as_of, snapshot_date, "
            "pred_score_a, pred_score_b, pred_spread_a, pred_total, win_prob_a, hca_eff, n_sims) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1000)",
            [gid, model_version, d, d, pa, pb, pb - pa, pa + pb, pw, hca])


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cbb.duckdb"


def test_run_backtest_audit_matches_in_memory(db_path, report):
    with db.get_db_connection(db_path=db_path) as con:
        seed_slate(con)
        seed_slate(con, model_version="other", games=False)   # must be ignored
        con.execute("INSERT INTO historical_games VALUES "
                    "('old', '2024-01-10', 'A', 'B', 50, 90, 'x', 3.3)")  # other season
    r = vm.run_backtest_audit(2026, VERSION, db_path=db_path)
    assert r.n_games == 6 and r.start == date(2025, 7, 1) and r.end == date(2026, 6, 30)
    assert r.su_model == report.su_model and r.ats == report.ats
    assert r.market.bss == pytest.approx(report.market.bss)

    narrowed = vm.run_backtest_audit(2026, VERSION, start=date(2026, 1, 11),
                                     end=date(2026, 1, 11), db_path=db_path)
    assert narrowed.n_games == 2


def test_season_window():
    assert vm.season_window(2026) == (date(2025, 7, 1), date(2026, 6, 30))
    with pytest.raises(ValueError):
        vm.resolve_window(2026, date(2026, 7, 1), None)


# --- walk-forward predictions -----------------------------------------------------


SNAP_1, SNAP_FUTURE = date(2026, 1, 1), date(2026, 1, 20)


@pytest.fixture
def pred_db(db_path):
    with db.get_db_connection(db_path=db_path) as con:
        con.executemany("INSERT INTO kenpom_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (SNAP_1, "Home U", 10.0, 112.0, 102.0, 68.0, 0.35),
            (SNAP_1, "Away St.", 5.0, 108.0, 103.0, 70.0, None),
            # A future snapshot with absurd ratings: must never leak into earlier games.
            (SNAP_FUTURE, "Home U", 100.0, 200.0, 100.0, 68.0, 0.35),
            (SNAP_FUTURE, "Away St.", 5.0, 108.0, 103.0, 70.0, None),
        ])
        con.executemany("INSERT INTO historical_games VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ("ok-home", date(2026, 1, 2), "Home U", "Away St.", 75, 70, "Home Arena", 3.3),
            ("ok-neutral", date(2026, 1, 3), "Home U", "Away St.", 70, 72, "Neutral Site", 0.0),
            ("stale", date(2026, 1, 10), "Home U", "Away St.", 70, 60, "Home Arena", 3.3),
            ("no-ratings", date(2025, 12, 15), "Home U", "Away St.", 70, 60, "Home Arena", 3.3),
        ])
        con.execute("INSERT INTO vegas_closing_odds VALUES ('ok-home', -4.5, -200, 170)")
    return db_path


def stored_predictions(path, version):
    with db.get_db_connection(read_only=True, db_path=path) as con:
        rows = con.execute("SELECT game_id, snapshot_date, pred_spread_a, win_prob_a, cover_prob_a, "
                           "hca_eff FROM model_predictions WHERE model_version = ? ORDER BY game_id",
                           [version]).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_generate_predictions_point_in_time(pred_db):
    with db.get_db_connection(db_path=pred_db) as con:
        run = vm.generate_predictions(con, date(2025, 7, 1), date(2026, 6, 30), n_sims=2_000)
    assert run.model_version == vm.DEFAULT_MODEL_VERSION
    assert run.written == 2
    assert run.skipped_no_ratings == ["no-ratings"]
    assert run.skipped_stale == ["stale"]

    preds = stored_predictions(pred_db, vm.DEFAULT_MODEL_VERSION)
    snap, spread, win_p, cover_p, hca = preds["ok-home"]
    assert snap == SNAP_1                       # never the 01-20 snapshot
    assert -12 < spread < 0 and 0.5 < win_p < 0.9   # sane, not the AdjO-200 blowout
    assert 0.0 < cover_p < 1.0 and hca == 3.3
    assert preds["ok-neutral"][4] == 0.0 and preds["ok-neutral"][3] is None


def test_generate_predictions_hca_override_and_determinism(pred_db):
    with db.get_db_connection(db_path=pred_db) as con:
        run = vm.generate_predictions(con, date(2026, 1, 1), date(2026, 1, 5),
                                      n_sims=2_000, seed=7, hca_override=2.0)
        again = vm.generate_predictions(con, date(2026, 1, 2), date(2026, 1, 2),
                                        model_version="again", n_sims=2_000, seed=7,
                                        hca_override=2.0)
    assert run.model_version == "lean-v1-hca2"
    preds = stored_predictions(pred_db, "lean-v1-hca2")
    assert preds["ok-home"][4] == 2.0 and preds["ok-neutral"][4] == 0.0
    # Per-game seeding: same game, same seed -> identical result regardless of slate.
    assert stored_predictions(pred_db, "again")["ok-home"][1:3] == preds["ok-home"][1:3]
    assert again.written == 1


def test_cli_predict_and_audit(pred_db, capsys):
    assert vm.main(["predict", "--season", "2026", "--sims", "1000", "--db-path", str(pred_db)]) == 0
    assert "wrote 2 predictions" in capsys.readouterr().out
    assert vm.main(["audit", "--season", "2026", "--db-path", str(pred_db)]) == 0
    out = capsys.readouterr().out
    assert "lean-v1" in out and "2 games" in out
