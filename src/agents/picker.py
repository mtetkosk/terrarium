"""Picker agent for bet selection"""

from typing import List, Optional, Dict, Any
import random

from src.agents.base import BaseAgent
from src.data.models import BetType
from src.data.storage import Database
from src.prompts import PICKER_PROMPT
from src.utils.logging import get_logger

logger = get_logger("agents.picker")


class Picker(BaseAgent):
    """Picker agent for selecting bets"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Picker agent"""
        super().__init__("Picker", db, llm_client)
        self.max_picks = self.config.get('max_picks_per_day', 10)
        self.parlay_enabled = self.config.get('parlay_enabled', True)
        self.parlay_probability = self.config.get('parlay_probability', 0.15)
        self.parlay_min_legs = self.config.get('parlay_min_legs', 2)
        self.parlay_max_legs = self.config.get('parlay_max_legs', 4)
        self.parlay_min_confidence = self.config.get('parlay_min_confidence', 0.65)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Picker"""
        return PICKER_PROMPT
    
    def process(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        bankroll_status: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Select picks using LLM
        
        Args:
            researcher_output: Output from Researcher
            modeler_output: Output from Modeler (predictions and edges)
            bankroll_status: Current bankroll status from Banker
            
        Returns:
            LLM response with candidate picks
        """
        if not self.is_enabled():
            self.log_warning("Picker agent is disabled")
            return {"candidate_picks": []}
        
        self.log_info(f"Selecting picks using LLM")
        
        # Prepare input for LLM
        input_data = {
            "researcher_output": researcher_output,
            "modeler_output": modeler_output,
            "bankroll_status": bankroll_status or {},
            "constraints": {
                "max_picks": self.max_picks,
                "parlay_enabled": self.parlay_enabled
            }
        }
        
        user_prompt = f"""Please analyze the research data and model predictions to select the best betting opportunities.

Focus on:
- Positive expected value (edge > 0)
- Reasonable confidence levels
- Avoid contradictory bets (same game, opposite sides)
- Avoid overly correlated exposures
- Prefer quality over quantity (aim for {self.max_picks} or fewer high-quality picks)

Provide clear justification for each pick combining model edge with contextual reasoning."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.5,  # Balanced for decision-making
                parse_json=True
            )
            
            picks = response.get("candidate_picks", [])
            self.log_info(f"Selected {len(picks)} candidate picks")
            
            # Optionally create parlay if enabled
            if self.parlay_enabled and len(picks) >= self.parlay_min_legs:
                if random.random() < self.parlay_probability:
                    parlay = self._maybe_create_parlay(picks)
                    if parlay:
                        picks.append(parlay)
                        self.log_info(f"🎲 Created parlay with {len(parlay.get('correlation_group', '').split(','))} legs")
            
            response["candidate_picks"] = picks
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM picking: {e}", exc_info=True)
            return {"candidate_picks": [], "overall_strategy_summary": []}
    
    def _maybe_create_parlay(self, picks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Create a parlay from high-confidence picks"""
        eligible = [
            p for p in picks
            if p.get("confidence", 0) >= self.parlay_min_confidence
            and p.get("edge_estimate", 0) > 0
            and p.get("bet_type") != "parlay"
        ]
        
        if len(eligible) < self.parlay_min_legs:
            return None
        
        num_legs = random.randint(
            self.parlay_min_legs,
            min(self.parlay_max_legs, len(eligible))
        )
        
        parlay_legs = sorted(
            eligible,
            key=lambda p: (p.get("confidence", 0), p.get("edge_estimate", 0)),
            reverse=True
        )[:num_legs]
        
        # Calculate combined odds and EV (simplified)
        combined_confidence = sum(p.get("confidence", 0) for p in parlay_legs) / len(parlay_legs)
        combined_edge = sum(p.get("edge_estimate", 0) for p in parlay_legs) / len(parlay_legs)
        
        return {
            "game_id": "parlay",
            "bet_type": "parlay",
            "selection": f"Parlay ({num_legs} legs)",
            "odds": "+500",  # Simplified - would calculate from individual odds
            "justification": [
                f"Parlay combining {num_legs} high-confidence picks for fun",
                f"Legs: {', '.join([p.get('selection', '') for p in parlay_legs])}"
            ],
            "edge_estimate": combined_edge * 0.5,  # Parlays have lower EV
            "confidence": combined_confidence * 0.8,  # Lower confidence
            "correlation_group": ",".join([p.get("game_id", "") for p in parlay_legs]),
            "notes": "Parlay for entertainment - higher risk, lower EV"
        }
