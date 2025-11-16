"""President agent for final approval and conflict resolution"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import CardReview
from src.data.storage import Database, CardReviewModel
from src.prompts import PRESIDENT_PROMPT
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.president")


class President(BaseAgent):
    """President agent for executive decisions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize President agent"""
        super().__init__("President", db, llm_client)
        self.bankroll_config = config.get_bankroll_config()
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for President"""
        return PRESIDENT_PROMPT
    
    def process(
        self,
        sized_picks: List[Dict[str, Any]],
        compliance_results: Dict[str, Any],
        researcher_output: Optional[Dict[str, Any]] = None,
        modeler_output: Optional[Dict[str, Any]] = None,
        banker_output: Optional[Dict[str, Any]] = None,
        auditor_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Review and approve betting card using LLM
        
        Args:
            sized_picks: Sized picks from Banker
            compliance_results: Compliance reviews
            researcher_output: Researcher insights
            modeler_output: Model predictions
            banker_output: Bankroll status
            auditor_feedback: Historical performance feedback
            
        Returns:
            LLM response with approved/rejected picks and strategy notes
        """
        if not self.is_enabled():
            self.log_warning("President agent is disabled")
            return {
                "approved_picks": sized_picks,
                "rejected_picks": [],
                "high_level_strategy_notes": []
            }
        
        self.interaction_logger.log_agent_start(
            "President",
            f"Reviewing {len(sized_picks)} picks for final approval"
        )
        
        # Prepare input for LLM
        input_data = {
            "sized_picks": sized_picks,
            "compliance_results": compliance_results,
            "researcher_output": researcher_output or {},
            "modeler_output": modeler_output or {},
            "banker_output": banker_output or {},
            "auditor_feedback": auditor_feedback or {},
            "bankroll_config": {
                "min_balance": self.bankroll_config.get('min_balance', 10.0),
                "initial": self.bankroll_config.get('initial', 100.0)
            }
        }
        
        user_prompt = """Please review the complete betting proposal and make the final approval decision.

Consider:
- Alignment with long-term bankroll growth
- Avoidance of ruin
- Avoidance of over-concentration
- Data-driven rationale
- Compliance feedback
- Model confidence and edge estimates

If you need clarification or additional information to make a confident decision, you can request revisions from any agent:
- Researcher: Request more detailed game context, injury updates, or market data
- Modeler: Request recalculation of probabilities or edge estimates
- Picker: Request different picks or more selective criteria
- Banker: Request stake adjustments or exposure changes
- Compliance: Request additional validation or risk assessment

If you include revision_requests, the system will loop back to get the additional information before final approval.

Provide your decision in the specified JSON format with approved picks, rejected picks, revision requests (if needed), and strategic notes."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.3,  # Low temperature for consistent, rational decisions
                parse_json=True
            )
            
            approved_count = len(response.get("approved_picks", []))
            rejected_count = len(response.get("rejected_picks", []))
            
            self.interaction_logger.log_agent_complete(
                "President",
                f"Card decision: {approved_count} approved, {rejected_count} rejected"
            )
            
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM presidential review: {e}", exc_info=True)
            # Fallback: reject all on error
            return {
                "approved_picks": [],
                "rejected_picks": [p.get("game_id", "") for p in sized_picks],
                "high_level_strategy_notes": [
                    f"Error during review: {str(e)}. All picks rejected for safety."
                ]
            }
