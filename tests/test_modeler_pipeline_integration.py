"""Integration test to verify modeler receives all inputs from researcher correctly."""

import pytest
from src.agents.modeler import Modeler
from src.agents.modeler_engine import calculate_game_model, GameContext


def test_researcher_to_modeler_data_flow():
    """Test that researcher output format correctly feeds into modeler engine."""
    
    # Sample researcher output matching the actual schema and cache format
    researcher_game_output = {
        "game_id": "1234",
        "league": "NCAA",
        "teams": {
            "away": "Virginia",
            "home": "NC State",
            "away_id": 697,
            "home_id": 355
        },
        "start_time": "2026-01-03T00:00:00Z",
        "market": {
            "spread": "virginia +4.5",  # Away team +4.5 means home is -4.5
            "total": 152.5,
            "moneyline": {
                "home": "-218",
                "away": "+180"
            }
        },
        "adv": {
            "away": {
                "kp_rank": 27,
                "adjo": 121.9,
                "adjd": 101.0,
                "adjt": 67.9,
                "net": 20.93,
                "conference": "ACC",
                "wins": 11,
                "losses": 2,
                "w_l": "11-2",
                "luck": -0.028,
                "sos": -2.12
            },
            "home": {
                "kp_rank": 24,
                "adjo": 120.2,
                "adjd": 98.7,
                "adjt": 71.2,
                "net": 21.53,
                "conference": "ACC",
                "wins": 10,
                "losses": 4,
                "w_l": "10-4",
                "luck": -0.073,
                "sos": 5.37
            },
            "matchup": []
        },
        "injuries": [],
        "recent": {
            "away": {
                "rec": "3-0",
                "last_3_avg_score": 75.0,
                "pace_trend": "same",  # This is in actual output but not in schema
                "notes": "solid defense"
            },
            "home": {
                "rec": "2-1",
                "last_3_avg_score": 78.0,
                "pace_trend": "faster",  # Test with different trend
                "notes": "high scoring"
            }
        },
        "experts": {
            "src": 1,
            "spread_pick": "Virginia +4.5",
            "total_pick": "Under 151.5",
            "scores": [],
            "reason": "Strong defensive matchup"
        },
        "common_opp": [],
        "context": [
            "Rest: Away 2d, Home 2d"
        ],
        "dq": []
    }
    
    # Step 1: Build GameContext from researcher output
    ctx = GameContext.from_researcher_output(researcher_game_output)
    
    # Verify extraction worked
    assert ctx is not None, "GameContext should be extracted successfully"
    
    # Verify all required fields are present
    assert ctx.away.adjo == 121.9
    assert ctx.away.adjd == 101.0
    assert ctx.away.adjt == 67.9
    assert ctx.away.conference == "ACC"
    assert ctx.away.pace_trend == "same"
    
    assert ctx.home.adjo == 120.2
    assert ctx.home.adjd == 98.7
    assert ctx.home.adjt == 71.2
    assert ctx.home.conference == "ACC"
    assert ctx.home.pace_trend == "faster"
    
    assert ctx.market_total == 152.5
    assert ctx.market_spread_home == -4.5  # Should parse "virginia +4.5" as home -4.5
    assert ctx.is_neutral_site is False  # No "neutral site" in context
    
    # Step 2: Run the modeler engine with context
    betting_lines = [
        {
            "bet_type": "spread",
            "line": -4.5,
            "odds": -110,
            "team": "home"
        },
        {
            "bet_type": "total",
            "line": 152.5,
            "odds": -110
        },
        {
            "bet_type": "moneyline",
            "line": 0,
            "odds": -218,
            "team": "home"
        }
    ]
    
    model = calculate_game_model(ctx, betting_lines, has_adv_stats=True)
    
    # Verify model output structure (raw output from calculate_game_model)
    assert "game_id" in model
    assert "predictions" in model
    assert "market_edges" in model
    assert model["game_id"] == "1234"
    
    # Verify predictions include required fields (raw structure before transformation)
    pred = model["predictions"]
    assert "scores" in pred
    assert "margin" in pred
    assert "total" in pred
    assert "win_probs" in pred
    assert "confidence" in pred
    assert "away" in pred["win_probs"]
    assert "home" in pred["win_probs"]
    
    # Verify market edges were calculated
    assert len(model["market_edges"]) > 0
    
    # Verify meta contains all adjustments
    meta = model.get("meta", {})
    assert "hca_adjustment" in meta
    assert "mismatch_adjustment" in meta
    assert "base_pace" in meta
    assert "final_pace" in meta
    
    # Verify margin and probabilities are rounded to 2 decimal places
    margin = pred["margin"]
    away_prob = pred["win_probs"]["away"]
    home_prob = pred["win_probs"]["home"]
    
    # Check that values are rounded to 2 decimal places
    assert margin == round(margin, 2), f"Margin {margin} should be rounded to 2 decimals"
    assert away_prob == round(away_prob, 2), f"Away prob {away_prob} should be rounded to 2 decimals"
    assert home_prob == round(home_prob, 2), f"Home prob {home_prob} should be rounded to 2 decimals"
    
    # Verify they have at most 2 decimal places
    margin_str = str(margin)
    away_prob_str = str(away_prob)
    home_prob_str = str(home_prob)
    if '.' in margin_str:
        assert len(margin_str.split('.')[1]) <= 2, f"Margin {margin} has more than 2 decimal places"
    if '.' in away_prob_str:
        assert len(away_prob_str.split('.')[1]) <= 2, f"Away prob {away_prob} has more than 2 decimal places"
    if '.' in home_prob_str:
        assert len(home_prob_str.split('.')[1]) <= 2, f"Home prob {home_prob} has more than 2 decimal places"


