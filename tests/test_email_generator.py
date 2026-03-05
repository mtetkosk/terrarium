"""Tests for EmailGenerator (_calculate_bet_profit_loss, _get_team_name)"""

import pytest
from unittest.mock import Mock, MagicMock, patch

from src.data.models import BetResult, BetType
from src.data.storage import PickModel, TeamModel
from src.utils.email.email_generator import EmailGenerator


@pytest.fixture
def email_generator_no_db():
    """EmailGenerator with db=None and LLM client mocked to avoid config/network."""
    with patch("src.utils.email.email_generator.LLMClient") as mock_llm:
        mock_llm.return_value = Mock()
        gen = EmailGenerator(db=None)
    return gen


class TestCalculateBetProfitLoss:
    """Test _calculate_bet_profit_loss with WIN (neg/pos), PUSH, LOSS, and null stakes."""

    def test_win_negative_odds(self, email_generator_no_db):
        pick = Mock(spec=PickModel, stake_units=1.0, stake_amount=2.5, odds=-110)
        profit_units, profit_dollars = email_generator_no_db._calculate_bet_profit_loss(pick, BetResult.WIN)
        expected_units = 100.0 / 110
        expected_dollars = 2.5 * expected_units
        assert abs(profit_units - expected_units) < 1e-6
        assert abs(profit_dollars - expected_dollars) < 1e-6

    def test_win_positive_odds(self, email_generator_no_db):
        pick = Mock(spec=PickModel, stake_units=1.0, stake_amount=10.0, odds=150)
        profit_units, profit_dollars = email_generator_no_db._calculate_bet_profit_loss(pick, BetResult.WIN)
        assert abs(profit_units - 1.5) < 1e-6
        assert abs(profit_dollars - 15.0) < 1e-6

    def test_push_returns_zero(self, email_generator_no_db):
        pick = Mock(spec=PickModel, stake_units=1.0, stake_amount=10.0, odds=-110)
        profit_units, profit_dollars = email_generator_no_db._calculate_bet_profit_loss(pick, BetResult.PUSH)
        assert profit_units == 0.0
        assert profit_dollars == 0.0

    def test_loss_returns_negative_stake(self, email_generator_no_db):
        pick = Mock(spec=PickModel, stake_units=1.0, stake_amount=10.0, odds=-110)
        profit_units, profit_dollars = email_generator_no_db._calculate_bet_profit_loss(pick, BetResult.LOSS)
        assert profit_units == -1.0
        assert profit_dollars == -10.0

    def test_zero_stakes_loss(self, email_generator_no_db):
        """Zero stakes (e.g. null/default) yield zero profit/loss."""
        pick = Mock(spec=PickModel, stake_units=0.0, stake_amount=0.0, odds=-110)
        profit_units, profit_dollars = email_generator_no_db._calculate_bet_profit_loss(pick, BetResult.LOSS)
        assert profit_units == 0.0
        assert profit_dollars == 0.0


class TestGetTeamName:
    """Test _get_team_name with None, valid id, not found, and exception."""

    def test_none_team_id_returns_unknown_team(self, email_generator_no_db):
        assert email_generator_no_db._get_team_name(None, Mock()) == "Unknown Team"

    def test_none_session_returns_unknown_team(self, email_generator_no_db):
        assert email_generator_no_db._get_team_name(1, None) == "Unknown Team"

    def test_valid_team_id_returns_name(self, email_generator_no_db):
        session = Mock()
        team = Mock(normalized_team_name="Duke")
        session.query.return_value.filter_by.return_value.first.return_value = team
        assert email_generator_no_db._get_team_name(1, session) == "Duke"
        session.query.assert_called_once_with(TeamModel)
        session.query.return_value.filter_by.assert_called_once_with(id=1)

    def test_team_id_not_in_db_returns_unknown_team_hash_id(self, email_generator_no_db):
        session = Mock()
        session.query.return_value.filter_by.return_value.first.return_value = None
        assert email_generator_no_db._get_team_name(42, session) == "Unknown Team #42"

    def test_exception_during_query_returns_unknown_team_hash_id(self, email_generator_no_db):
        session = Mock()
        session.query.return_value.filter_by.return_value.first.side_effect = RuntimeError("db error")
        assert email_generator_no_db._get_team_name(7, session) == "Unknown Team #7"
