"""Compliance agent for validation and sanity checks"""

from typing import List, Optional, Dict, Any

from src.agents.base import BaseAgent
from src.data.storage import Database
from src.prompts import COMPLIANCE_PROMPT
from src.utils.logging import get_logger

logger = get_logger("agents.compliance")


class Compliance(BaseAgent):
    """Compliance agent for validating picks"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Compliance agent"""
        super().__init__("Compliance", db, llm_client)
        self.max_confidence = self.config.get('max_confidence', 0.85)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Compliance"""
        return COMPLIANCE_PROMPT
    
    def process(
        self,
        sized_picks: List[Dict[str, Any]],
        picker_rationales: Optional[Dict[str, Any]] = None,
        modeler_output: Optional[Dict[str, Any]] = None,
        bankroll_status: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Validate picks using LLM
        
        Args:
            sized_picks: Sized picks from Banker
            picker_rationales: Rationales from Picker
            modeler_output: Model outputs for reference
            bankroll_status: Bankroll status
            
        Returns:
            LLM response with compliance reviews
        """
        if not self.is_enabled():
            self.log_warning("Compliance agent is disabled")
            return {"bet_reviews": []}
        
        self.log_info(f"Validating {len(sized_picks)} picks using LLM")
        
        # Prepare input for LLM
        input_data = {
            "sized_picks": sized_picks,
            "picker_rationales": picker_rationales or {},
            "modeler_output": modeler_output or {},
            "bankroll_status": bankroll_status or {},
            "constraints": {
                "max_confidence": self.max_confidence
            }
        }
        
        user_prompt = """Please review each bet for compliance with responsible gambling practices and logical consistency.

Check for:
- Coherent reasoning (model edge + contextual consistency)
- Over-staking relative to bankroll
- Correlated exposures
- Missing information or data gaps
- Superstitious or non-causal reasoning
- Overconfidence

Approve, approve-with-warning, or reject each bet with clear explanations."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.2,  # Very low temperature for strict compliance
                parse_json=True
            )
            
            reviews = response.get("bet_reviews", [])
            approved = sum(1 for r in reviews if r.get("compliance_status") == "approved")
            self.log_info(f"Approved {approved}/{len(reviews)} picks")
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM compliance check: {e}", exc_info=True)
            return {"bet_reviews": [], "global_risk_assessment": []}
