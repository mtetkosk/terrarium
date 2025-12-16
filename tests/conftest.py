"""Pytest configuration and shared fixtures"""

import pytest
import json
import os
from datetime import date, datetime
from typing import Dict, Any, Optional, List
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

from src.data.models import Game, BettingLine, BetType, GameStatus
from src.data.storage import Database, TeamModel
from src.utils.llm import LLMClient
from src.utils.team_normalizer import normalize_team_name_for_lookup


class MockLLMClient:
    """Mock LLM client that returns predefined responses for unit tests"""
    
    def __init__(self, model: str = "mock-model"):
        self.model = model
        self.total_tokens_used = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._responses = {}
        self._default_response = None
    
    def set_response(self, response: Dict[str, Any], key: Optional[str] = None):
        """Set a response for a specific key or as default"""
        if key:
            self._responses[key] = response
        else:
            self._default_response = response
    
    def get_response(self, key: Optional[str] = None) -> Dict[str, Any]:
        """Get response by key or return default"""
        if key and key in self._responses:
            return self._responses[key]
        if self._default_response:
            return self._default_response
        return {"error": "No response configured"}
    
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None
    ) -> Dict[str, Any]:
        """Mock LLM call"""
        # Track token usage (simulated)
        prompt_length = len(system_prompt) + len(user_prompt)
        self.total_prompt_tokens += prompt_length // 4  # Rough estimate
        self.total_completion_tokens += 500  # Simulated completion
        self.total_tokens_used += self.total_prompt_tokens + self.total_completion_tokens
        
        # Get response based on key (could be based on user_prompt hash or explicit key)
        response = self.get_response()
        
        # If tools are provided and LLM should call them, simulate tool calling
        if tools and response.get("tool_calls"):
            return response
        
        # Parse JSON if needed
        if parse_json and isinstance(response, dict):
            return response
        
        return {"raw_response": json.dumps(response) if isinstance(response, dict) else str(response)}

    def call_chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        parse_json: bool = True,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Mock LLM chat call"""
        # Calculate prompt length from messages
        prompt_length = sum(len(str(m.get("content", ""))) for m in messages)
        self.total_prompt_tokens += prompt_length // 4
        self.total_completion_tokens += 500
        self.total_tokens_used += self.total_prompt_tokens + self.total_completion_tokens
        
        response = self.get_response()
        
        # Handle tool calls
        if tools and response.get("tool_calls"):
            return response
            
        if parse_json and isinstance(response, dict):
            return response
            
        return {"raw_response": json.dumps(response) if isinstance(response, dict) else str(response)}
    
    def get_usage_stats(self) -> Dict[str, int]:
        """Get token usage statistics"""
        return {
            "total_tokens": self.total_tokens_used,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens
        }
    
    def reset_usage_stats(self) -> None:
        """Reset token usage statistics"""
        self.total_tokens_used = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0


@pytest.fixture
def mock_llm_client():
    """Fixture providing a mock LLM client for unit tests"""
    return MockLLMClient()


@pytest.fixture
def real_llm_client():
    """Fixture providing a real LLM client for integration tests"""
    # Use cheapest model for integration tests to minimize token cost
    try:
        # Check if API key is present
        if not os.getenv("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY not found. Skipping integration test.")
            
        client = LLMClient(model="gemini-3-flash")
        yield client
    except Exception as e:
        pytest.skip(f"Could not initialize LLM client: {e}. Skipping integration test.")


@pytest.fixture
def mock_database(tmp_path):
    """Fixture providing a mock/in-memory database for tests"""
    db_path = tmp_path / "test_terrarium.db"
    # Database expects a database_url in SQLite format
    database_url = f"sqlite:///{db_path}"
    db = Database(database_url=database_url)
    
    # Create tables
    from src.data.storage import Base
    Base.metadata.create_all(db.engine)
    
    yield db
    
    # Cleanup
    db.close()
    db.engine.dispose()
    if db_path.exists():
        db_path.unlink()


def get_or_create_team(session, team_name: str) -> int:
    """Helper function to get or create a team and return its ID"""
    normalized_name = normalize_team_name_for_lookup(team_name)
    team = session.query(TeamModel).filter_by(normalized_team_name=normalized_name).first()
    if not team:
        team = TeamModel(normalized_team_name=normalized_name)
        session.add(team)
        session.flush()
    return team.id


@pytest.fixture
def sample_game():
    """Fixture providing a sample Game object"""
    return Game(
        id=1,
        team1="Duke",
        team2="North Carolina",
        date=date.today(),
        venue="Cameron Indoor Stadium",
        status=GameStatus.SCHEDULED
    )


@pytest.fixture
def sample_games():
    """Fixture providing a list of sample Game objects"""
    return [
        Game(
            id=1,
            team1="Duke",
            team2="North Carolina",
            date=date.today(),
            venue="Cameron Indoor Stadium",
            status=GameStatus.SCHEDULED
        ),
        Game(
            id=2,
            team1="Kentucky",
            team2="Louisville",
            date=date.today(),
            venue="Rupp Arena",
            status=GameStatus.SCHEDULED
        )
    ]


@pytest.fixture
def minimal_game():
    """Fixture providing a minimal Game object for integration tests (token optimization)"""
    return Game(
        id=1,
        team1="Team A",
        team2="Team B",
        date=date.today(),
        status=GameStatus.SCHEDULED
    )


@pytest.fixture
def sample_betting_lines(sample_game):
    """Fixture providing sample BettingLine objects"""
    return [
        BettingLine(
            game_id=sample_game.id,
            book="DraftKings",
            bet_type=BetType.SPREAD,
            line=-7.5,
            odds=-110,
            team="Duke"
        ),
        BettingLine(
            game_id=sample_game.id,
            book="DraftKings",
            bet_type=BetType.TOTAL,
            line=150.5,
            odds=-110,
            team="over"
        ),
        BettingLine(
            game_id=sample_game.id,
            book="DraftKings",
            bet_type=BetType.MONEYLINE,
            line=0,
            odds=-350,
            team="Duke"
        )
    ]


@pytest.fixture
def minimal_betting_lines(minimal_game):
    """Fixture providing minimal BettingLine objects for integration tests"""
    return [
        BettingLine(
            game_id=minimal_game.id,
            book="DraftKings",
            bet_type=BetType.SPREAD,
            line=-5.0,
            odds=-110,
            team="Team A"
        )
    ]


@pytest.fixture
def sample_researcher_output(sample_game):
    """Fixture providing sample researcher output"""
    return {
        "games": [
            {
                "game_id": str(sample_game.id),
                "league": "NCAA",
                "teams": {
                    "away": sample_game.team2,
                    "home": sample_game.team1
                },
                "start_time": sample_game.date.isoformat(),
                "market": {
                    "spread": f"{sample_game.team1} -7.5",
                    "total": 150.5,
                    "moneyline": {
                        "home": "-350",
                        "away": "+280"
                    }
                },
                "advanced_stats": {
                    "team1": {
                        "adj_o": 115.2,
                        "adj_d": 95.3,
                        "adj_t": 68.5
                    },
                    "team2": {
                        "adj_o": 108.5,
                        "adj_d": 98.2,
                        "adj_t": 65.2
                    }
                },
                "key_injuries": [],
                "recent_form_summary": "Both teams playing well recently",
                "expert_predictions_summary": "Duke favored by 7-8 points",
                "common_opponents_analysis": [],
                "notable_context": ["Rivalry game"],
                "data_quality_notes": ""
            }
        ]
    }


@pytest.fixture
def minimal_researcher_output(minimal_game):
    """Fixture providing minimal researcher output for integration tests"""
    return {
        "games": [
            {
                "game_id": str(minimal_game.id),
                "league": "NCAA",
                "teams": {
                    "away": minimal_game.team2,
                    "home": minimal_game.team1
                },
                "start_time": minimal_game.date.isoformat(),
                "market": {
                    "spread": f"{minimal_game.team1} -5.0"
                },
                "advanced_stats": {},
                "key_injuries": [],
                "recent_form_summary": "Test game",
                "expert_predictions_summary": "",
                "common_opponents_analysis": [],
                "notable_context": [],
                "data_quality_notes": ""
            }
        ]
    }


@pytest.fixture
def sample_modeler_output(sample_game):
    """Fixture providing sample modeler output"""
    return {
        "game_models": [
            {
                "game_id": str(sample_game.id),
                "league": "NCAA",
                "predictions": {
                    "spread": {
                        "projected_line": f"{sample_game.team1} -8.0",
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
                "model_notes": "Strong home court advantage"
            }
        ]
    }


@pytest.fixture
def minimal_modeler_output(minimal_game):
    """Fixture providing minimal modeler output for integration tests"""
    return {
        "game_models": [
            {
                "game_id": str(minimal_game.id),
                "league": "NCAA",
                "predictions": {
                    "spread": {
                        "projected_line": "Team A -5.5",
                        "projected_margin": -5.5,
                        "model_confidence": 0.6
                    }
                },
                "market_edges": [
                    {
                        "market_type": "spread",
                        "market_line": "-5.0",
                        "model_estimated_probability": 0.55,
                        "implied_probability": 0.524,
                        "edge": 0.026,
                        "edge_confidence": 0.6
                    }
                ],
                "model_notes": "Test model output"
            }
        ]
    }


@pytest.fixture
def sample_picks(sample_game):
    """Fixture providing sample picks"""
    return [
        {
            "game_id": str(sample_game.id),
            "bet_type": "spread",
            "selection": f"{sample_game.team1} -7.5",
            "odds": "-110",
            "justification": ["Model shows edge", "Home court advantage"],
            "edge_estimate": 0.056,
            "confidence": 0.75,
            "confidence_score": 7,
            "best_bet": True,
            "book": "DraftKings"
        }
    ]


@pytest.fixture
def sample_sized_picks(sample_game):
    """Fixture providing sample sized picks from Banker"""
    return [
        {
            "game_id": str(sample_game.id),
            "bet_type": "spread",
            "selection": f"{sample_game.team1} -7.5",
            "odds": "-110",
            "edge_estimate": 0.056,
            "confidence": 0.75,
            "units": 2.5,
            "stake_rationale": ["Kelly fraction applied", "Moderate confidence"],
            "risk_flags": [],
            "book": "DraftKings"
        }
    ]


@pytest.fixture
def sample_bankroll_status():
    """Fixture providing sample bankroll status"""
    return {
        "current_bankroll": 1000.0,
        "initial_bankroll": 1000.0,
        "total_wagered": 0.0,
        "total_profit": 0.0,
        "active_bets": 0
    }


@pytest.fixture
def mock_web_browser():
    """Fixture providing a mocked web browser for Researcher tests"""
    mock_browser = Mock()
    mock_browser.search_web = Mock(return_value={"results": []})
    mock_browser.search_injury_reports = Mock(return_value={"injuries": []})
    mock_browser.search_team_stats = Mock(return_value={"stats": {}})
    mock_browser.search_advanced_stats = Mock(return_value={"stats": {}})
    mock_browser.fetch_url = Mock(return_value={"content": ""})
    mock_browser.search_game_predictions = Mock(return_value={"predictions": []})
    return mock_browser


# Pytest markers
def pytest_configure(config):
    """Register custom pytest markers"""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (requires real LLM API calls)"
    )
