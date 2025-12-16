"""Picker agent for bet selection"""

from typing import List, Optional, Dict, Any

from src.agents.base import BaseAgent
from src.data.models import BetType
from src.data.storage import Database
from src.prompts import PICKER_PROMPT
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
            # Small number of games - process all at once
            result = self._process_batch(
                researcher_output, 
                modeler_output, 
                historical_performance,
                batch_num=1, 
                total_batches=1
            )
            picks = result.get("candidate_picks", [])
            
            # Filter out picks with extreme odds (same logic as batch processing)
            filtered_picks = []
            rejected_count = 0
            for pick in picks:
                odds_str = str(pick.get("odds", "-110"))
                try:
                    odds_int = int(odds_str.replace("+", "").replace("-", ""))
                    if odds_str.startswith("-") and odds_int > 500:
                        rejected_count += 1
                        self.log_warning(
                            f"Rejected pick with extreme odds {odds_str}: {pick.get('selection', 'Unknown')} "
                            f"(game_id: {pick.get('game_id', 'Unknown')})"
                        )
                        continue
                except (ValueError, AttributeError):
                    pass
                
                filtered_picks.append(pick)
            
            if rejected_count > 0:
                self.log_info(f"Filtered out {rejected_count} picks with extreme odds (worse than -500)")
            
            self.log_info(f"Selected {len(filtered_picks)} candidate picks (one per game)")
            
            return {
                "candidate_picks": filtered_picks,
                "overall_strategy_summary": result.get("overall_strategy_summary", [])
            }
        
        # Large number of games - batch process
        self.log_info(f"Selecting picks from {num_games} games using batch processing (batch size: {batch_size})")
        
        all_picks = []
        failed_batches = []
        processed_game_ids = set()
        
        # Create game_id to game_data mappings for efficient lookup
        researcher_by_id = {game.get("game_id"): game for game in researcher_games}
        modeler_by_id = {model.get("game_id"): model for model in modeler_games}
        
        # Use researcher_games as primary source, fallback to modeler_games if needed
        all_game_ids = set(researcher_by_id.keys()) | set(modeler_by_id.keys())
        game_ids_list = list(all_game_ids)
        
        total_batches = (len(game_ids_list) + batch_size - 1) // batch_size
        
        for i in range(0, len(game_ids_list), batch_size):
            batch_game_ids = game_ids_list[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            
            # Build batch data
            batch_researcher_games = [researcher_by_id[gid] for gid in batch_game_ids if gid in researcher_by_id]
            batch_modeler_games = [modeler_by_id[gid] for gid in batch_game_ids if gid in modeler_by_id]
            
            batch_researcher_output = {"games": batch_researcher_games}
            batch_modeler_output = {"game_models": batch_modeler_games}
            
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_game_ids)} games)")
            
            # Process batch with retry
            batch_result = self._process_batch_with_retry(
                batch_researcher_output,
                batch_modeler_output,
                historical_performance,
                batch_num,
                max_retries=2
            )
            
            if batch_result and len(batch_result.get("candidate_picks", [])) > 0:
                batch_picks = batch_result.get("candidate_picks", [])
                all_picks.extend(batch_picks)
                # Track which games were processed
                for pick in batch_picks:
                    game_id = pick.get("game_id")
                    if game_id:
                        processed_game_ids.add(str(game_id))
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_picks)} picks")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate picks")
        
        # Validate all games were processed
        if len(processed_game_ids) < len(game_ids_list):
            missing_count = len(game_ids_list) - len(processed_game_ids)
            self.log_warning(
                f"⚠️  Warning: Only {len(processed_game_ids)}/{len(game_ids_list)} games have picks. "
                f"{missing_count} games missing picks."
            )
        
        if failed_batches:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed, {len(game_ids_list) - len(processed_game_ids)} games missing picks")
        else:
            self.log_info(f"✅ Successfully generated picks for all {len(processed_game_ids)} games")
        
        # Filter out picks with extreme odds
        filtered_picks = []
        rejected_count = 0
        for pick in all_picks:
            odds_str = str(pick.get("odds", "-110"))
            try:
                odds_int = int(odds_str.replace("+", "").replace("-", ""))
                if odds_str.startswith("-") and odds_int > 500:
                    rejected_count += 1
                    self.log_warning(
                        f"Rejected pick with extreme odds {odds_str}: {pick.get('selection', 'Unknown')} "
                        f"(game_id: {pick.get('game_id', 'Unknown')})"
                    )
                    continue
            except (ValueError, AttributeError):
                pass
            
            filtered_picks.append(pick)
        
        if rejected_count > 0:
            self.log_info(f"Filtered out {rejected_count} picks with extreme odds (worse than -500)")
        
        self.log_info(f"Selected {len(filtered_picks)} candidate picks for {num_games} games (one per game)")
        
        return {
            "candidate_picks": filtered_picks,
            "overall_strategy_summary": []  # Could aggregate strategy summaries if needed
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
        
        historical_context = ""
        if historical_performance:
            hp = historical_performance
            historical_context = f"""

HISTORICAL PERFORMANCE (Learn from past results):
- Period: {hp.get('period', 'N/A')}
- Recent Performance: {hp.get('wins', 0)}W-{hp.get('losses', 0)}L-{hp.get('pushes', 0)}P ({hp.get('win_rate', 0):.1f}% win rate)
- ROI: {hp.get('roi', 0):.1f}%
- Total Profit: ${hp.get('total_profit', 0):.2f}
- Bet Type Performance: {hp.get('bet_type_performance', {})}
- Recent Recommendations: {hp.get('recent_recommendations', [])}

Use this historical data to:
- Learn which bet types have been most successful
- Adjust confidence levels based on recent accuracy
- Avoid patterns that led to losses
- Double down on strategies that have been profitable
"""

        user_prompt = f"""Please analyze the research data and model predictions to select betting opportunities.

CRITICAL REQUIREMENT: You MUST generate EXACTLY ONE pick for EVERY game provided. Do not skip any games. Do not generate multiple picks for the same game.

For each game:
- Generate exactly one pick (spread, total, or moneyline) based on model edge and research
- Choose the bet type with the best edge and reasoning for that specific game
- Include clear, detailed justification explaining why this pick was chosen
- The President will review ALL picks, assign units, and select the top 5 best bets

Focus on:
- Positive expected value (edge > 0) when possible
- Reasonable confidence levels
- Clear reasoning that combines model edge with contextual factors
{historical_context}
Provide clear, detailed justification for each pick that explains:
- Why this specific bet type was chosen (spread vs total vs moneyline)
- How the model edge supports this pick
- What contextual factors (injuries, recent form, matchups) influenced the decision
- How historical performance patterns informed this selection"""
        
        # Call LLM directly without complex token counting (Gemini has 1M+ context)
        import json
        from src.agents.base import _make_json_serializable
        
        serializable_data = _make_json_serializable(input_data)
        
        full_user_prompt = f"""{user_prompt}

Input data:
{json.dumps(serializable_data, indent=2)}"""
        
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
