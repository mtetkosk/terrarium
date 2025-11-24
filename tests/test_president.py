"""Tests for President agent"""

import pytest
from unittest.mock import Mock, patch

from src.agents.president import President


class TestPresidentUnit:
    """Unit tests for President agent (mocked LLM)"""
    
    def test_process_with_mocked_llm(self, mock_database, mock_llm_client, sample_sized_picks):
        """Test president processes picks with mocked LLM"""
        # Setup mock response (new format: approved_picks and daily_report_summary)
        mock_response = {
            "approved_picks": [
                {
                    "game_id": sample_sized_picks[0]["game_id"],
                    "bet_type": sample_sized_picks[0]["bet_type"],
                    "selection": sample_sized_picks[0]["selection"],
                    "odds": sample_sized_picks[0]["odds"],
                    "edge_estimate": sample_sized_picks[0]["edge_estimate"],
                    "units": sample_sized_picks[0]["units"],
                    "best_bet": True,
                    "final_decision_reasoning": "Strong edge and compliance approved"
                }
            ],
            "daily_report_summary": {
                "total_games": 1,
                "total_units": sample_sized_picks[0]["units"],
                "best_bets_count": 1,
                "strategic_notes": ["Card approved, good edge opportunities"]
            }
        }
        mock_llm_client.set_response(mock_response)
        
        # Create president with mocked LLM
        president = President(db=mock_database, llm_client=mock_llm_client)
        result = president.process(sample_sized_picks)
        
        # Verify output structure (new format)
        assert "approved_picks" in result
        assert "daily_report_summary" in result
        
        assert len(result["approved_picks"]) == 1
        assert result["approved_picks"][0]["game_id"] == sample_sized_picks[0]["game_id"]
    
    def test_approval_logic(self, mock_database, mock_llm_client, sample_sized_picks):
        """Test that president approves picks correctly"""
        mock_response = {
            "approved_picks": [
                {
                    "game_id": sample_sized_picks[0]["game_id"],
                    "bet_type": sample_sized_picks[0]["bet_type"],
                    "selection": sample_sized_picks[0]["selection"],
                    "odds": sample_sized_picks[0]["odds"],
                    "edge_estimate": sample_sized_picks[0]["edge_estimate"],
                    "units": sample_sized_picks[0]["units"],
                    "best_bet": False,
                    "final_decision_reasoning": "Approved based on model edge"
                }
            ],
            "daily_report_summary": {
                "total_games": 1,
                "total_units": sample_sized_picks[0]["units"],
                "best_bets_count": 0,
                "strategic_notes": []
            }
        }
        mock_llm_client.set_response(mock_response)
        
        president = President(db=mock_database, llm_client=mock_llm_client)
        result = president.process(sample_sized_picks)
        
        # Verify approval
        assert len(result["approved_picks"]) == 1
        approved = result["approved_picks"][0]
        assert "final_decision_reasoning" in approved or "units" in approved
    
    def test_strategic_notes(self, mock_database, mock_llm_client, sample_sized_picks):
        """Test that president provides strategic notes in daily_report_summary"""
        mock_response = {
            "approved_picks": [
                {
                    "game_id": sample_sized_picks[0]["game_id"],
                    "bet_type": sample_sized_picks[0]["bet_type"],
                    "selection": sample_sized_picks[0]["selection"],
                    "odds": sample_sized_picks[0]["odds"],
                    "edge_estimate": sample_sized_picks[0]["edge_estimate"],
                    "units": sample_sized_picks[0]["units"],
                    "best_bet": True,
                    "final_decision_reasoning": "Good opportunity"
                }
            ],
            "daily_report_summary": {
                "total_games": 1,
                "total_units": sample_sized_picks[0]["units"],
                "best_bets_count": 1,
                "strategic_notes": [
                    "Focusing on high-edge opportunities",
                    "Maintaining conservative exposure"
                ]
            }
        }
        mock_llm_client.set_response(mock_response)
        
        president = President(db=mock_database, llm_client=mock_llm_client)
        result = president.process(sample_sized_picks)
        
        # Verify strategic notes in daily_report_summary
        assert "daily_report_summary" in result
        assert "strategic_notes" in result["daily_report_summary"]
        assert isinstance(result["daily_report_summary"]["strategic_notes"], list)
        assert len(result["daily_report_summary"]["strategic_notes"]) > 0
    
    def test_empty_input(self, mock_database, mock_llm_client):
        """Test president handles empty input"""
        # Setup mock response for empty input
        mock_response = {
            "approved_picks": [],
            "daily_report_summary": {
                "total_games": 0,
                "total_units": 0,
                "best_bets_count": 0,
                "strategic_notes": []
            }
        }
        mock_llm_client.set_response(mock_response)
        
        president = President(db=mock_database, llm_client=mock_llm_client)
        result = president.process([])
        
        assert "approved_picks" in result
        assert "daily_report_summary" in result
        assert isinstance(result["approved_picks"], list)
        assert len(result["approved_picks"]) == 0


@pytest.mark.integration
class TestPresidentIntegration:
    """Integration tests for President agent (real LLM)"""
    
    def test_process_with_real_llm(self, mock_database, real_llm_client, sample_sized_picks):
        """Test president processes picks with real LLM and validates output quality"""
        president = President(db=mock_database, llm_client=real_llm_client)
        
        result = president.process(sample_sized_picks)
        
        # Verify output structure (new format)
        assert "approved_picks" in result, "Missing approved_picks"
        assert "daily_report_summary" in result, "Missing daily_report_summary"
        
        # Verify all are correct types
        assert isinstance(result["approved_picks"], list)
        assert isinstance(result["daily_report_summary"], dict)
        
        # Should have at least one approved pick
        assert len(result["approved_picks"]) >= 1, "Should have at least one approved pick"
        
        # Verify approved picks if any
        for approved in result["approved_picks"]:
            assert "game_id" in approved, "Approved pick missing game_id"
            assert "units" in approved, "Approved pick missing units"
            assert "best_bet" in approved, "Approved pick missing best_bet"
            
            # Verify game_id matches one of the input picks
            input_game_ids = {p["game_id"] for p in sample_sized_picks}
            assert approved["game_id"] in input_game_ids, \
                f"Approved pick game_id {approved['game_id']} not in input picks"
        
        # Verify daily_report_summary
        summary = result["daily_report_summary"]
        assert "total_games" in summary
        assert "total_units" in summary
        assert "best_bets_count" in summary
        assert "strategic_notes" in summary
        
        # Verify strategic notes are strings
        for note in summary["strategic_notes"]:
            assert isinstance(note, str), "Strategic note must be a string"
            assert len(note) > 0, "Strategic note should not be empty"

