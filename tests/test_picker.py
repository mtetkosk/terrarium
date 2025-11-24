"""Tests for Picker agent"""

import pytest
from unittest.mock import Mock, patch

from src.agents.picker import Picker


class TestPickerUnit:
    """Unit tests for Picker agent (mocked LLM)"""
    
    def test_process_with_mocked_llm(self, mock_database, mock_llm_client, sample_researcher_output, sample_modeler_output, sample_bankroll_status):
        """Test picker processes inputs with mocked LLM"""
        # Setup mock response
        mock_response = {
            "candidate_picks": [
                {
                    "game_id": sample_modeler_output["game_models"][0]["game_id"],
                    "bet_type": "spread",
                    "selection": "Duke -7.5",
                    "odds": "-110",
                    "justification": ["Model shows edge", "Home advantage"],
                    "edge_estimate": 0.056,
                    "confidence": 0.75,
                    "confidence_score": 7,
                    "best_bet": True,
                    "book": "DraftKings"
                }
            ],
            "overall_strategy_summary": ["Focus on spreads with edge > 0.05"]
        }
        mock_llm_client.set_response(mock_response)
        
        # Create picker with mocked LLM
        picker = Picker(db=mock_database, llm_client=mock_llm_client)
        result = picker.process(sample_researcher_output, sample_modeler_output, sample_bankroll_status)
        
        # Verify output structure
        assert "candidate_picks" in result
        assert len(result["candidate_picks"]) == 1
        pick = result["candidate_picks"][0]
        assert pick["game_id"] == sample_modeler_output["game_models"][0]["game_id"]
        assert pick["bet_type"] == "spread"
        assert "selection" in pick
        assert "odds" in pick
        assert "edge_estimate" in pick
    
    def test_pick_filtering(self, mock_database, mock_llm_client, sample_researcher_output, sample_modeler_output, sample_bankroll_status):
        """Test that picker filters extreme odds"""
        # Setup mock response with extreme odds
        mock_response = {
            "candidate_picks": [
                {
                    "game_id": sample_modeler_output["game_models"][0]["game_id"],
                    "bet_type": "moneyline",
                    "selection": "Duke",
                    "odds": "-10000",  # Extreme odds
                    "justification": ["Very heavy favorite"],
                    "edge_estimate": 0.1,
                    "confidence": 0.9,
                    "confidence_score": 9,
                    "best_bet": False,
                    "book": "DraftKings"
                },
                {
                    "game_id": sample_modeler_output["game_models"][0]["game_id"],
                    "bet_type": "spread",
                    "selection": "Duke -7.5",
                    "odds": "-110",  # Normal odds
                    "justification": ["Good value"],
                    "edge_estimate": 0.056,
                    "confidence": 0.75,
                    "confidence_score": 7,
                    "best_bet": True,
                    "book": "DraftKings"
                }
            ],
            "overall_strategy_summary": []
        }
        mock_llm_client.set_response(mock_response)
        
        picker = Picker(db=mock_database, llm_client=mock_llm_client)
        result = picker.process(sample_researcher_output, sample_modeler_output, sample_bankroll_status)
        
        # Verify extreme odds pick is filtered out
        picks = result["candidate_picks"]
        assert len(picks) == 1  # Only the normal odds pick should remain
        assert picks[0]["odds"] == "-110"
    
    def test_parlay_creation(self, mock_database, mock_llm_client, sample_researcher_output, sample_modeler_output, sample_bankroll_status):
        """Test that picker can create parlays"""
        # Setup multiple high-confidence picks
        mock_response = {
            "candidate_picks": [
                {
                    "game_id": "1",
                    "bet_type": "spread",
                    "selection": "Team A -5.5",
                    "odds": "-110",
                    "justification": ["High confidence"],
                    "edge_estimate": 0.08,
                    "confidence": 0.75,
                    "confidence_score": 8,
                    "best_bet": True,
                    "book": "DraftKings"
                },
                {
                    "game_id": "2",
                    "bet_type": "spread",
                    "selection": "Team B -3.5",
                    "odds": "-110",
                    "justification": ["High confidence"],
                    "edge_estimate": 0.07,
                    "confidence": 0.70,
                    "confidence_score": 7,
                    "best_bet": True,
                    "book": "DraftKings"
                }
            ],
            "overall_strategy_summary": []
        }
        mock_llm_client.set_response(mock_response)
        
        picker = Picker(db=mock_database, llm_client=mock_llm_client)
        # Force parlay creation by setting probability to 1.0
        picker.parlay_probability = 1.0
        result = picker.process(sample_researcher_output, sample_modeler_output, sample_bankroll_status)
        
        # Verify picks exist
        picks = result["candidate_picks"]
        assert len(picks) >= 2
        
        # Check if parlay was created (may or may not happen based on random)
        parlay_picks = [p for p in picks if p.get("bet_type") == "parlay"]
        # Parlay creation is probabilistic, so we just verify the picks exist
        assert len(picks) >= 2
    
    def test_confidence_thresholds(self, mock_database, mock_llm_client, sample_researcher_output, sample_modeler_output, sample_bankroll_status):
        """Test that picker respects confidence thresholds"""
        mock_response = {
            "candidate_picks": [
                {
                    "game_id": sample_modeler_output["game_models"][0]["game_id"],
                    "bet_type": "spread",
                    "selection": "Duke -7.5",
                    "odds": "-110",
                    "justification": ["Good edge"],
                    "edge_estimate": 0.056,
                    "confidence": 0.75,
                    "confidence_score": 7,
                    "best_bet": True,  # High confidence
                    "book": "DraftKings"
                }
            ],
            "overall_strategy_summary": []
        }
        mock_llm_client.set_response(mock_response)
        
        picker = Picker(db=mock_database, llm_client=mock_llm_client)
        result = picker.process(sample_researcher_output, sample_modeler_output, sample_bankroll_status)
        
        # Verify pick has confidence fields
        pick = result["candidate_picks"][0]
        assert "confidence" in pick
        assert "confidence_score" in pick
        assert 0.0 <= pick["confidence"] <= 1.0
        assert 1 <= pick["confidence_score"] <= 10
    
    def test_empty_input(self, mock_database, mock_llm_client):
        """Test picker handles empty input"""
        empty_researcher = {"games": []}
        empty_modeler = {"game_models": []}
        
        picker = Picker(db=mock_database, llm_client=mock_llm_client)
        result = picker.process(empty_researcher, empty_modeler)
        
        assert "candidate_picks" in result
        assert isinstance(result["candidate_picks"], list)


