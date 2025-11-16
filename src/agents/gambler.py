"""Gambler agent for fun commentary and flavor"""

from typing import List, Optional, Dict, Any

from src.agents.base import BaseAgent
from src.data.storage import Database
from src.prompts import GAMBLER_PROMPT
from src.utils.logging import get_logger

logger = get_logger("agents.gambler")


class Gambler(BaseAgent):
    """Gambler agent for adding entertainment value"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Gambler agent"""
        super().__init__("Gambler", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Gambler"""
        return GAMBLER_PROMPT
    
    def process(
        self,
        approved_card: List[Dict[str, Any]],
        bankroll_status: Optional[Dict[str, Any]] = None,
        recent_performance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Add fun commentary to approved card using LLM
        
        Args:
            approved_card: Final approved picks from President
            bankroll_status: Current bankroll status
            recent_performance: Recent performance metrics
            
        Returns:
            LLM response with flavored card and fun leans
        """
        if not self.is_enabled():
            self.log_warning("Gambler agent is disabled")
            return {"official_card_with_flavor": approved_card}
        
        self.log_info("Adding fun commentary to approved card")
        
        # Prepare input for LLM
        input_data = {
            "approved_card": approved_card,
            "bankroll_status": bankroll_status or {},
            "recent_performance": recent_performance or {}
        }
        
        user_prompt = """Please add fun, entertaining commentary to the approved betting card.

Remember:
- You do NOT change any picks or bet sizes
- Add hype, storylines, and entertainment value
- Keep it self-aware and fun
- Optionally suggest fun leans (clearly marked as NOT OFFICIAL)
- Match the tone to bankroll health and recent performance"""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.8,  # Higher temperature for creative, fun commentary
                parse_json=True
            )
            
            self.log_info("Added gambler commentary to card")
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM gambler commentary: {e}", exc_info=True)
            return {
                "official_card_with_flavor": approved_card,
                "fun_leans_not_official": [],
                "disclaimers": ["Error generating commentary"]
            }

