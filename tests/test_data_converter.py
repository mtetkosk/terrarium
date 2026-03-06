"""Tests for DataConverter (parse_odds, picks_from_json)"""

import pytest
from datetime import date

from src.data.models import Game, BetType
from src.orchestration.data_converter import DataConverter


class TestParseOdds:
    """Test DataConverter.parse_odds with valid, null, and unparseable inputs."""

    def test_valid_negative(self):
        assert DataConverter.parse_odds("-110") == -110
        assert DataConverter.parse_odds("-200") == -200

    def test_valid_positive(self):
        assert DataConverter.parse_odds("+150") == 150
        assert DataConverter.parse_odds("+100") == 100

    def test_empty_returns_none(self):
        assert DataConverter.parse_odds("") is None

    def test_missing_unavailable_return_none(self):
        assert DataConverter.parse_odds("n/a") is None
        assert DataConverter.parse_odds("N/A") is None
        assert DataConverter.parse_odds("market_unavailable") is None
        assert DataConverter.parse_odds("unavailable") is None
        assert DataConverter.parse_odds("none") is None
        assert DataConverter.parse_odds("na") is None

    def test_unparseable_return_none(self):
        assert DataConverter.parse_odds("abc") is None
        assert DataConverter.parse_odds("12.5") is None

    def test_whitespace_stripped_valid(self):
        assert DataConverter.parse_odds("  -110  ") == -110
        assert DataConverter.parse_odds(" +150 ") == 150


class TestPicksFromJson:
    """Test DataConverter.picks_from_json; skip when odds is None, include when valid."""

    @pytest.fixture
    def minimal_game(self):
        return Game(team1="Team A", team2="Team B", date=date.today(), id=1)

    def test_skip_when_odds_none(self, minimal_game):
        candidate_picks = [
            {
                "game_id": "1",
                "bet_type": "spread",
                "selection": "Team A -5.5",
                "odds": "",
                "justification": ["Good edge"],
                "line": -5.5,
            }
        ]
        picks = DataConverter.picks_from_json(candidate_picks, [minimal_game])
        assert picks == []

    def test_skip_when_odds_na(self, minimal_game):
        candidate_picks = [
            {
                "game_id": "1",
                "bet_type": "spread",
                "selection": "Team A -5.5",
                "odds": "n/a",
                "justification": ["Good edge"],
                "line": -5.5,
            }
        ]
        picks = DataConverter.picks_from_json(candidate_picks, [minimal_game])
        assert picks == []

    def test_include_when_odds_valid(self, minimal_game):
        candidate_picks = [
            {
                "game_id": "1",
                "bet_type": "spread",
                "selection": "Team A -5.5",
                "odds": "-110",
                "justification": ["Good edge"],
                "line": -5.5,
            }
        ]
        picks = DataConverter.picks_from_json(candidate_picks, [minimal_game])
        assert len(picks) == 1
        assert picks[0].odds == -110
        assert picks[0].game_id == 1

    def test_mixed_valid_and_invalid_odds(self, minimal_game):
        candidate_picks = [
            {
                "game_id": "1",
                "bet_type": "spread",
                "selection": "Team A -5.5",
                "odds": "-110",
                "justification": ["First pick"],
                "line": -5.5,
            },
            {
                "game_id": "1",
                "bet_type": "total",
                "selection": "Over 150.5",
                "odds": "invalid",
                "justification": ["Second pick"],
                "line": 150.5,
            },
        ]
        picks = DataConverter.picks_from_json(candidate_picks, [minimal_game])
        assert len(picks) == 1
        assert picks[0].odds == -110
        assert picks[0].rationale == "First pick"

    def test_parse_single_pick_returns_pick_when_valid(self, minimal_game):
        """_parse_single_pick returns a Pick when game_id, odds, and rationale are valid."""
        game_map = {minimal_game.id: minimal_game}
        pick_data = {
            "game_id": "1",
            "bet_type": "spread",
            "selection": "Team A -5.5",
            "odds": "-110",
            "justification": ["Good edge"],
            "line": -5.5,
        }
        pick = DataConverter._parse_single_pick(pick_data, [minimal_game], game_map)
        assert pick is not None
        assert pick.game_id == 1
        assert pick.odds == -110
        assert pick.bet_type == BetType.SPREAD
        assert pick.rationale == "Good edge"

    def test_parse_single_pick_returns_none_when_odds_unavailable(self, minimal_game):
        """_parse_single_pick returns None when odds are missing/unparseable (pick skipped)."""
        game_map = {minimal_game.id: minimal_game}
        pick_data = {
            "game_id": "1",
            "bet_type": "spread",
            "selection": "Team A -5.5",
            "odds": "n/a",
            "justification": ["Good edge"],
            "line": -5.5,
        }
        pick = DataConverter._parse_single_pick(pick_data, [minimal_game], game_map)
        assert pick is None

    def test_missing_rationale_skips_pick(self, minimal_game):
        """Empty justification and notes cause rationale validation to fail; pick is skipped (exception caught)."""
        candidate_picks = [
            {
                "game_id": "1",
                "bet_type": "spread",
                "selection": "Team A -5.5",
                "odds": "-110",
                "justification": [],
                "notes": "",
                "line": -5.5,
            }
        ]
        picks = DataConverter.picks_from_json(candidate_picks, [minimal_game])
        assert picks == []
