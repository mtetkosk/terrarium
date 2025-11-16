"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import Prediction
from src.data.storage import Database
from src.prompts import MODELER_PROMPT
from src.utils.logging import get_logger

logger = get_logger("agents.modeler")


class Modeler(BaseAgent):
    """Modeler agent for generating predictions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Modeler agent"""
        super().__init__("Modeler", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Modeler"""
        return MODELER_PROMPT
    
    def process(
        self,
        researcher_output: Dict[str, Any],
        betting_lines: Optional[List] = None,
        historical_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate predictions using LLM
        
        Args:
            researcher_output: Output from Researcher agent (game insights)
            betting_lines: Optional list of BettingLine objects (will be converted to dicts)
            historical_data: Optional historical performance data
            
        Returns:
            LLM response with predictions and edge estimates
        """
        if not self.is_enabled():
            self.log_warning("Modeler agent is disabled")
            return {"game_models": []}
        
        games = researcher_output.get("games", [])
        self.log_info(f"Modeling {len(games)} games using LLM")
        
        # Convert betting lines to JSON-serializable format if provided
        betting_lines_dict = None
        if betting_lines:
            betting_lines_dict = []
            for line in betting_lines:
                # Convert BettingLine dataclass to dict
                line_dict = {
                    "game_id": line.game_id,
                    "book": line.book,
                    "bet_type": line.bet_type.value if hasattr(line.bet_type, 'value') else str(line.bet_type),
                    "line": line.line,
                    "odds": line.odds,
                    "id": line.id,
                    "timestamp": line.timestamp.isoformat() if hasattr(line.timestamp, 'isoformat') else str(line.timestamp)
                }
                betting_lines_dict.append(line_dict)
        
        # Prepare input for LLM
        input_data = {
            "researcher_output": researcher_output,
            "betting_lines": betting_lines_dict,
            "historical_data": historical_data or {}
        }
        
        user_prompt = """Please analyze the game data and generate predictions for each game.

For each game, provide:
- Win probabilities for each side
- Expected scoring margin (for spreads)
- Expected total points (for totals)
- Market edges (your probability vs implied probability from odds)
- Confidence levels based on data quality

Be explicit, quantitative, and cautious. If data is thin or noisy, lower your confidence and explain why."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.4,  # Slightly higher for modeling but still controlled
                parse_json=True
            )
            
            self.log_info(f"Generated predictions for {len(response.get('game_models', []))} games")
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM modeling: {e}", exc_info=True)
            return {"game_models": []}
