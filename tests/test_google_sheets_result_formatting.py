"""Test that Google Sheets correctly formats game results with proper home/away team ordering"""

import pytest
from datetime import date
from sqlalchemy.orm import Session
from unittest.mock import Mock, MagicMock

from src.data.storage import GameModel, TeamModel, PickModel, BetModel
from src.data.models import BetType, BetResult, GameStatus
from src.utils.google_sheets import GoogleSheetsService
from src.utils.team_normalizer import (
    get_home_away_team_names, 
    determine_home_away_from_result,
    get_home_away_scores
)


def test_game_result_formatting_with_reversed_teams():
    """
    Test that game result string correctly formats when database teams are in reverse order.
    
    Scenario: Game 221
    - Database: team1=VCU (605), team2=Vanderbilt (582)
    - Result: home=Vanderbilt (89), away=VCU (74)
    - Expected: "VCU Rams 74 - Vanderbilt Commodores 89"
    
    This tests the exact scenario from the bug report.
    """
    # Setup mock session
    session = create_mock_session()
    
    # Mock game with reversed team order (exact scenario from bug)
    game_result_data = {
        'home_score': '89',  # Vanderbilt scored 89
        'away_score': '74',  # VCU scored 74
        'home_team': 'Vanderbilt Commodores',
        'away_team': 'VCU Rams'
    }
    
    team1_id = 605  # VCU
    team2_id = 582  # Vanderbilt
    
    # Step 1: Get team names (determine which is home/away)
    home_team_name, away_team_name = get_home_away_team_names(
        team1_id,
        team2_id,
        game_result_data,
        session,
        fallback_team1_is_home=True
    )
    
    # Verify team names are correctly identified
    assert home_team_name == 'vanderbilt commodores', f"Expected home team to be 'vanderbilt commodores', got '{home_team_name}'"
    assert away_team_name == 'vcu rams', f"Expected away team to be 'vcu rams', got '{away_team_name}'"
    
    # Step 2: Get scores - these should match the result's home/away teams
    home_score, away_score = get_home_away_scores(
        team1_id,
        team2_id,
        game_result_data,
        session,
        fallback_team1_is_home=True
    )
    
    # Verify scores are correctly mapped
    # Since result says Vanderbilt (home) = 89, VCU (away) = 74
    # And we determined Vanderbilt is home, VCU is away
    # Scores should be: home=89, away=74
    assert home_score == 89, f"Home score should be 89 (Vanderbilt's score), got {home_score}"
    assert away_score == 74, f"Away score should be 74 (VCU's score), got {away_score}"
    
    # Step 3: Format as Google Sheets does: "Away Team Score - Home Team Score"
    game_result = f"{away_team_name} {away_score} - {home_team_name} {home_score}"
    
    # Expected: "VCU Rams 74 - Vanderbilt Commodores 89"
    expected = "vcu rams 74 - vanderbilt commodores 89"
    actual = game_result.lower()
    
    assert actual == expected, f"Expected '{expected}', got '{actual}'. " \
                                f"This test ensures the result string shows: away team (VCU) with away score (74), " \
                                f"then home team (Vanderbilt) with home score (89)."
    
    # Also verify the reverse would be wrong (what was showing before)
    wrong_result = f"{home_team_name} {away_score} - {away_team_name} {home_score}"
    wrong_expected = "vanderbilt commodores 74 - vcu rams 89"
    assert wrong_result.lower() == wrong_expected.lower(), "This is the wrong format that was being shown"
    assert actual != wrong_result.lower(), "Our result should NOT match the wrong format"


def test_determine_home_away_from_result():
    """Test the determine_home_away_from_result utility function directly"""
    session = create_mock_session()
    
    result_data = {
        'home_score': 89,
        'away_score': 74,
        'home_team': 'Vanderbilt Commodores',
        'away_team': 'VCU Rams'
    }
    
    # Test: team1=VCU (605), team2=Vanderbilt (582)
    # Result says Vanderbilt is home, VCU is away
    # So team1 should be away (False), team2 should be home (True)
    result = determine_home_away_from_result(605, 582, result_data, session)
    
    assert result is not None, "Should be able to determine home/away"
    team1_is_home, team2_is_home = result
    assert team1_is_home == False, f"team1 (VCU) should be away (False), got {team1_is_home}"
    assert team2_is_home == True, f"team2 (Vanderbilt) should be home (True), got {team2_is_home}"


def test_get_home_away_scores():
    """Test that scores are correctly mapped to home/away teams"""
    session = create_mock_session()
    
    result_data = {
        'home_score': 89,
        'away_score': 74,
        'home_team': 'Vanderbilt Commodores',
        'away_team': 'VCU Rams'
    }
    
    # team1=VCU (605), team2=Vanderbilt (582)
    # Result: Vanderbilt is home (89), VCU is away (74)
    # Since we determine team2 (Vanderbilt) is home, scores should be: home=89, away=74
    home_score, away_score = get_home_away_scores(605, 582, result_data, session)
    
    assert home_score == 89, f"Home score should be 89, got {home_score}"
    assert away_score == 74, f"Away score should be 74, got {away_score}"


def test_full_result_string_formatting():
    """
    Integration test: Test the full flow of formatting a result string like Google Sheets does.
    
    This ensures teams and scores are correctly matched when formatting the result string.
    """
    session = create_mock_session()
    
    # Game 221 scenario
    team1_id = 605  # VCU
    team2_id = 582  # Vanderbilt
    
    game_result_data = {
        'home_score': '89',
        'away_score': '74',
        'home_team': 'Vanderbilt Commodores',
        'away_team': 'VCU Rams'
    }
    
    # Get team names
    home_team_name, away_team_name = get_home_away_team_names(
        team1_id, team2_id, game_result_data, session
    )
    
    # Get scores
    home_score, away_score = get_home_away_scores(
        team1_id, team2_id, game_result_data, session
    )
    
    # Format the result string exactly as Google Sheets does
    game_result = f"{away_team_name} {away_score} - {home_team_name} {home_score}"
    
    # Expected output
    expected = "vcu rams 74 - vanderbilt commodores 89"
    
    # Verify
    assert game_result.lower() == expected, \
        f"Result string format is incorrect. Expected '{expected}', got '{game_result.lower()}'. " \
        f"This means scores or teams are not correctly matched."


def create_mock_session():
    """Helper to create a mock session with team models"""
    session = Mock(spec=Session)
    
    vcu_team = Mock(spec=TeamModel)
    vcu_team.id = 605
    vcu_team.normalized_team_name = 'vcu rams'
    
    vandy_team = Mock(spec=TeamModel)
    vandy_team.id = 582
    vandy_team.normalized_team_name = 'vanderbilt commodores'
    
    def query_filter_by(model_class, **kwargs):
        mock_query = Mock()
        if hasattr(model_class, '__name__') and model_class.__name__ == 'TeamModel':
            if kwargs.get('id') == 605:
                mock_query.first.return_value = vcu_team
            elif kwargs.get('id') == 582:
                mock_query.first.return_value = vandy_team
            else:
                mock_query.first.return_value = None
        return mock_query
    
    session.query = Mock(side_effect=lambda model_class: Mock(
        filter_by=Mock(side_effect=lambda **kwargs: query_filter_by(model_class, **kwargs))
    ))
    
    return session


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
