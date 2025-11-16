"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import Prediction
from src.data.storage import Database
from src.prompts import MODELER_PROMPT
from src.utils.logging import get_logger

logger = get_logger("agents.modeler")


class Modeler(BaseAgent):
    """Modeler agent for generating predictions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Modeler agent"""
        super().__init__("Modeler", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Modeler"""
        return MODELER_PROMPT
    
    def process(
        self,
        researcher_output: Dict[str, Any],
        betting_lines: Optional[List] = None,
        historical_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate predictions using LLM with batch processing
        
        Args:
            researcher_output: Output from Researcher agent (game insights)
            betting_lines: Optional list of BettingLine objects (will be converted to dicts)
            historical_data: Optional historical performance data
            
        Returns:
            LLM response with predictions and edge estimates
        """
        if not self.is_enabled():
            self.log_warning("Modeler agent is disabled")
            return {"game_models": []}
        
        games = researcher_output.get("games", [])
        self.log_info(f"Modeling {len(games)} games using LLM (batch processing)")
        
        # Convert betting lines to JSON-serializable format if provided
        betting_lines_dict = None
        if betting_lines:
            betting_lines_dict = []
            for line in betting_lines:
                # Convert BettingLine dataclass to dict
                line_dict = {
                    "game_id": line.game_id,
                    "book": line.book,
                    "bet_type": line.bet_type.value if hasattr(line.bet_type, 'value') else str(line.bet_type),
                    "line": line.line,
                    "odds": line.odds,
                    "id": line.id,
                    "timestamp": line.timestamp.isoformat() if hasattr(line.timestamp, 'isoformat') else str(line.timestamp)
                }
                betting_lines_dict.append(line_dict)
        
        # Create a map of game_id to betting lines for quick lookup
        lines_by_game = {}
        if betting_lines_dict:
            for line in betting_lines_dict:
                game_id = line.get("game_id")
                if game_id not in lines_by_game:
                    lines_by_game[game_id] = []
                lines_by_game[game_id].append(line)
        
        # Process games in batches of 2-3
        batch_size = 3  # Process 3 games at a time
        all_game_models = []
        failed_batches = []
        
        for i in range(0, len(games), batch_size):
            batch_games = games[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(games) + batch_size - 1) // batch_size
            
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_games)} games)")
            
            # Get betting lines for this batch
            batch_lines = []
            for game in batch_games:
                game_id = game.get('game_id')
                if game_id in lines_by_game:
                    batch_lines.extend(lines_by_game[game_id])
            
            # Process batch with retry
            batch_models = self._process_batch_with_retry(
                batch_games,
                batch_lines,
                historical_data,
                batch_num,
                max_retries=2
            )
            
            if batch_models and len(batch_models) > 0:
                all_game_models.extend(batch_models)
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_models)} predictions")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate predictions")
        
        result = {"game_models": all_game_models}
        
        if failed_batches:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed: {failed_batches}")
            self.log_warning(f"Generated predictions for {len(all_game_models)}/{len(games)} games")
        else:
            self.log_info(f"✅ Successfully generated predictions for all {len(all_game_models)} games")
        
        return result
    
    def _process_batch_with_retry(
        self,
        batch_games: List[Dict[str, Any]],
        batch_lines: List[Dict[str, Any]],
        historical_data: Optional[Dict[str, Any]],
        batch_num: int,
        max_retries: int = 2
    ) -> List[Dict[str, Any]]:
        """Process a batch of games with retry mechanism"""
        for attempt in range(max_retries + 1):
            try:
                batch_result = self._process_batch(batch_games, batch_lines, historical_data)
                if batch_result and len(batch_result) > 0:
                    return batch_result
                else:
                    if attempt < max_retries:
                        self.log_warning(f"⚠️  Batch {batch_num} attempt {attempt + 1} returned empty results, retrying...")
                    else:
                        self.log_error(f"❌ Batch {batch_num} failed after {max_retries + 1} attempts")
            except Exception as e:
                if attempt < max_retries:
                    self.log_warning(f"⚠️  Batch {batch_num} attempt {attempt + 1} failed with error: {e}, retrying...")
                else:
                    self.log_error(f"❌ Batch {batch_num} failed after {max_retries + 1} attempts: {e}")
        
        return []
    
    def _process_batch(
        self,
        batch_games: List[Dict[str, Any]],
        batch_lines: List[Dict[str, Any]],
        historical_data: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Process a single batch of games"""
        # Prepare input for this batch
        batch_researcher_output = {"games": batch_games}
        
        input_data = {
            "researcher_output": batch_researcher_output,
            "betting_lines": batch_lines,
            "historical_data": historical_data or {}
        }
        
        num_games = len(batch_games)
        user_prompt = f"""Please analyze the game data and generate predictions for ALL {num_games} games in this batch.

CRITICAL: You MUST return predictions for every single game in the input data. The response must include exactly {num_games} entries in the "game_models" array, one for each game_id provided. Do not skip any games, even if:
- Data is limited or missing
- Confidence is low
- The game seems less important

For games with limited data, use lower confidence scores (e.g., 0.3-0.5) and clearly note the data limitations in model_notes.

For each game, provide:
- Win probabilities for each side
- Expected scoring margin (for spreads)
- Expected total points (for totals)
- Market edges (your probability vs implied probability from odds)
- Confidence levels based on data quality

Be explicit, quantitative, and cautious. If data is thin or noisy, lower your confidence and explain why in model_notes."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.4,  # Slightly higher for modeling but still controlled
                parse_json=True
            )
            
            # Check if response indicates a parsing error
            if response.get("error_type") == "json_decode_error":
                self.log_error(
                    f"JSON parsing failed for Modeler batch response. "
                    f"Error: {response.get('parse_error', 'Unknown')}."
                )
                self.log_debug(f"Raw response: {response.get('raw_response', '')[:500]}")
                return []
            
            # Ensure response has expected structure
            if not isinstance(response, dict) or "game_models" not in response:
                self.log_warning(f"Unexpected response structure from Modeler: {response.keys() if isinstance(response, dict) else type(response)}")
                return []
            
            game_models = response.get('game_models', [])
            num_expected = len(batch_games)
            num_generated = len(game_models)
            
            if num_generated < num_expected:
                self.log_warning(
                    f"⚠️  Batch only generated {num_generated} predictions for {num_expected} games. "
                    f"Missing {num_expected - num_generated} game(s)."
                )
                # Log which game_ids are missing
                expected_ids = {g.get('game_id') for g in batch_games if g.get('game_id')}
                generated_ids = {gm.get('game_id') for gm in game_models if gm.get('game_id')}
                missing_ids = expected_ids - generated_ids
                if missing_ids:
                    self.log_warning(f"Missing game_ids in batch: {sorted(missing_ids)}")
            
            return game_models
            
        except Exception as e:
            self.log_error(f"Error in batch LLM modeling: {e}", exc_info=True)
            return []
