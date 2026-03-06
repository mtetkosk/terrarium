"""Parity tests for programmatic Modeler engine."""

from src.agents import modeler_engine as me
from src.agents.modeler_engine import GameContext, TeamContext


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


# --- Tempo multiplier ---


def test_tempo_multiplier_below_70():
    assert me.calculate_tempo_multiplier(68.0) == 1.0
    assert me.calculate_tempo_multiplier(69.5) == 1.0


def test_tempo_multiplier_70_to_72():
    # Threshold is strict: final_pace > 70
    assert me.calculate_tempo_multiplier(70.0) == 1.0
    assert me.calculate_tempo_multiplier(70.1) == 1.015
    assert me.calculate_tempo_multiplier(71.0) == 1.015


def test_tempo_multiplier_72_to_74():
    # Threshold is strict: final_pace > 72
    assert me.calculate_tempo_multiplier(72.0) == 1.015
    assert me.calculate_tempo_multiplier(72.1) == 1.03
    assert me.calculate_tempo_multiplier(73.5) == 1.03


def test_tempo_multiplier_above_74():
    # Threshold is strict: final_pace > 74
    assert me.calculate_tempo_multiplier(74.0) == 1.03
    assert me.calculate_tempo_multiplier(74.1) == 1.05
    assert me.calculate_tempo_multiplier(76.0) == 1.05


# --- Conference grudge adjustment ---


def _make_stats(away_conf=None, home_conf=None, is_rivalry=False):
    away = TeamContext(
        name="Away",
        team_id=None,
        adjo=108.0,
        adjd=98.0,
        adjt=65.0,
        pace_trend=None,
        conference=away_conf,
    )
    home = TeamContext(
        name="Home",
        team_id=None,
        adjo=115.0,
        adjd=95.0,
        adjt=68.0,
        pace_trend=None,
        conference=home_conf,
    )
    return GameContext(
        game_id="test",
        away=away,
        home=home,
        market_total=None,
        market_spread_home=None,
        is_neutral_site=False,
        is_rivalry=is_rivalry,
    )


def test_conference_grudge_same_conference():
    stats = _make_stats(away_conf="ACC", home_conf="ACC")
    is_conf, grudge_adj, hca_red = me.calculate_conference_grudge_adjustment(stats, final_pace=70.0)
    assert is_conf is True
    assert hca_red == 1.0
    assert grudge_adj == 3.0  # 68 < pace <= 72


def test_conference_grudge_high_pace():
    stats = _make_stats(away_conf="SEC", home_conf="SEC")
    _, grudge_adj, _ = me.calculate_conference_grudge_adjustment(stats, final_pace=73.0)
    assert grudge_adj == 4.0


def test_conference_grudge_slow_pace():
    stats = _make_stats(away_conf="Big East", home_conf="Big East")
    _, grudge_adj, _ = me.calculate_conference_grudge_adjustment(stats, final_pace=66.0)
    assert grudge_adj == 2.0


def test_conference_grudge_different_conference():
    stats = _make_stats(away_conf="ACC", home_conf="SEC")
    is_conf, grudge_adj, hca_red = me.calculate_conference_grudge_adjustment(stats, final_pace=70.0)
    assert is_conf is False
    assert grudge_adj == 0.0
    assert hca_red == 0.0


def test_conference_grudge_rivalry_no_same_conf():
    stats = _make_stats(away_conf=None, home_conf=None, is_rivalry=True)
    _, grudge_adj, hca_red = me.calculate_conference_grudge_adjustment(stats, final_pace=70.0)
    assert hca_red == 1.0
    assert grudge_adj == 3.0


def test_conference_grudge_missing_conference():
    stats = _make_stats(away_conf="ACC", home_conf=None)
    is_conf, grudge_adj, hca_red = me.calculate_conference_grudge_adjustment(stats, final_pace=70.0)
    assert is_conf is False
    assert hca_red == 0.0
    assert grudge_adj == 0.0


# --- Discrepancy shrinkage ---


def test_discrepancy_shrinkage_none():
    away, home, applied, factor = me.apply_discrepancy_shrinkage(0.3, 0.7, edge_mag=5.0)
    assert applied is False
    assert factor == 1.0
    assert abs(away - 0.3) < 0.001 and abs(home - 0.7) < 0.001


def test_discrepancy_shrinkage_25_percent():
    away, home, applied, factor = me.apply_discrepancy_shrinkage(0.2, 0.8, edge_mag=7.0)
    assert applied is True
    assert factor == 0.75
    # 0.5 + (0.8 - 0.5) * 0.75 = 0.5 + 0.225 = 0.725
    assert abs(home - 0.725) < 0.01
    assert abs(away + home - 1.0) < 0.001


def test_discrepancy_shrinkage_50_percent():
    away, home, applied, factor = me.apply_discrepancy_shrinkage(0.1, 0.9, edge_mag=9.0)
    assert applied is True
    assert factor == 0.50
    # 0.5 + (0.9 - 0.5) * 0.5 = 0.5 + 0.2 = 0.7
    assert abs(home - 0.7) < 0.01
    assert abs(away + home - 1.0) < 0.001


# --- Power conference / mismatch ---


def test_is_power_conference_acc():
    assert me._is_power_conference("ACC") is True
    assert me._is_power_conference("acc") is True


def test_is_power_conference_big_ten_variants():
    assert me._is_power_conference("B10") is True
    assert me._is_power_conference("BIG TEN") is True
    assert me._is_power_conference("Big 10") is True


def test_is_power_conference_mid_major():
    assert me._is_power_conference("A-10") is False
    assert me._is_power_conference("Mountain West") is False
    assert me._is_power_conference("WCC") is False


