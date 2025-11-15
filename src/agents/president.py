"""President agent for final approval and conflict resolution"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import (
    Pick, ComplianceResult, CardReview, Conflict, Resolution, DailyReport,
    RevisionRequest, RevisionRequestType, GameInsight, Prediction
)
from src.data.storage import Database, CardReviewModel
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.president")


class President(BaseAgent):
    """President agent for executive decisions"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize President agent"""
        super().__init__("President", db)
        self.bankroll_config = config.get_bankroll_config()
    
    def process(
        self,
        picks: List[Pick],
        compliance_results: List[ComplianceResult],
        bankroll_balance: float,
        insights: Optional[List[GameInsight]] = None,
        predictions: Optional[List[Prediction]] = None
    ) -> CardReview:
        """Review and approve betting card"""
        if not self.is_enabled():
            self.log_warning("President agent is disabled")
            return CardReview(
                date=date.today(),
                approved=True,
                picks_approved=[p.id or 0 for p in picks],
                picks_rejected=[]
            )
        
        self.interaction_logger.log_agent_start(
            "President",
            f"Reviewing {len(picks)} picks, {len(compliance_results)} compliance results"
        )
        
        # Review card
        review = self.review_card(
            picks, compliance_results, bankroll_balance, insights, predictions
        )
        
        # Resolve any conflicts
        conflicts = self._detect_conflicts(picks, compliance_results)
        if conflicts:
            self.log_warning(f"Detected {len(conflicts)} conflicts")
            resolution = self.resolve_conflicts(conflicts, picks, compliance_results)
            review.review_notes += f"\nResolved {len(conflicts)} conflicts: {resolution.decision}"
        
        # Set strategic directives based on performance
        # (Would need historical data for this)
        
        self._save_review(review)
        
        self.interaction_logger.log_agent_complete(
            "President",
            f"Card {'APPROVED' if review.approved else 'REJECTED'} | "
            f"{len(review.picks_approved)} approved, {len(review.picks_rejected)} rejected | "
            f"{len(review.revision_requests)} revision requests"
        )
        
        return review
    
    def review_card(
        self,
        picks: List[Pick],
        compliance_results: List[ComplianceResult],
        bankroll_balance: float,
        insights: Optional[List[GameInsight]] = None,
        predictions: Optional[List[Prediction]] = None
    ) -> CardReview:
        """Review betting card"""
        # Create compliance map
        compliance_by_pick = {
            result.pick_id: result for result in compliance_results
        }
        
        approved_picks = []
        rejected_picks = []
        review_notes = []
        
        # Check bankroll health
        min_balance = self.bankroll_config.get('min_balance', 1000.0)
        if bankroll_balance < min_balance:
            review_notes.append(
                f"WARNING: Bankroll ({bankroll_balance:.2f}) below minimum ({min_balance:.2f})"
            )
            # Reject all picks if bankroll too low
            return CardReview(
                date=date.today(),
                approved=False,
                picks_approved=[],
                picks_rejected=[p.id or 0 for p in picks],
                review_notes="Bankroll below minimum threshold. All picks rejected."
            )
        
        # Review each pick
        total_exposure = 0.0
        for pick in picks:
            compliance = compliance_by_pick.get(pick.id or 0)
            
            if not compliance:
                rejected_picks.append(pick.id or 0)
                review_notes.append(f"Pick {pick.id}: No compliance result")
                continue
            
            if not compliance.approved:
                rejected_picks.append(pick.id or 0)
                review_notes.append(
                    f"Pick {pick.id}: Rejected - {', '.join(compliance.reasons)}"
                )
                continue
            
            # Additional checks
            if pick.stake_amount <= 0:
                rejected_picks.append(pick.id or 0)
                review_notes.append(f"Pick {pick.id}: Invalid stake")
                continue
            
            # Check total exposure
            total_exposure += pick.stake_amount
            max_exposure = bankroll_balance * self.bankroll_config.get('max_daily_exposure', 0.05)
            
            if total_exposure > max_exposure:
                rejected_picks.append(pick.id or 0)
                review_notes.append(
                    f"Pick {pick.id}: Would exceed max daily exposure"
                )
                continue
            
            approved_picks.append(pick.id or 0)
        
        # Overall approval decision
        approved = len(approved_picks) > 0
        
        if not approved:
            review_notes.append("No picks approved. Card rejected.")
        else:
            review_notes.append(
                f"Approved {len(approved_picks)}/{len(picks)} picks. "
                f"Total exposure: ${total_exposure:.2f}"
            )
        
        # Strategic directives
        strategic_directives = self._generate_strategic_directives(
            picks, approved_picks, bankroll_balance
        )
        
        # Check if revisions are needed
        revision_requests = self._check_revision_needed(
            picks, insights, predictions, compliance_results
        )
        
        if revision_requests:
            self.interaction_logger.log_revision_request(
                "President",
                "Multiple Agents",
                f"{len(revision_requests)} revision requests generated"
            )
            for req in revision_requests:
                self.interaction_logger.log_revision_request(
                    "President",
                    req.target_agent,
                    req.feedback
                )
        
        review = CardReview(
            date=date.today(),
            approved=approved and len(revision_requests) == 0,  # Don't approve if revisions needed
            picks_approved=approved_picks,
            picks_rejected=rejected_picks,
            review_notes="\n".join(review_notes),
            strategic_directives=strategic_directives,
            revision_requests=revision_requests
        )
        
        return review
    
    def _check_revision_needed(
        self,
        picks: List[Pick],
        insights: Optional[List[GameInsight]],
        predictions: Optional[List[Prediction]],
        compliance_results: List[ComplianceResult]
    ) -> List[RevisionRequest]:
        """Check if revisions are needed and generate requests"""
        revision_requests = []
        
        # Check research quality
        if insights:
            low_quality_count = sum(
                1 for i in insights
                if i.confidence_factors.get('data_quality', 1.0) < 0.6
            )
            if low_quality_count > len(insights) * 0.3:  # More than 30% low quality
                revision_requests.append(RevisionRequest(
                    request_type=RevisionRequestType.RESEARCH,
                    target_agent="Researcher",
                    feedback=f"{low_quality_count} games have low data quality. "
                            "Please gather more comprehensive statistics and injury data.",
                    priority="high"
                ))
        
        # Check prediction quality
        if predictions:
            low_confidence_count = sum(
                1 for p in predictions
                if p.confidence_score < 0.5
            )
            if low_confidence_count > len(predictions) * 0.4:  # More than 40% low confidence
                revision_requests.append(RevisionRequest(
                    request_type=RevisionRequestType.MODELING,
                    target_agent="Modeler",
                    feedback=f"{low_confidence_count} predictions have low confidence. "
                            "Please review model inputs and consider additional factors.",
                    priority="medium"
                ))
        
        # Check pick quality
        if picks:
            low_ev_count = sum(1 for p in picks if p.expected_value < 0.05)
            if low_ev_count > len(picks) * 0.5:  # More than 50% low EV
                revision_requests.append(RevisionRequest(
                    request_type=RevisionRequestType.SELECTION,
                    target_agent="Picker",
                    feedback=f"{low_ev_count} picks have low expected value. "
                            "Please be more selective and only choose highest-EV opportunities.",
                    priority="high"
                ))
        
        # Check compliance rejections
        rejection_rate = sum(1 for r in compliance_results if not r.approved) / len(compliance_results) if compliance_results else 0
        if rejection_rate > 0.5:  # More than 50% rejected
            revision_requests.append(RevisionRequest(
                request_type=RevisionRequestType.VALIDATION,
                target_agent="Compliance",
                feedback=f"High rejection rate ({rejection_rate:.1%}). "
                        "Please review validation criteria or provide more detailed feedback.",
                priority="medium"
            ))
        
        return revision_requests
    
    def request_revision(
        self,
        request_type: RevisionRequestType,
        target_agent: str,
        feedback: str,
        priority: str = "medium",
        original_output_id: Optional[int] = None
    ) -> RevisionRequest:
        """Create a revision request"""
        request = RevisionRequest(
            request_type=request_type,
            target_agent=target_agent,
            original_output_id=original_output_id,
            feedback=feedback,
            priority=priority
        )
        
        self.interaction_logger.log_revision_request(
            self.name,
            target_agent,
            feedback
        )
        
        return request
    
    def resolve_conflicts(
        self,
        conflicts: List[Conflict],
        picks: List[Pick],
        compliance_results: List[ComplianceResult]
    ) -> Resolution:
        """Resolve conflicts between agents"""
        if not conflicts:
            return Resolution(
                resolution="No conflicts to resolve",
                decision="none",
                resolved_by=self.name
            )
        
        # Resolve each conflict
        resolutions = []
        for conflict in conflicts:
            if conflict.conflict_type == "compliance_disagreement":
                # President overrides compliance if EV is very high
                # Find the pick in question
                pick = next((p for p in picks if p.id == conflict.description), None)
                if pick and pick.expected_value > 0.15:
                    resolutions.append("Override compliance rejection due to high EV")
                else:
                    resolutions.append("Uphold compliance rejection")
            
            elif conflict.conflict_type == "stake_allocation":
                resolutions.append("Adjust stakes proportionally")
            
            else:
                resolutions.append("Default: conservative approach")
        
        decision = "; ".join(resolutions)
        
        return Resolution(
            resolution=f"Resolved {len(conflicts)} conflicts",
            decision=decision,
            resolved_by=self.name
        )
    
    def _detect_conflicts(
        self,
        picks: List[Pick],
        compliance_results: List[ComplianceResult]
    ) -> List[Conflict]:
        """Detect conflicts between agents"""
        conflicts = []
        
        # Check for compliance rejections of high-EV picks
        compliance_by_pick = {
            result.pick_id: result for result in compliance_results
        }
        
        for pick in picks:
            compliance = compliance_by_pick.get(pick.id or 0)
            if compliance and not compliance.approved and pick.expected_value > 0.10:
                conflicts.append(Conflict(
                    conflict_type="compliance_disagreement",
                    description=str(pick.id or 0),
                    involved_agents=["Compliance", "Picker"],
                    severity="medium"
                ))
        
        return conflicts
    
    def _generate_strategic_directives(
        self,
        picks: List[Pick],
        approved_picks: List[int],
        bankroll_balance: float
    ) -> Dict[str, Any]:
        """Generate strategic directives"""
        directives = {}
        
        # Calculate average EV
        if approved_picks:
            approved_pick_objects = [p for p in picks if (p.id or 0) in approved_picks]
            avg_ev = sum(p.expected_value for p in approved_pick_objects) / len(approved_pick_objects)
            
            if avg_ev < 0.05:
                directives["ev_threshold"] = "Consider raising EV threshold"
            
            if avg_ev > 0.15:
                directives["ev_threshold"] = "High EV opportunities detected. Good market conditions."
        
        # Bankroll health
        min_balance = self.bankroll_config.get('min_balance', 1000.0)
        if bankroll_balance < min_balance * 1.5:
            directives["bankroll"] = "Conservative approach recommended"
        
        # Pick count
        if len(approved_picks) == 0:
            directives["selection"] = "No picks approved. Review model and data quality."
        elif len(approved_picks) < 3:
            directives["selection"] = "Low pick count. Consider expanding criteria."
        
        return directives
    
    def set_strategy(self, performance: Dict[str, Any]) -> Dict[str, Any]:
        """Set strategic directives based on performance"""
        strategy = {}
        
        roi = performance.get('roi', 0.0)
        win_rate = performance.get('win_rate', 0.0)
        
        if roi < -5.0:
            strategy['action'] = "Reduce exposure and review models"
        elif roi > 5.0:
            strategy['action'] = "Continue current approach"
        else:
            strategy['action'] = "Monitor closely"
        
        if win_rate < 0.45:
            strategy['ev_threshold'] = "Increase minimum EV threshold"
        elif win_rate > 0.55:
            strategy['ev_threshold'] = "Current EV threshold appropriate"
        
        return strategy
    
    def _save_review(self, review: CardReview) -> None:
        """Save card review to database"""
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            # Convert revision requests to JSON
            revision_requests_json = [
                {
                    'request_type': req.request_type,
                    'target_agent': req.target_agent,
                    'original_output_id': req.original_output_id,
                    'feedback': req.feedback,
                    'priority': req.priority
                }
                for req in review.revision_requests
            ] if review.revision_requests else None
            
            review_model = CardReviewModel(
                date=review.date,
                approved=review.approved,
                picks_approved=review.picks_approved,
                picks_rejected=review.picks_rejected,
                review_notes=review.review_notes,
                strategic_directives=review.strategic_directives,
                revision_requests=revision_requests_json
            )
            session.add(review_model)
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving card review: {e}")
            session.rollback()
        finally:
            session.close()

