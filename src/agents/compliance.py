"""Compliance agent for validation and sanity checks"""

from typing import List, Optional
import re

from src.agents.base import BaseAgent
from src.data.models import Pick, GameInsight, ComplianceResult, Bankroll
from src.data.storage import Database, ComplianceResultModel
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.compliance")


class Compliance(BaseAgent):
    """Compliance agent for validation"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Compliance agent"""
        super().__init__("Compliance", db)
        self.betting_config = config.get_betting_config()
        self.max_confidence = self.betting_config.get('max_confidence', 0.85)
    
    def process(
        self,
        picks: List[Pick],
        insights: List[GameInsight],
        bankroll: Bankroll
    ) -> List[ComplianceResult]:
        """Validate picks"""
        if not self.is_enabled():
            self.log_warning("Compliance agent is disabled")
            return [ComplianceResult(
                pick_id=pick.id or 0,
                approved=True,
                risk_level="low"
            ) for pick in picks]
        
        self.log_info(f"Validating {len(picks)} picks")
        
        # Create insights map
        insights_by_game = {insight.game_id: insight for insight in insights}
        
        results = []
        for pick in picks:
            insight = insights_by_game.get(pick.game_id)
            result = self.validate_pick(pick, insight, bankroll)
            results.append(result)
            self._save_result(result)
        
        approved_count = sum(1 for r in results if r.approved)
        self.log_info(f"Approved {approved_count}/{len(results)} picks")
        
        return results
    
    def validate_pick(
        self,
        pick: Pick,
        insight: Optional[GameInsight],
        bankroll: Bankroll
    ) -> ComplianceResult:
        """Validate a single pick"""
        reasons = []
        approved = True
        
        # Check required information
        if not pick.rationale or len(pick.rationale.strip()) < 10:
            reasons.append("Missing or insufficient rationale")
            approved = False
        
        if pick.stake_amount <= 0:
            reasons.append("Invalid stake amount")
            approved = False
        
        if pick.expected_value <= 0:
            reasons.append("Non-positive expected value")
            approved = False
        
        # Check reasoning quality
        if not self.check_reasoning_quality(pick.rationale):
            reasons.append("Poor reasoning quality (non-causal or superstitious)")
            approved = False
        
        # Check overconfidence
        if pick.confidence > self.max_confidence:
            reasons.append(f"Overconfident (confidence {pick.confidence:.2f} > {self.max_confidence})")
            approved = False
        
        # Check risk level
        risk_level = self.check_risk_level(pick, bankroll)
        if risk_level == "high":
            reasons.append("High risk level")
            approved = False
        
        # Check for missing insight data
        if not insight:
            reasons.append("Missing game insight data")
            approved = False
        elif insight.confidence_factors.get('data_quality', 1.0) < 0.5:
            reasons.append("Low data quality")
            approved = False
        
        if approved and not reasons:
            reasons.append("All checks passed")
        
        return ComplianceResult(
            pick_id=pick.id or 0,
            approved=approved,
            reasons=reasons,
            risk_level=risk_level
        )
    
    def check_reasoning_quality(self, rationale: str) -> bool:
        """Check if reasoning is causal and non-superstitious"""
        if not rationale:
            return False
        
        rationale_lower = rationale.lower()
        
        # Superstitious patterns to reject
        superstitious_patterns = [
            r'\b(lucky|unlucky|jinx|curse|omen)\b',
            r'\b(always|never)\s+(wins|loses)\b',
            r'\b(guaranteed|certain|definitely)\b',
        ]
        
        for pattern in superstitious_patterns:
            if re.search(pattern, rationale_lower):
                return False
        
        # Require some causal reasoning
        causal_indicators = [
            'model', 'stat', 'average', 'points', 'defense', 'offense',
            'injury', 'matchup', 'spread', 'probability', 'expected'
        ]
        
        has_causal = any(indicator in rationale_lower for indicator in causal_indicators)
        
        if not has_causal:
            return False
        
        # Check minimum length and substance
        if len(rationale) < 20:
            return False
        
        return True
    
    def check_risk_level(self, pick: Pick, bankroll: Bankroll) -> str:
        """Check risk level of a pick"""
        # Calculate stake as percentage of bankroll
        stake_pct = (pick.stake_amount / bankroll.balance) * 100 if bankroll.balance > 0 else 0
        
        # High risk: >5% of bankroll
        if stake_pct > 5.0:
            return "high"
        
        # Medium risk: 2-5% of bankroll or low confidence
        if stake_pct > 2.0 or pick.confidence < 0.6:
            return "medium"
        
        # Low risk: <2% of bankroll and reasonable confidence
        return "low"
    
    def _save_result(self, result: ComplianceResult) -> None:
        """Save compliance result to database"""
        if not self.db or result.pick_id == 0:
            return
        
        session = self.db.get_session()
        try:
            result_model = ComplianceResultModel(
                pick_id=result.pick_id,
                approved=result.approved,
                reasons=result.reasons,
                risk_level=result.risk_level
            )
            session.add(result_model)
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving compliance result: {e}")
            session.rollback()
        finally:
            session.close()

