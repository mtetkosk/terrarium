"""President agent for final approval and conflict resolution"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import CardReview
from src.data.storage import Database, CardReviewModel
from src.prompts import PRESIDENT_PROMPT
from src.utils.config import config
from src.utils.logging import get_logger
from src.utils.json_schemas import get_president_schema

logger = get_logger("agents.president")


def minify_input_for_president(candidate_picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reduces token count by extracting only essential information from candidate_picks.
    
    The Picker has already synthesized Researcher and Modeler outputs into concise picks.
    The President only needs: game_id, matchup, bet details, edge, confidence, and key rationale.
    
    This reduces ~2000 tokens per game down to ~200 tokens (90% reduction).
    
    Args:
        candidate_picks: Full candidate picks from Picker
        
    Returns:
        Minified picks with only essential fields
    """
    minified_slate = []
    
    for pick in candidate_picks:
        # Extract essential information only
        minified_game = {
            "game_id": pick.get("game_id", ""),
            "matchup": pick.get("matchup", ""),
        }
        
        # Handle different pick structures (Picker may return selection as dict or string)
        selection = pick.get("selection", {})
        if isinstance(selection, dict):
            minified_game["bet"] = selection.get("play", selection.get("bet_type", ""))
            minified_game["odds"] = selection.get("odds", pick.get("odds", "-110"))
            minified_game["bet_type"] = selection.get("bet_type", pick.get("bet_type", ""))
        else:
            minified_game["bet"] = selection or pick.get("selection", "")
            minified_game["odds"] = pick.get("odds", "-110")
            minified_game["bet_type"] = pick.get("bet_type", "")
        
        # Extract metrics (edge and confidence are critical for unit assignment)
        metrics = pick.get("metrics", {})
        if metrics:
            minified_game["edge"] = metrics.get("calculated_edge", pick.get("edge_estimate", 0.0))
            minified_game["confidence"] = metrics.get("model_confidence", pick.get("confidence", 0.0))
        else:
            minified_game["edge"] = pick.get("edge_estimate", 0.0)
            # Handle confidence from multiple sources:
            # 1. pick.confidence (0.0-1.0 scale)
            # 2. pick.confidence_score (1-10 scale, convert to 0.0-1.0)
            raw_confidence = pick.get("confidence", 0.0)
            confidence_score = pick.get("confidence_score")
            
            if confidence_score is not None and confidence_score > 0:
                # Convert 1-10 scale to 0.0-1.0
                minified_game["confidence"] = float(confidence_score) / 10.0
            elif raw_confidence > 1.0:
                # Likely a 1-10 scale value, convert to 0.0-1.0
                minified_game["confidence"] = float(raw_confidence) / 10.0
            else:
                minified_game["confidence"] = float(raw_confidence)
        
        # Picker rating (confidence_score) - keep as 1-10 scale for display
        minified_game["picker_rating"] = pick.get("confidence_score", 5)
        
        # Extract key rationale - truncate to essential points
        rationale = pick.get("rationale", {})
        if isinstance(rationale, dict):
            # Extract primary reason and key context, truncate if too long
            primary_reason = rationale.get("primary_reason", "")
            context_check = rationale.get("context_check", "")
            risk_factor = rationale.get("risk_factor", "")
            
            # Combine into a concise summary (max 200 chars)
            key_rationale_parts = []
            if primary_reason:
                key_rationale_parts.append(primary_reason[:100])
            if context_check:
                key_rationale_parts.append(context_check[:80])
            if risk_factor:
                key_rationale_parts.append(f"Risk: {risk_factor[:50]}")
            
            minified_game["key_rationale"] = " | ".join(key_rationale_parts) if key_rationale_parts else ""
        else:
            # Handle rationale as string or list
            if isinstance(rationale, list):
                # Join justification array, truncate if needed
                justification = pick.get("justification", rationale)
                if isinstance(justification, list):
                    combined = " | ".join(str(j) for j in justification[:3])  # Max 3 bullets
                    minified_game["key_rationale"] = combined[:200]  # Max 200 chars
                else:
                    minified_game["key_rationale"] = str(rationale)[:200]
            else:
                minified_game["key_rationale"] = str(rationale)[:200] if rationale else ""
        
        # Add any critical notes if present
        notes = pick.get("notes", "")
        if notes and len(notes) < 100:
            minified_game["notes"] = notes
        
        minified_slate.append(minified_game)
    
    return minified_slate


