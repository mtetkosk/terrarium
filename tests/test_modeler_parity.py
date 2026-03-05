"""Parity tests for programmatic Modeler engine."""

from src.agents import modeler_engine as me


def test_pace_calculation():
    # (65 * 0.65) + (70 * 0.35) = 66.75, no trend
    base, trend, final = me.calculate_pace(70.0, 65.0, None, None)
    assert abs(base - 66.75) < 0.1
    assert trend == 0.0
    assert 62 <= final <= 78


def test_margin_dampening():
    dampened, applied = me.apply_margin_dampening(22.0)
    assert applied is True
    assert abs(dampened - 19.6) < 0.1


def test_total_calibration():
    # Expected values follow calibrate_total v3 in modeler_engine (2026-01-13).
    # Case 1: raw_total > 155 -> regression=0.20, over_adj=1.5; diff=10, calibrated = 160 - 2 + 1.5 = 159.5
    calibrated, regression_pct = me.calibrate_total(raw_total=160.0, market_total=150.0)
    assert abs(calibrated - 159.5) < 0.1
    assert regression_pct == 0.20

    # Case 2: 145 <= raw_total <= 155 -> regression=0.15; diff=5, calibrated = 150 - 0.75 = 149.25
    calibrated2, regression_pct2 = me.calibrate_total(raw_total=150.0, market_total=145.0)
    assert regression_pct2 == 0.15
    assert abs(calibrated2 - 149.25) < 0.1

    # Case 3: low total (<140) -> regression=0.35; diff=5, calibrated = 135 - 1.75 = 133.25
    calibrated3, regression_pct3 = me.calibrate_total(raw_total=135.0, market_total=130.0)
    assert regression_pct3 == 0.35
    assert abs(calibrated3 - 133.25) < 0.1


def test_win_probability_directionality():
    away_prob, home_prob = me.calculate_win_probability(margin=10.0)
    assert home_prob > away_prob
    assert 0.0 <= away_prob <= 1.0
    assert 0.0 <= home_prob <= 1.0


def test_win_probability_sigmoid():
    """Verify sigmoid calculation matches agentic modeler: 1/(1+exp(-margin/7.5))"""
    # From agentic modeler: margin=23.9 => p_home=0.961
    away_prob, home_prob = me.calculate_win_probability(margin=23.9)
    # Manual calc: 1/(1+exp(-23.9/7.5)) = 1/(1+exp(-3.187)) ≈ 1/(1+0.041) ≈ 0.961
    assert abs(home_prob - 0.961) < 0.01
    
    # From agentic modeler: margin=2.5 => p_home=0.583
    away_prob2, home_prob2 = me.calculate_win_probability(margin=2.5)
    # Manual calc: 1/(1+exp(-2.5/7.5)) = 1/(1+exp(-0.333)) ≈ 1/(1+0.717) ≈ 0.583
    assert abs(home_prob2 - 0.583) < 0.01


def test_missing_stats_results_in_skip():
    # If stats are missing, calculate_game_model should not be invoked; this is enforced upstream.
    # This test ensures validator behavior remains explicit (documented expectation).
    assert me.calculate_confidence(has_adv_stats=False, edge_magnitude=5.0) == 0.0

