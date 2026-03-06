"""Tests for Modeler agent"""

import pytest
from unittest.mock import Mock, patch
from datetime import date

from src.agents.modeler import Modeler
from src.agents.modeler_validation import validate_score_team_consistency
from src.agents.modeler_engine import GameContext
from src.data.models import BettingLine, BetType


class TestModelerUnit:
    """Unit tests for Modeler agent (mocked LLM)"""
    
    def test_process_with_mocked_llm(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test modeler processes researcher output (programmatic path; force_refresh to avoid cache)."""
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(
            sample_researcher_output,
            betting_lines=sample_betting_lines,
            force_refresh=True,
        )
        assert "game_models" in result
        assert len(result["game_models"]) == 1
        model = result["game_models"][0]
        assert str(model["game_id"]) == str(sample_researcher_output["games"][0]["game_id"])
        assert "predictions" in model
        assert "market_edges" in model
        assert "predicted_score" in model
        predicted_score = model["predicted_score"]
        assert "away_score" in predicted_score
        assert "home_score" in predicted_score
        predictions = model["predictions"]
        assert "spread" in predictions or "margin" in predictions
        assert "total" in predictions or "scores" in predictions
    
    def test_batch_processing(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test that modeler processes games in batches (programmatic path; force_refresh to avoid cache)."""
        games_output = {"games": sample_researcher_output["games"] * 2}
        games_output["games"][1]["game_id"] = "2"
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(
            games_output,
            betting_lines=sample_betting_lines,
            force_refresh=True,
        )
        assert len(result["game_models"]) == len(games_output["games"])
    
    def test_empty_input(self, mock_database, mock_llm_client):
        """Test modeler handles empty input"""
        empty_output = {"games": []}
        
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(empty_output)
        
        assert result == {"game_models": []}
    
    def test_probability_validation(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test that modeler validates probability ranges (programmatic path; force_refresh to avoid cache)."""
        if "adv" not in sample_researcher_output["games"][0]:
            sample_researcher_output["games"][0]["adv"] = {
                "away": {"adjo": 108.0, "adjd": 98.0, "adjt": 65.0},
                "home": {"adjo": 115.0, "adjd": 95.0, "adjt": 68.0}
            }
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(
            sample_researcher_output,
            betting_lines=sample_betting_lines,
            force_refresh=True,
        )
        assert len(result["game_models"]) >= 1
        model = result["game_models"][0]
        predictions = model.get("predictions", {})
        
        # Check win_probs (programmatic modeler structure)
        win_probs = predictions.get("win_probs", {})
        if win_probs:
            away_prob = win_probs.get("away", 0)
            home_prob = win_probs.get("home", 0)
            total = away_prob + home_prob
            assert 0.9 <= total <= 1.1, f"Win probs sum to {total}, expected ~1.0"
        else:
            # Fallback: check moneyline structure
            moneyline = predictions.get("moneyline", {})
            away_prob = moneyline.get("away_win_prob", 0)
            home_prob = moneyline.get("home_win_prob", 0)
            if away_prob > 0 or home_prob > 0:
                total = away_prob + home_prob
                assert 0.9 <= total <= 1.1, f"Moneyline probs sum to {total}, expected ~1.0"

    def test_validate_score_team_consistency_margin_mismatch(self):
        """validate_score_team_consistency flags when margin != home_score - away_score."""
        model = {
            "game_id": "1",
            "teams": {"away": "A", "home": "B", "away_id": 1, "home_id": 2},
            "predictions": {
                "scores": {"away": 70.0, "home": 78.0},
                "margin": 5.0,
                "win_probs": {"away": 0.4, "home": 0.6},
            },
        }
        game_data = {
            "teams": {"away": "A", "home": "B", "away_id": 1, "home_id": 2},
            "adv": {"away": {"adjo": 100, "adjd": 100, "adjt": 68}, "home": {"adjo": 100, "adjd": 100, "adjt": 68}},
            "recent": {"away": {}, "home": {}},
            "context": [],
        }
        ctx = GameContext.from_researcher_output(game_data)
        assert ctx is not None
        result = validate_score_team_consistency(model, ctx, game_data)
        assert result["valid"] is False
        assert "MARGIN MISMATCH" in (result.get("warning") or "")

    def test_validate_score_team_consistency_valid_model(self):
        """validate_score_team_consistency passes when margin matches scores."""
        model = {
            "game_id": "1",
            "teams": {"away": "A", "home": "B", "away_id": 1, "home_id": 2},
            "predictions": {
                "scores": {"away": 70.0, "home": 78.0},
                "margin": 8.0,
                "win_probs": {"away": 0.4, "home": 0.6},
            },
        }
        game_data = {
            "teams": {"away": "A", "home": "B", "away_id": 1, "home_id": 2},
            "adv": {"away": {"adjo": 100, "adjd": 100, "adjt": 68}, "home": {"adjo": 100, "adjd": 100, "adjt": 68}},
            "recent": {"away": {}, "home": {}},
            "context": [],
        }
        ctx = GameContext.from_researcher_output(game_data)
        assert ctx is not None
        result = validate_score_team_consistency(model, ctx, game_data)
        assert result["valid"] is True


@pytest.mark.integration
class TestModelerIntegration:
    """Integration tests for Modeler agent (real LLM)"""
    
    def test_process_with_real_llm(self, mock_database, real_llm_client, minimal_researcher_output, minimal_betting_lines):
        """Test modeler processes games with real LLM and validates output quality"""
        modeler = Modeler(db=mock_database, llm_client=real_llm_client)
        
        result = modeler.process(
            minimal_researcher_output,
            betting_lines=minimal_betting_lines,
            force_refresh=True
        )
        
        # Verify output structure
        assert "game_models" in result
        assert len(result["game_models"]) >= 1
        
        model = result["game_models"][0]
        
        # Verify required fields
        assert "game_id" in model
        assert "predictions" in model
        assert "market_edges" in model
        
        # Verify game_id matches
        assert model["game_id"] == minimal_researcher_output["games"][0]["game_id"]
        
        # Verify predictions structure
        predictions = model.get("predictions", {})
        assert isinstance(predictions, dict)
        
        # If moneyline predictions exist, verify probabilities
        if "moneyline" in predictions and "team_probabilities" in predictions["moneyline"]:
            probs = predictions["moneyline"]["team_probabilities"]
            if "away" in probs and "home" in probs:
                away_prob = probs["away"]
                home_prob = probs["home"]
                
                # Verify probabilities are in valid range
                assert 0.0 <= away_prob <= 1.0, f"Away probability {away_prob} out of range"
                assert 0.0 <= home_prob <= 1.0, f"Home probability {home_prob} out of range"
                
                # Verify probabilities sum to approximately 1.0
                total = away_prob + home_prob
                assert 0.9 <= total <= 1.1, f"Probabilities sum to {total}, expected ~1.0"
        
        # Verify confidence scores are in valid range if present
        for pred_type, pred_data in predictions.items():
            if isinstance(pred_data, dict) and "model_confidence" in pred_data:
                conf = pred_data["model_confidence"]
                assert 0.0 <= conf <= 1.0, f"Confidence {conf} out of range for {pred_type}"
        
        # Verify market edges have reasonable values
        for edge in model.get("market_edges", []):
            if "edge" in edge:
                # Edge can be positive or negative, but should be reasonable
                assert -1.0 <= edge["edge"] <= 1.0, f"Edge {edge['edge']} out of reasonable range"
            
            if "model_estimated_probability" in edge:
                prob = edge["model_estimated_probability"]
                assert 0.0 <= prob <= 1.0, f"Probability {prob} out of range"
            
            if "implied_probability" in edge:
                prob = edge["implied_probability"]
                assert 0.0 <= prob <= 1.0, f"Implied probability {prob} out of range"