def test_mismatch_home_power_away_not():
    stats = _make_stats(away_conf="A-10", home_conf="ACC")
    adj = me.calculate_mismatch_adjustment(stats, "A-10", "ACC")
    assert adj == 5.0


def test_mismatch_away_power_home_not():
    stats = _make_stats(away_conf="SEC", home_conf="Summit")
    adj = me.calculate_mismatch_adjustment(stats, "SEC", "Summit")
    assert adj == -5.0


def test_mismatch_both_power():
    stats = _make_stats(away_conf="ACC", home_conf="SEC")
    adj = me.calculate_mismatch_adjustment(stats, "ACC", "SEC")
    assert adj == 0.0


# --- Market edge helpers (via calculate_market_edges) ---


def test_spread_edge_calculation():
    predicted = {
        "margin": -5.0,
        "total": 150.0,
        "win_probs": {"away": 0.4, "home": 0.6},
        "confidence": 0.5,
    }
    lines = [{"bet_type": "spread", "line": -4.0, "odds": -110, "team": "home"}]
    edges = me.calculate_market_edges(predicted, lines)
    assert len(edges) == 1
    assert edges[0]["market_type"] == "SPREAD_HOME"
    assert edges[0]["market_line"] == "-4.0"
    assert 0.0 <= edges[0]["model_estimated_probability"] <= 1.0
    assert edges[0]["edge_confidence"] == 0.5


def test_total_edge_calculation():
    predicted = {
        "margin": 0.0,
        "total": 148.0,
        "win_probs": {"away": 0.5, "home": 0.5},
        "confidence": 0.4,
    }
    lines = [{"bet_type": "total", "line": 150.0, "odds": -110, "team": "over"}]
    edges = me.calculate_market_edges(predicted, lines)
    assert len(edges) == 2  # over and under
    types = {e["market_type"] for e in edges}
    assert "TOTAL_OVER" in types and "TOTAL_UNDER" in types
    assert all(0.0 <= e["model_estimated_probability"] <= 1.0 for e in edges)


def test_moneyline_edge_calculation():
    predicted = {
        "margin": 3.0,
        "total": 150.0,
        "win_probs": {"away": 0.45, "home": 0.55},
        "confidence": 0.6,
    }
    lines = [
        {"bet_type": "moneyline", "line": 0, "odds": 120, "team": "away"},
        {"bet_type": "moneyline", "line": 0, "odds": -140, "team": "home"},
    ]
    edges = me.calculate_market_edges(predicted, lines)
    assert len(edges) == 2
    assert edges[0]["market_type"] == "MONEYLINE_AWAY"
    assert edges[1]["market_type"] == "MONEYLINE_HOME"
    assert abs(edges[0]["model_estimated_probability"] - 0.45) < 0.01
    assert abs(edges[1]["model_estimated_probability"] - 0.55) < 0.01


# --- build_model_output ---


def test_build_model_output_structure():
    predictions = {
        "scores": {"away": 72.0, "home": 78.0},
        "margin": 6.0,
        "total": 150.0,
        "win_probs": {"away": 0.4, "home": 0.6},
        "confidence": 0.5,
    }
    meta = {"base_pace": 67.0, "final_pace": 67.0}
    out = me.build_model_output(
        game_id="g1",
        away_team="Away",
        home_team="Home",
        away_id=1,
        home_id=2,
        predictions=predictions,
        away_score=72.0,
        home_score=78.0,
        market_edges=[],
        edge_mag=3.0,
        meta=meta,
    )
    assert out["game_id"] == "g1"
    assert out["teams"]["away"] == "Away" and out["teams"]["home"] == "Home"
    assert out["teams"]["away_id"] == 1 and out["teams"]["home_id"] == 2
    assert out["predictions"] == predictions
    assert out["predicted_score"]["away_score"] == 72.0 and out["predicted_score"]["home_score"] == 78.0
    assert out["market_analysis"]["edge_magnitude"] == 3.0
    assert out["meta"] == meta
    assert out["ev_estimate"] == 0.0


# --- calculate_game_model end-to-end ---


def test_calculate_game_model_end_to_end():
    away = TeamContext(
        name="Team A",
        team_id=10,
        adjo=110.0,
        adjd=100.0,
        adjt=68.0,
        pace_trend=None,
        conference="ACC",
    )
    home = TeamContext(
        name="Team B",
        team_id=20,
        adjo=108.0,
        adjd=102.0,
        adjt=66.0,
        pace_trend=None,
        conference="ACC",
    )
    ctx = GameContext(
        game_id="test-1",
        away=away,
        home=home,
        market_total=145.0,
        market_spread_home=-4.0,
        is_neutral_site=False,
        is_rivalry=False,
    )
    lines = [
        {"bet_type": "spread", "line": -4.0, "odds": -110, "team": "home"},
        {"bet_type": "total", "line": 145.0, "odds": -110, "team": "over"},
    ]
    model = me.calculate_game_model(ctx, lines, has_adv_stats=True)
    assert model["game_id"] == "test-1"
    assert model["teams"]["away"] == "Team A" and model["teams"]["home"] == "Team B"
    assert "predictions" in model
    assert "scores" in model["predictions"]
    assert "margin" in model["predictions"]
    assert "total" in model["predictions"]
    assert "win_probs" in model["predictions"]
    assert "confidence" in model["predictions"]
    assert "market_edges" in model
    assert "meta" in model
    assert "base_pace" in model["meta"]
    assert "final_pace" in model["meta"]
    assert 0.99 <= model["predictions"]["win_probs"]["away"] + model["predictions"]["win_probs"]["home"] <= 1.01

