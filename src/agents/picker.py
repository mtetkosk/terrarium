"""Picker agent for bet selection"""

from typing import List, Optional, Dict, Any

from src.agents.base import BaseAgent
from src.data.models import BetType
from src.data.storage import Database
from src.prompts import PICKER_PROMPT, build_picker_user_prompt
from src.utils.logging import get_logger
from src.utils.json_schemas import get_picker_schema

logger = get_logger("agents.picker")


class Picker(BaseAgent):
    """Picker agent for selecting bets"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Picker agent"""
        super().__init__("Picker", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Picker"""
        return PICKER_PROMPT

    def _filter_extreme_odds(self, picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out picks with extreme odds (e.g. -500 favorites, +300 ML). Returns filtered list."""
        filtered = []
        rejected = 0
        for pick in picks:
            odds_str = str(pick.get("odds", "-110"))
            selection = str(pick.get("selection", "")).lower()
            is_ml = "ml" in selection or "moneyline" in selection
            try:
                odds_int = int(odds_str.replace("+", "").replace("-", ""))
                if odds_str.startswith("-") and odds_int > 500:
                    rejected += 1
                    self.log_warning(
                        f"Rejected pick with extreme odds {odds_str}: {pick.get('selection', 'Unknown')} "
                        f"(game_id: {pick.get('game_id', 'Unknown')})"
                    )
                    continue
                if is_ml and odds_str.startswith("+") and odds_int > 300:
                    rejected += 1
                    self.log_warning(
                        f"Rejected ML pick with high odds {odds_str}: {pick.get('selection', 'Unknown')} "
                        f"(game_id: {pick.get('game_id', 'Unknown')}) - ML bets over +300 are too risky"
                    )
                    continue
                filtered.append(pick)
            except (ValueError, AttributeError):
                self.log_warning(
                    f"Skipped pick with unparseable odds '{odds_str}': {pick.get('selection', 'Unknown')} "
                    f"(game_id: {pick.get('game_id', 'Unknown')})"
                )
        if rejected > 0:
            self.log_info(f"Filtered out {rejected} picks with extreme odds")
        return filtered

    def _merge_batch_picks(
        self,
        researcher_games: List[Dict[str, Any]],
        modeler_games: List[Dict[str, Any]],
        batch_size: int,
        historical_performance: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Run batch processing and return merged filtered picks."""
        researcher_by_id = {g.get("game_id"): g for g in researcher_games}
        modeler_by_id = {m.get("game_id"): m for m in modeler_games}
        game_ids_list = list(set(researcher_by_id.keys()) | set(modeler_by_id.keys()))
        total_batches = (len(game_ids_list) + batch_size - 1) // batch_size
        all_picks = []
        for i in range(0, len(game_ids_list), batch_size):
            batch_ids = game_ids_list[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            batch_researcher = {"games": [researcher_by_id[gid] for gid in batch_ids if gid in researcher_by_id]}
            batch_modeler = {"game_models": [modeler_by_id[gid] for gid in batch_ids if gid in modeler_by_id]}
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_ids)} games)")
            result = self._process_batch_with_retry(
                batch_researcher, batch_modeler, historical_performance, batch_num, max_retries=2
            )
            if result and result.get("candidate_picks"):
                all_picks.extend(result["candidate_picks"])
                self.log_info(f"✅ Batch {batch_num} completed: {len(result['candidate_picks'])} picks")
            else:
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate picks")
        return self._filter_extreme_odds(all_picks)

    def process(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Select picks using LLM - exactly one pick per game
        
        Args:
            researcher_output: Output from Researcher
            modeler_output: Output from Modeler (predictions and edges)
            historical_performance: Historical performance data for learning
            
        Returns:
            LLM response with candidate picks (one per game)
        """
        if not self.is_enabled():
            self.log_warning("Picker agent is disabled")
            return {"candidate_picks": []}
        
        # Get games from researcher_output or modeler_output
        researcher_games = researcher_output.get("games", [])
        modeler_games = modeler_output.get("game_models", [])
        num_games = len(researcher_games) if researcher_games else len(modeler_games)
        
        # Batch processing: split games into smaller chunks to avoid huge prompts
        # Similar to Researcher (batch_size=5) and Modeler (batch_size=3)
        # Use larger batch size (10-15) since picks are simpler than research/modeling
        batch_size = self.config.get('picker_batch_size', 12)  # Process 12 games at a time
        
        if num_games <= batch_size:
            result = self._process_batch(
                researcher_output,
                modeler_output,
                historical_performance,
                batch_num=1,
                total_batches=1,
            )
            filtered_picks = self._filter_extreme_odds(result.get("candidate_picks", []))
            self.log_info(f"Selected {len(filtered_picks)} candidate picks (one per game)")
            return {
                "candidate_picks": filtered_picks,
                "overall_strategy_summary": result.get("overall_strategy_summary", []),
            }

        self.log_info(f"Selecting picks from {num_games} games using batch processing (batch size: {batch_size})")
        filtered_picks = self._merge_batch_picks(
            researcher_games, modeler_games, batch_size, historical_performance
        )
        self.log_info(f"Selected {len(filtered_picks)} candidate picks for {num_games} games (one per game)")
        return {
            "candidate_picks": filtered_picks,
            "overall_strategy_summary": [],
        }
    
    def _process_batch_with_retry(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]],
        batch_num: int,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """Process a batch of games with retry mechanism"""
        for attempt in range(max_retries + 1):
            try:
                batch_result = self._process_batch(
                    researcher_output,
                    modeler_output,
                    historical_performance,
                    batch_num,
                    1  # total_batches not needed for individual batch
                )
                if batch_result and len(batch_result.get("candidate_picks", [])) > 0:
                    return batch_result
                elif attempt < max_retries:
                    self.log_warning(f"Batch {batch_num} attempt {attempt + 1} returned no picks, retrying...")
            except Exception as e:
                if attempt < max_retries:
                    self.log_warning(f"Batch {batch_num} attempt {attempt + 1} failed: {e}, retrying...")
                else:
                    self.log_error(f"Batch {batch_num} failed after {max_retries + 1} attempts: {e}", exc_info=True)
        
        return {"candidate_picks": []}
    
    def _process_batch(
        self,
        researcher_output: Dict[str, Any],
        modeler_output: Dict[str, Any],
        historical_performance: Optional[Dict[str, Any]],
        batch_num: int,
        total_batches: int
    ) -> Dict[str, Any]:
        """Process a single batch of games - generates exactly one pick per game"""
        
        # Prepare input for LLM
        input_data = {
            "researcher_output": researcher_output,
            "modeler_output": modeler_output,
            "historical_performance": historical_performance or {}
        }
        
        num_games = len(researcher_output.get("games", []))
        if num_games == 0:
            num_games = len(modeler_output.get("game_models", []))
        
        from src.agents.base import _make_json_serializable

        serializable_data = _make_json_serializable(input_data)
        full_user_prompt = build_picker_user_prompt(historical_performance, serializable_data)
        
        self.log_info(f"Calling LLM for batch {batch_num} ({num_games} games)")
        
        # Get usage stats before call
        usage_before = self.llm_client.get_usage_stats()
        
        response = self.llm_client.call(
            system_prompt=self.system_prompt,
            user_prompt=full_user_prompt,
            temperature=0.5,
            parse_json=True,
            response_format=get_picker_schema(),
            max_tokens=8192  # Gemini max output tokens
        )
        
        # Log usage
        usage_after = self.llm_client.get_usage_stats()
        tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
        if tokens_used > 0:
             self.log_info(f"💰 Picker batch {batch_num} token usage: {tokens_used:,}")
        
        picks = response.get("candidate_picks", [])
        
        # Validate batch results
        if len(picks) < num_games:
            missing_count = num_games - len(picks)
            self.log_warning(
                f"⚠️  Batch {batch_num}: Only {len(picks)} picks generated for {num_games} games. "
                f"{missing_count} games missing picks."
            )
        
        response["candidate_picks"] = picks
        return response