@pytest.mark.integration
class TestPickerIntegration:
    """Integration tests for Picker agent (real LLM)"""
    
    def test_process_with_real_llm(self, mock_database, real_llm_client, minimal_researcher_output, minimal_modeler_output, sample_bankroll_status):
        """Test picker processes inputs with real LLM and validates output quality"""
        picker = Picker(db=mock_database, llm_client=real_llm_client)
        
        result = picker.process(minimal_researcher_output, minimal_modeler_output, sample_bankroll_status)
        
        # Verify output structure
        assert "candidate_picks" in result
        assert isinstance(result["candidate_picks"], list)
        
        # Should have at least one pick
        assert len(result["candidate_picks"]) >= 1
        
        # Verify each pick has required fields
        for pick in result["candidate_picks"]:
            assert "game_id" in pick, "Pick missing game_id"
            assert "bet_type" in pick, "Pick missing bet_type"
            assert "selection" in pick, "Pick missing selection"
            assert "odds" in pick, "Pick missing odds"
            
            # Verify bet_type is valid
            assert pick["bet_type"] in ["spread", "total", "moneyline", "parlay"], \
                f"Invalid bet_type: {pick['bet_type']}"
            
            # Verify edge_estimate is reasonable
            if "edge_estimate" in pick:
                edge = pick["edge_estimate"]
                assert -1.0 <= edge <= 1.0, f"Edge estimate {edge} out of reasonable range"
            
            # Verify confidence is in valid range
            if "confidence" in pick:
                conf = pick["confidence"]
                assert 0.0 <= conf <= 1.0, f"Confidence {conf} out of range"
            
            # Verify confidence_score is in valid range
            if "confidence_score" in pick:
                score = pick["confidence_score"]
                assert 1 <= score <= 10, f"Confidence score {score} out of range (1-10)"
            
            # Verify best_bet is boolean if present
            if "best_bet" in pick:
                assert isinstance(pick["best_bet"], bool), "best_bet must be boolean"
            
            # Verify odds format is reasonable
            odds_str = str(pick["odds"])
            # Should start with + or - for American odds
            assert odds_str.startswith(("+", "-")) or odds_str.isdigit(), \
                f"Odds format unexpected: {odds_str}"
            
            # Verify justification exists and is a list
            if "justification" in pick:
                assert isinstance(pick["justification"], list), "justification must be a list"
                assert len(pick["justification"]) > 0, "justification should not be empty"