def test_missing_conference_handles_gracefully():
    """Test that missing conference doesn't break mismatch adjustment."""
    game_data = {
        "game_id": "5678",
        "teams": {"away": "Team A", "home": "Team B"},
        "market": {"spread": "Team A +5", "total": 150.0},
        "adv": {
            "away": {"adjo": 110.0, "adjd": 100.0, "adjt": 70.0},  # No conference
            "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 72.0, "conference": "SEC"}
        },
        "recent": {"away": {}, "home": {}},
        "context": []
    }
    
    ctx = GameContext.from_researcher_output(game_data)
    assert ctx is not None
    assert ctx.away.conference is None
    assert ctx.home.conference == "SEC"
    
    # Should not crash with missing conference
    model = calculate_game_model(ctx, [], has_adv_stats=True)
    assert model is not None


def test_neutral_site_detection():
    """Test that neutral site games are detected correctly."""
    game_data = {
        "game_id": "9999",
        "teams": {"away": "Team A", "home": "Team B"},
        "market": {"spread": "Team A +5", "total": 150.0},
        "adv": {
            "away": {"adjo": 110.0, "adjd": 100.0, "adjt": 70.0},
            "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 72.0}
        },
        "recent": {"away": {}, "home": {}},
        "context": ["Neutral site game at MSG"]
    }
    
    ctx = GameContext.from_researcher_output(game_data)
    assert ctx is not None
    assert ctx.is_neutral_site is True


def test_missing_pace_trend_handles_gracefully():
    """Test that missing pace_trend doesn't break calculations."""
    game_data = {
        "game_id": "8888",
        "teams": {"away": "Team A", "home": "Team B"},
        "market": {"spread": "Team A +5", "total": 150.0},
        "adv": {
            "away": {"adjo": 110.0, "adjd": 100.0, "adjt": 70.0},
            "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 72.0}
        },
        "recent": {"away": {}, "home": {}},  # No pace_trend
        "context": []
    }
    
    ctx = GameContext.from_researcher_output(game_data)
    assert ctx is not None
    assert ctx.away.pace_trend is None
    assert ctx.home.pace_trend is None
    
    # Should default to no trend adjustment
    model = calculate_game_model(ctx, [], has_adv_stats=True)
    assert model is not None


def test_missing_market_data_handles_gracefully():
    """Test that missing market total/spread doesn't break calculations."""
    game_data = {
        "game_id": "7777",
        "teams": {"away": "Team A", "home": "Team B"},
        "market": {},  # No market data
        "adv": {
            "away": {"adjo": 110.0, "adjd": 100.0, "adjt": 70.0},
            "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 72.0}
        },
        "recent": {"away": {}, "home": {}},
        "context": []
    }
    
    ctx = GameContext.from_researcher_output(game_data)
    assert ctx is not None
    assert ctx.market_total is None
    assert ctx.market_spread_home is None
    
    # Should use raw_total and continue without market calibration
    model = calculate_game_model(ctx, [], has_adv_stats=True)
    assert model is not None
    assert "predictions" in model


def test_conference_mismatch_adjustment():
    """Test that conference mismatch adjustment works correctly."""
    # Power conference vs mid-major
    game_data = {
        "game_id": "6666",
        "teams": {"away": "Summit Team", "home": "Big 12 Team"},
        "market": {"spread": "Summit Team +10", "total": 145.0},
        "adv": {
            "away": {"adjo": 100.0, "adjd": 105.0, "adjt": 68.0, "conference": "Summit"},
            "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 72.0, "conference": "Big 12"}
        },
        "recent": {"away": {}, "home": {}},
        "context": []
    }
    
    ctx = GameContext.from_researcher_output(game_data)
    assert ctx is not None
    
    # Run calculation and check that mismatch adjustment is applied
    model = calculate_game_model(ctx, [], has_adv_stats=True)
    assert model is not None
    
    # The mismatch adjustment should add +5.0 to the margin (favoring home)
    # We can verify this by checking the adjustments in meta
    meta = model.get("meta", {})
    # Verify adjustments are tracked
    assert "hca_adjustment" in meta
    assert "mismatch_adjustment" in meta
    # Verify mismatch adjustment is +5.0 for Big 12 (power) vs Summit (mid-major)
    assert meta["mismatch_adjustment"] == 5.0

