"""President agent for final approval and conflict resolution"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import CardReview
from src.data.storage import Database, CardReviewModel
from src.prompts import PRESIDENT_PROMPT, build_president_user_prompt
from src.utils.config import config
from src.utils.logging import get_logger
from src.utils.json_schemas import get_president_schema

logger = get_logger("agents.president")


def _minify_single_pick(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Extract essential fields from a single pick for President input (token reduction)."""
    minified_game = {
        "game_id": pick.get("game_id", ""),
        "matchup": pick.get("matchup", ""),
    }
    selection = pick.get("selection", {})
    if isinstance(selection, dict):
        minified_game["bet"] = selection.get("play", selection.get("bet_type", ""))
        minified_game["odds"] = selection.get("odds", pick.get("odds", "-110"))
        minified_game["bet_type"] = selection.get("bet_type", pick.get("bet_type", ""))
    else:
        minified_game["bet"] = selection or pick.get("selection", "")
        minified_game["odds"] = pick.get("odds", "-110")
        minified_game["bet_type"] = pick.get("bet_type", "")

    metrics = pick.get("metrics", {})
    if metrics:
        minified_game["edge"] = metrics.get("calculated_edge", pick.get("edge_estimate", 0.0))
        minified_game["confidence"] = metrics.get("model_confidence", pick.get("confidence", 0.0))
    else:
        minified_game["edge"] = pick.get("edge_estimate", 0.0)
        raw_confidence = pick.get("confidence", 0.0)
        confidence_score = pick.get("confidence_score")
        if confidence_score is not None and confidence_score > 0:
            minified_game["confidence"] = float(confidence_score) / 10.0
        elif raw_confidence > 1.0:
            minified_game["confidence"] = float(raw_confidence) / 10.0
        else:
            minified_game["confidence"] = float(raw_confidence)

    confidence_score = pick.get("confidence_score")
    if confidence_score is None:
        confidence_value = pick.get("confidence", 0.5)
        confidence_score = max(1, min(10, int(round(confidence_value * 10)))) if confidence_value <= 1.0 else max(1, min(10, int(confidence_value)))
    minified_game["picker_rating"] = confidence_score

    rationale = pick.get("rationale", {})
    if isinstance(rationale, dict):
        key_rationale_parts = []
        if rationale.get("primary_reason"):
            key_rationale_parts.append(rationale["primary_reason"][:100])
        if rationale.get("context_check"):
            key_rationale_parts.append(rationale["context_check"][:80])
        if rationale.get("risk_factor"):
            key_rationale_parts.append(f"Risk: {rationale['risk_factor'][:50]}")
        minified_game["key_rationale"] = " | ".join(key_rationale_parts) if key_rationale_parts else ""
    else:
        if isinstance(rationale, list):
            justification = pick.get("justification", rationale)
            if isinstance(justification, list):
                minified_game["key_rationale"] = " | ".join(str(j) for j in justification[:3])[:200]
            else:
                minified_game["key_rationale"] = str(rationale)[:200]
        else:
            minified_game["key_rationale"] = str(rationale)[:200] if rationale else ""

    notes = pick.get("notes", "")
    if notes and len(notes) < 100:
        minified_game["notes"] = notes
    return minified_game


def minify_input_for_president(candidate_picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reduces token count by extracting only essential information from candidate_picks.
    The President only needs: game_id, matchup, bet details, edge, confidence, and key rationale.
    """
    return [_minify_single_pick(pick) for pick in candidate_picks]


class President(BaseAgent):
    """President agent for executive decisions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize President agent"""
        super().__init__("President", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for President"""
        return PRESIDENT_PROMPT

    def _apply_pick_safeguards(
        self,
        approved_picks: List[Dict[str, Any]],
        candidate_picks: List[Dict[str, Any]],
        minified_picks: List[Dict[str, Any]],
    ) -> None:
        """Apply unit validation, best_bet defaults, low-confidence filter, and max 5 best bets."""
        picker_rating_map = {p.get("game_id"): p.get("picker_rating") for p in minified_picks if p.get("game_id")}
        for pick in approved_picks:
            if "units" not in pick or pick.get("units") is None:
                self.log_warning(f"Pick {pick.get('game_id')} missing units, defaulting to 1.0")
                pick["units"] = 1.0
            if "best_bet" not in pick:
                pick["best_bet"] = False
        for pick in approved_picks:
            if not pick.get("best_bet", False):
                continue
            picker_rating = pick.get("picker_rating")
            if picker_rating is None:
                game_id = pick.get("game_id")
                picker_rating = picker_rating_map.get(game_id)
                if picker_rating is None:
                    orig = next((p for p in candidate_picks if p.get("game_id") == game_id), None)
                    if orig:
                        picker_rating = orig.get("confidence_score") or orig.get("picker_rating")
            if picker_rating is not None and picker_rating <= 3:
                self.log_warning(
                    f"⚠️  Removing best_bet flag from pick {pick.get('game_id')} "
                    f"due to low confidence (picker_rating={picker_rating} <= 3)"
                )
                pick["best_bet"] = False
        best_bet_picks = [p for p in approved_picks if p.get("best_bet", False)]
        max_best_bets = min(5, len(approved_picks))
        if len(best_bet_picks) > max_best_bets:
            self.log_warning(
                f"⚠️  President selected {len(best_bet_picks)} best bets, max is {max_best_bets}. "
                "Adjusting to top by edge and confidence."
            )
            sorted_best = sorted(
                best_bet_picks,
                key=lambda p: (p.get("edge_estimate", 0), p.get("confidence", 0) if "confidence" in p else 0),
                reverse=True,
            )
            top_ids = {p.get("game_id") for p in sorted_best[:max_best_bets]}
            for pick in approved_picks:
                if pick.get("best_bet", False) and pick.get("game_id") not in top_ids:
                    pick["best_bet"] = False

    def _finalize_daily_summary(
        self,
        approved_picks: List[Dict[str, Any]],
        daily_report_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Ensure daily_report_summary has total_games, total_units, best_bets_count."""
        total_units = sum(p.get("units", 0.0) for p in approved_picks)
        best_bet_count = len([p for p in approved_picks if p.get("best_bet", False)])
        if not daily_report_summary:
            return {
                "total_games": len(approved_picks),
                "total_units": total_units,
                "best_bets_count": best_bet_count,
                "strategic_notes": [],
            }
        daily_report_summary["total_games"] = len(approved_picks)
        daily_report_summary["total_units"] = total_units
        daily_report_summary["best_bets_count"] = best_bet_count
        return daily_report_summary

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
        
        user_prompt = build_president_user_prompt(auditor_feedback)
        
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
            self._apply_pick_safeguards(approved_picks, candidate_picks, minified_picks)
            daily_report_summary = self._finalize_daily_summary(approved_picks, daily_report_summary)
            final_best_bet_count = len([p for p in approved_picks if p.get("best_bet", False)])
            total_units = daily_report_summary.get("total_units", 0.0)
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
