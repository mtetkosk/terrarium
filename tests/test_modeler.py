"""Tests for Modeler agent"""

import pytest
from unittest.mock import Mock, patch
from datetime import date

from src.agents.modeler import Modeler
from src.data.models import BettingLine, BetType


class TestModelerUnit:
    """Unit tests for Modeler agent (mocked LLM)"""
    
    def test_process_with_mocked_llm(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test modeler processes researcher output with mocked LLM"""
        # Setup mock response
        mock_response = {
            "game_models": [
                {
                    "game_id": sample_researcher_output["games"][0]["game_id"],
                    "league": "NCAA",
                    "predictions": {
                        "spread": {
                            "projected_line": "Duke -8.0",
                            "projected_margin": -8.0,
                            "model_confidence": 0.75
                        },
                        "total": {
                            "projected_total": 152.0,
                            "model_confidence": 0.65
                        },
                        "moneyline": {
                            "team_probabilities": {
                                "away": 0.25,
                                "home": 0.75
                            },
                            "model_confidence": 0.70
                        }
                    },
                    "predicted_score": {
                        "away_score": 72.0,
                        "home_score": 80.0
                    },
                    "market_edges": [
                        {
                            "market_type": "spread",
                            "market_line": "-7.5",
                            "model_estimated_probability": 0.58,
                            "implied_probability": 0.524,
                            "edge": 0.056,
                            "edge_confidence": 0.75
                        }
                    ],
                    "model_notes": "Strong model confidence"
                }
            ]
        }
        mock_llm_client.set_response(mock_response)
        
        # Create modeler with mocked LLM
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(sample_researcher_output, betting_lines=sample_betting_lines)
        
        # Verify output structure
        assert "game_models" in result
        assert len(result["game_models"]) == 1
        assert result["game_models"][0]["game_id"] == sample_researcher_output["games"][0]["game_id"]
        assert "predictions" in result["game_models"][0]
        assert "market_edges" in result["game_models"][0]
        # Verify predicted_score is present (new field)
        assert "predicted_score" in result["game_models"][0]
        predicted_score = result["game_models"][0]["predicted_score"]
        assert "away_score" in predicted_score
        assert "home_score" in predicted_score
    
    def test_batch_processing(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test that modeler processes games in batches"""
        # Duplicate game for batch testing
        games_output = {
            "games": sample_researcher_output["games"] * 2
        }
        # Update game_id for second game
        games_output["games"][1]["game_id"] = "2"
        
        mock_response = {
            "game_models": [
                {
                    "game_id": game["game_id"],
                    "league": "NCAA",
                    "predictions": {
                        "spread": {
                            "projected_line": "Team -5.0",
                            "projected_margin": -5.0,
                            "model_confidence": 0.6
                        }
                    },
                    "predicted_score": {
                        "away_score": 70.0,
                        "home_score": 75.0
                    },
                    "market_edges": [],
                    "model_notes": "Test model"
                }
                for game in games_output["games"]
            ]
        }
        mock_llm_client.set_response(mock_response)
        
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(games_output, betting_lines=sample_betting_lines)
        
        # Verify all games are processed
        assert len(result["game_models"]) == len(games_output["games"])
    
    def test_fallback_handling(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test that modeler creates fallback entries for failed batches"""
        # Setup mock to return empty response (simulating failure)
        mock_llm_client.set_response({"game_models": []})
        
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        # Pass force_refresh=True to bypass cache during tests
        result = modeler.process(sample_researcher_output, betting_lines=sample_betting_lines, force_refresh=True)
        
        # Verify fallback entry is created
        assert len(result["game_models"]) == 1
        assert result["game_models"][0]["game_id"] == sample_researcher_output["games"][0]["game_id"]
        assert "CRITICAL: Model data unavailable" in result["game_models"][0].get("model_notes", "")
    
    def test_empty_input(self, mock_database, mock_llm_client):
        """Test modeler handles empty input"""
        empty_output = {"games": []}
        
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(empty_output)
        
        assert result == {"game_models": []}
    
    def test_probability_validation(self, mock_database, mock_llm_client, sample_researcher_output, sample_betting_lines):
        """Test that modeler validates probability ranges"""
        mock_response = {
            "game_models": [
                {
                    "game_id": sample_researcher_output["games"][0]["game_id"],
                    "league": "NCAA",
                    "predictions": {
                        "moneyline": {
                            "team_probabilities": {
                                "away": 0.3,
                                "home": 0.7
                            },
                            "model_confidence": 0.70
                        }
                    },
                    "market_edges": [],
                    "model_notes": "Test"
                }
            ]
        }
        mock_llm_client.set_response(mock_response)
        
        modeler = Modeler(db=mock_database, llm_client=mock_llm_client)
        result = modeler.process(sample_researcher_output, betting_lines=sample_betting_lines)
        
        # Verify probabilities sum to approximately 1.0
        probs = result["game_models"][0]["predictions"]["moneyline"]["team_probabilities"]
        total = probs["away"] + probs["home"]
        assert 0.9 <= total <= 1.1  # Allow small floating point differences


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