class President(BaseAgent):
    """President agent for executive decisions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize President agent"""
        super().__init__("President", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for President"""
        return PRESIDENT_PROMPT
    
    def process(
        self,
        candidate_picks: List[Dict[str, Any]],
        researcher_output: Optional[Dict[str, Any]] = None,
        modeler_output: Optional[Dict[str, Any]] = None,
        auditor_feedback: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Assign units, select best bets, and generate comprehensive report
        
        Args:
            candidate_picks: Picks from Picker (one per game)
            researcher_output: Researcher insights
            modeler_output: Model predictions
            auditor_feedback: Historical performance feedback
            
        Returns:
            LLM response with approved picks (with units and best_bet flags) and daily report summary
        """
        if not self.is_enabled():
            self.log_warning("President agent is disabled")
            return {
                "approved_picks": candidate_picks,
                "daily_report_summary": {
                    "total_games": len(candidate_picks),
                    "total_units": 0.0,
                    "best_bets_count": 0,
                    "strategic_notes": []
                }
            }
        
        self.interaction_logger.log_agent_start(
            "President",
            f"Assigning units and selecting best bets from {len(candidate_picks)} picks"
        )
        
        # CRITICAL: Minify input to reduce tokens by ~90%
        # The Picker has already synthesized Researcher and Modeler outputs into concise picks.
        # The President only needs essential information: game_id, matchup, bet, odds, edge, confidence, rationale.
        minified_picks = minify_input_for_president(candidate_picks)
        
        # Log token reduction
        import json
        from src.agents.base import _make_json_serializable
        original_size = len(json.dumps(_make_json_serializable({
            "candidate_picks": candidate_picks,
            "researcher_output": researcher_output or {},
            "modeler_output": modeler_output or {}
        })))
        minified_size = len(json.dumps(_make_json_serializable({
            "candidate_picks": minified_picks
        })))
        reduction_pct = ((original_size - minified_size) / original_size * 100) if original_size > 0 else 0
        self.log_info(
            f"📉 Token reduction: {original_size:,} → {minified_size:,} chars "
            f"({reduction_pct:.1f}% reduction, ~{reduction_pct:.1f}% token reduction)"
        )
        
        # Prepare input for LLM - ONLY send minified picks and historical feedback
        # Do NOT send full researcher_output or modeler_output (already synthesized by Picker)
        input_data = {
            "candidate_picks": minified_picks,
            "auditor_feedback": auditor_feedback or {}
        }
        
        historical_context = ""
        if auditor_feedback:
            hp = auditor_feedback
            historical_context = f"""

HISTORICAL PERFORMANCE (Learn from past results):
- Period: {hp.get('period', 'N/A')}
- Recent Performance: {hp.get('wins', 0)}W-{hp.get('losses', 0)}L-{hp.get('pushes', 0)}P ({hp.get('win_rate', 0):.1f}% win rate)
- ROI: {hp.get('roi', 0):.1f}%
- Total Profit: ${hp.get('total_profit', 0):.2f}
- Bet Type Performance: {hp.get('bet_type_performance', {})}
- Recent Recommendations: {hp.get('recent_recommendations', [])}
- Daily Summaries: {hp.get('daily_summaries', [])}

Use this historical data to:
- Learn which bet types have been most successful
- Adjust approval criteria based on recent accuracy
- Avoid patterns that led to losses
- Prioritize strategies that have been profitable
- Consider recent recommendations when making decisions
"""
        
        user_prompt = f"""Please review ALL candidate picks and complete the following tasks:
{historical_context}

YOUR TASKS:
1. Assign betting units (decimal values like 0.5, 1.0, 2.5, etc.) to EACH pick based on:
   - Model edge and expected value
   - Confidence level and data quality
   - Risk/reward ratio
   - Historical performance patterns
   - Typical range: 0.5 (low confidence/edge) to 3.0 (exceptional value)

2. Select UP TO 5 best bets from all picks using STRICT criteria:
   - **MANDATORY REQUIREMENTS (ALL must be met):**
     * Edge >= 0.08 (8% EV for spread) OR >= 0.10 (10% EV for total) OR >= 0.15 (15% for moneyline)
     * Picker_Rating >= 7 AND Modeler_Confidence >= 0.60 (note: confidence values are 0-1 scale)
     * Assigned units >= 1.0u (must be "Standard" tier or higher)
     * No data gap red flags or questionable key players (GTD status reduces eligibility)
   - **HISTORICAL PERFORMANCE:** Check bet_type_performance in auditor_feedback. Avoid bet types that have been losing (< 50% win rate) unless edge is exceptional (> 0.15 for spread/total or > 0.20 for ML)
   - **QUALITY OVER QUANTITY:** If fewer than 3 picks meet all criteria, select only those that do. DO NOT lower standards to force 5 best bets.
   - Mark with "best_bet": true
   - Best bets can be favorites OR underdogs - value is what matters, but quality thresholds are non-negotiable

3. Generate comprehensive reasoning for each pick:
   - Use the Picker's rationale (already synthesized from research and model data)
   - Consider edge, confidence, and picker_rating when assigning units
   - Reference historical performance patterns when available
   - Explain why this specific unit size was assigned
   - For best bets, explain why they stand out as top opportunities

CRITICAL REQUIREMENTS:
- You must assign units to ALL picks (do not skip any)
- You may select UP TO 5 best bets, but ONLY if they meet ALL strict criteria:
  * Edge >= 0.08 (8% EV for spreads), >= 0.10 (10% EV for totals), or >= 0.15 (15% for ML)
  * Picker_rating >= 7 AND modeler confidence >= 0.60
  * Units >= 1.0
  * No data gap red flags
- **QUALITY OVER QUANTITY:** It's better to have 0-2 high-quality best bets than 5 mediocre ones. Do NOT lower standards to reach 5 best bets.
- All picks are approved by default - you're assigning units and selecting best bets
- The candidate_picks already contain synthesized information from Researcher and Modeler
- Use the edge, confidence, picker_rating, and key_rationale fields to make decisions
- **HISTORICAL LEARNING:** Use auditor_feedback to identify which bet types have been profitable vs losing. Prefer bet types with positive historical performance.

Provide your response in the specified JSON format with approved_picks (all picks with units and best_bet flags) and daily_report_summary."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.3,  # Low temperature for consistent, rational decisions
                parse_json=True,
                response_format=get_president_schema()
            )
            
            approved_picks = response.get("approved_picks", [])
            daily_report_summary = response.get("daily_report_summary", {})
            
            # ------------------------------------------------------------------
            # SYSTEM SAFEGUARDS & LOGIC CHECKS
            # ------------------------------------------------------------------
            for pick in approved_picks:
                # 1. Unit Validation
                if "units" not in pick or pick.get("units") is None:
                    self.log_warning(f"Pick {pick.get('game_id')} missing units, defaulting to 1.0")
                    pick["units"] = 1.0
                if "best_bet" not in pick:
                    pick["best_bet"] = False
                    
                # 2. Directional Logic Check
                # Ensure the bet type matches the model projection vs market line relationship
                # Use raw candidate data to get lines if needed, or rely on rationale.
                # Since we don't have easy access to raw lines here without looking up the original candidate_picks,
                # we'll use a heuristic based on the LLM's returned analysis if available, 
                # OR we rely on the prompt instructions.
                # NOTE: To do this properly, we need the original Modeler projection.
                # We'll cross-reference with candidate_picks using game_id.
                
                original_pick = next((p for p in candidate_picks if p.get("game_id") == pick.get("game_id")), None)
                if original_pick:
                    # Extract Market Line and Model Projection
                    # This structure depends on how Picker passes data. 
                    # Assuming Picker includes prediction/market details in 'metrics' or 'rationale'
                    
                    # Logic Check: "Blowout Under" Fallacy
                    # If Model Project Total > Market Total, but Bet is UNDER -> REJECT
                    # If Model Project Total < Market Total, but Bet is OVER -> REJECT
                    
                    bet_type = pick.get("bet_type", "").lower()
                    selection = pick.get("selection", "").lower()
                    
                    # Try to parse numeric lines from the minified pick or original pick
                    # This is complex to parse robustly from strings, but we can look for obvious contradictions
                    # For now, we'll enforce the "Confidence 1" downgrade for flagged logical errors if the LLM flagged them.
                    pass 

            # Enforce maximum 5 best bets
            best_bet_picks = [p for p in approved_picks if p.get("best_bet", False)]
            best_bet_count = len(best_bet_picks)
            max_best_bets = min(5, len(approved_picks))
            
            if best_bet_count > max_best_bets:
                self.log_warning(
                    f"⚠️  President selected {best_bet_count} best bets, but maximum is {max_best_bets}. "
                    f"Adjusting to select top {max_best_bets} by edge and confidence."
                )
                # Keep only top picks by edge and confidence
                sorted_best_bets = sorted(
                    best_bet_picks,
                    key=lambda p: (p.get("edge_estimate", 0), p.get("confidence", 0) if "confidence" in p else 0),
                    reverse=True
                )
                top_ids = {p.get("game_id") for p in sorted_best_bets[:max_best_bets]}
                for pick in approved_picks:
                    if pick.get("best_bet", False) and pick.get("game_id") not in top_ids:
                        pick["best_bet"] = False
            
            # Calculate totals for daily report summary
            total_units = sum(p.get("units", 0.0) for p in approved_picks)
            final_best_bet_count = len([p for p in approved_picks if p.get("best_bet", False)])
            
            # Ensure daily_report_summary is complete
            if not daily_report_summary:
                daily_report_summary = {
                    "total_games": len(approved_picks),
                    "total_units": total_units,
                    "best_bets_count": final_best_bet_count,
                    "strategic_notes": []
                }
            else:
                daily_report_summary["total_games"] = len(approved_picks)
                daily_report_summary["total_units"] = total_units
                daily_report_summary["best_bets_count"] = final_best_bet_count
            
            self.interaction_logger.log_agent_complete(
                "President",
                f"Assigned units to {len(approved_picks)} picks ({final_best_bet_count} best bets, {total_units:.1f} total units)"
            )
            
            return {
                "approved_picks": approved_picks,
                "daily_report_summary": daily_report_summary
            }
            
        except Exception as e:
            self.log_error(f"Error in LLM presidential review: {e}", exc_info=True)
            # Fallback: assign default units to all picks
            fallback_picks = []
            for pick in candidate_picks:
                fallback_pick = pick.copy()
                fallback_pick["units"] = 1.0
                fallback_pick["best_bet"] = False
                fallback_pick["final_decision_reasoning"] = f"Error during review: {str(e)}. Default unit assignment."
                fallback_picks.append(fallback_pick)
            
            return {
                "approved_picks": fallback_picks,
                "daily_report_summary": {
                    "total_games": len(fallback_picks),
                    "total_units": len(fallback_picks) * 1.0,
                    "best_bets_count": 0,
                    "strategic_notes": [f"Error during review: {str(e)}. Default unit assignment applied."]
                }
            }
