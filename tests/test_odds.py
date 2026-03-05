"""Tests for American odds utility"""

import pytest

from src.utils.odds import american_odds_to_profit_multiplier


class TestAmericanOddsToProfitMultiplier:
    """Test american_odds_to_profit_multiplier with realistic and edge inputs."""

    def test_zero_returns_zero(self):
        assert american_odds_to_profit_multiplier(0) == 0.0

    def test_negative_standard_minus_110(self):
        expected = 100.0 / 110
        actual = american_odds_to_profit_multiplier(-110)
        assert abs(actual - expected) < 1e-6

    def test_negative_200(self):
        assert abs(american_odds_to_profit_multiplier(-200) - 0.5) < 1e-6

    def test_positive_150(self):
        assert abs(american_odds_to_profit_multiplier(150) - 1.5) < 1e-6

    def test_positive_100(self):
        assert abs(american_odds_to_profit_multiplier(100) - 1.0) < 1e-6

    def test_other_negative_odds(self):
        assert abs(american_odds_to_profit_multiplier(-300) - (100.0 / 300)) < 1e-6

    def test_other_positive_odds(self):
        assert abs(american_odds_to_profit_multiplier(200) - 2.0) < 1e-6
