"""Model Performance scorecard: metrics vs. Vegas, grading, trends, calibration."""

from datetime import date, timedelta

import pytest

from src import scorecard as sc

START = date(2025, 11, 3)   # a Monday


def game(day, act_a, act_b, pred_a, pred_b, p_win, spread=None, ml_a=None, ml_b=None, hca=1.9):
    return (day, act_a, act_b, hca, pred_a, pred_b, p_win, spread, ml_a, ml_b)


def test_scorecard_matches_hand_computation():
    rows = [
        game(START, 80, 70, 78, 72, 0.70, spread=-4.0, ml_a=-200, ml_b=170),   # fav A wins, covers
        game(START, 60, 66, 70, 68, 0.55, spread=-1.0, ml_a=-120, ml_b=100),   # model A, B wins
    ]
    s = sc.scorecard(rows)
    assert s.n == s.n_ml == s.n_spread == 2
    assert s.su_model == 0.5 and s.su_vegas == 0.5
    assert s.ats == 0.5 and s.ats_n == 2
    # model margins 6, 2 vs actual 10, -6 -> errors 4, -8 ; Vegas margins 4, 1 -> errors 6, -7
    assert s.margin_rmse_model == pytest.approx(((16 + 64) / 2) ** 0.5)
    assert s.margin_rmse_vegas == pytest.approx(((36 + 49) / 2) ** 0.5)
    assert s.home_bias == pytest.approx((4 - 8) / 2)


def test_empty_scorecard_is_nan_not_error():
    s = sc.scorecard([])
    assert s.n == 0 and s.total_rmse != s.total_rmse
    assert all(sc.status(m, s) in (None, "early") for m in sc.METRICS)


def _metric(key):
    return next(m for m in sc.METRICS if m.key == key)


@pytest.mark.parametrize("ats, expected", [(0.55, "good"), (0.524, "ok"), (0.51, "ok"), (0.49, "poor")])
def test_ats_status_bands(ats, expected):
    s = sc.scorecard([]).__class__(**{**sc.scorecard([]).__dict__, "ats": ats, "ats_n": 500})
    assert sc.status(_metric("ats"), s) == (expected if ats != 0.524 else "good")


def test_lower_is_better_metrics_flip_sign():
    base = sc.scorecard([]).__dict__
    s = sc.Scorecard(**{**base, "margin_rmse_model": 11.0, "margin_rmse_vegas": 11.4, "n_spread": 500})
    m = _metric("margin")
    assert sc.advantage(m, s) == pytest.approx(0.4) and sc.status(m, s) == "good"
    worse = sc.Scorecard(**{**base, "margin_rmse_model": 12.2, "margin_rmse_vegas": 11.4, "n_spread": 500})
    assert sc.status(m, worse) == "poor"


def test_bias_graded_by_distance_from_zero():
    base = sc.scorecard([]).__dict__
    m = _metric("bias")
    for bias, grade in [(0.4, "good"), (-1.5, "ok"), (2.6, "poor")]:
        assert sc.status(m, sc.Scorecard(**{**base, "home_bias": bias, "n_home": 500})) == grade


def test_small_samples_are_too_early():
    base = sc.scorecard([]).__dict__
    s = sc.Scorecard(**{**base, "ats": 0.60, "ats_n": sc.MIN_SAMPLE - 1})
    assert sc.status(_metric("ats"), s) == "early"


def test_total_has_no_benchmark_or_status():
    rows = [game(START, 70, 60, 68, 62, 0.6)] * 3
    m = _metric("total")
    assert sc.status(m, sc.scorecard(rows)) is None


def _season(weeks_good_ats):
    """Weekly rows: in good weeks the model's side covers every game, else never."""
    rows = []
    for w, good in enumerate(weeks_good_ats):
        day = START + timedelta(weeks=w)
        for k in range(sc.MIN_WEEK_GAMES):
            act = (75, 65) if good else (65, 75)
            rows.append(game(day + timedelta(days=k % 5), *act, 72, 68, 0.6, spread=-2.0, ml_a=-130, ml_b=110))
    return rows


def test_weekly_series_and_direction():
    rows = _season([False, False, False, False, True, True, True])
    weeks = sc.weekly(rows)
    m = _metric("ats")
    series = sc.weekly_series(m, weeks)
    assert len(series) == 7
    assert series[0] == pytest.approx(-sc.ATS_BREAK_EVEN) and series[-1] == pytest.approx(1 - sc.ATS_BREAK_EVEN)
    assert sc.direction(m, sc.scorecard(rows), sc.scorecard(sc.recent(rows)), len(weeks)) == "up"
    flipped = _season([True] * 4 + [False] * 3)
    assert sc.direction(m, sc.scorecard(flipped), sc.scorecard(sc.recent(flipped)), 7) == "down"
    assert sc.direction(m, sc.scorecard(rows), sc.scorecard(rows), 2) is None   # too few weeks


def test_small_weeks_are_dropped():
    rows = [game(START, 70, 60, 68, 62, 0.6)] * (sc.MIN_WEEK_GAMES - 1)
    assert sc.weekly(rows) == []


def test_recent_window_is_relative_to_last_game():
    rows = [game(START + timedelta(days=d), 70, 60, 68, 62, 0.6) for d in (0, 30, 40, 50)]
    assert [r[0] for r in sc.recent(rows, days=21)] == [START + timedelta(days=d) for d in (30, 40, 50)]


def test_calibration_buckets_favorites():
    rows = [game(START, 70, 60, 68, 62, 0.75, ml_a=-300, ml_b=250),     # A fav 75%, wins
            game(START, 60, 70, 68, 62, 0.72, ml_a=-250, ml_b=210),     # A fav 72%, loses
            game(START, 60, 70, 62, 68, 0.35, ml_a=150, ml_b=-170),     # B fav 65%, wins
            game(START, 60, 70, 62, 68, 0.35)]                          # no moneyline: skipped
    cal = {(round(c.lo, 1)): c for c in sc.calibration(rows)}
    assert set(cal) == {0.6, 0.7}
    assert cal[0.7].n == 2 and cal[0.7].actual == 0.5 and cal[0.7].model_said == pytest.approx(0.735)
    assert cal[0.6].n == 1 and cal[0.6].actual == 1.0 and 0.5 < cal[0.6].vegas_said < 0.7
