"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta
import re
import json
import hashlib
from pathlib import Path

from src.agents.base import BaseAgent
from src.data.models import Prediction
from src.data.storage import Database
from src.prompts import MODELER_PROMPT
from src.utils.logging import get_logger
from src.utils.json_schemas import get_modeler_schema

logger = get_logger("agents.modeler")


def has_advanced_stats(game_data: Dict[str, Any]) -> bool:
    """
    Check if advanced stats are available for a game
    
    Args:
        game_data: Game data dictionary from researcher output
        
    Returns:
        True if advanced stats are available, False otherwise
    """
    # Check new token-efficient schema first (adv field)
    adv = game_data.get("adv", {})
    
    # Check if data is explicitly marked as unavailable
    if adv.get("data_unavailable", False):
        return False
    
    # Check for key advanced stat fields (AdjO, AdjD, AdjT)
    # These are the critical metrics for confidence
    away_stats = adv.get("away", {})
    home_stats = adv.get("home", {})
    
    # Check if we have at least one team with key metrics
    has_away_stats = any(
        away_stats.get(key) is not None 
        for key in ["adjo", "adjd", "adjt", "net", "kp_rank", "torvik_rank"]
    )
    has_home_stats = any(
        home_stats.get(key) is not None 
        for key in ["adjo", "adjd", "adjt", "net", "kp_rank", "torvik_rank"]
    )
    
    # Need stats for at least one team to consider advanced stats available
    if has_away_stats or has_home_stats:
        return True
    
    # Fallback: check old schema for backward compatibility
    advanced_stats = game_data.get("advanced_stats", {})
    if advanced_stats.get("data_unavailable", False):
        return False
    
    team1_stats = advanced_stats.get("team1", {})
    team2_stats = advanced_stats.get("team2", {})
    
    has_team1_stats = any(
        team1_stats.get(key) is not None 
        for key in ["adjo", "adjd", "adjt", "adj_offense", "adj_defense", "adj_tempo", "kenpom_rank"]
    )
    has_team2_stats = any(
        team2_stats.get(key) is not None 
        for key in ["adjo", "adjd", "adjt", "adj_offense", "adj_defense", "adj_tempo", "kenpom_rank"]
    )
    
    return has_team1_stats or has_team2_stats


class Modeler(BaseAgent):
    """Modeler agent for generating predictions"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Modeler agent"""
        super().__init__("Modeler", db, llm_client)
        # Cache configuration - only store 1 day of modeler output
        self.cache_ttl = timedelta(days=1)  # Cache for 1 day only
        self.cache_file = Path("data/cache/modeler_cache.json")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache = self._load_cache()
        # Clean up old cache entries on initialization
        self._cleanup_old_cache()
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Modeler"""
        return MODELER_PROMPT
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load modeler cache: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Save cache to file"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, default=str, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save modeler cache: {e}")
    
    def _cleanup_old_cache(self) -> None:
        """Remove cache entries older than 1 day"""
        if not self.cache:
            return
        
        now = datetime.now()
        keys_to_remove = []
        
        for key, entry in self.cache.items():
            try:
                cached_time = datetime.fromisoformat(entry.get('timestamp', ''))
                age = now - cached_time
                if age >= self.cache_ttl:
                    keys_to_remove.append(key)
            except Exception:
                # Invalid timestamp, remove entry
                keys_to_remove.append(key)
        
        if keys_to_remove:
            for key in keys_to_remove:
                del self.cache[key]
            self._save_cache()
            logger.debug(f"Cleaned up {len(keys_to_remove)} old modeler cache entries")
    
    def _get_cache_key(self, researcher_output: Dict[str, Any], target_date: Optional[date]) -> str:
        """Generate cache key based on researcher output and date"""
        # Create a stable key from game IDs and date
        games = researcher_output.get("games", [])
        game_ids = sorted([g.get("game_id") for g in games if g.get("game_id")])
        date_str = target_date.isoformat() if target_date else "none"
        key_data = f"{date_str}_{'_'.join(map(str, game_ids))}"
        # Use hash for shorter keys
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _is_cache_valid(self, cache_entry: Dict[str, Any]) -> bool:
        """Check if cache entry is still valid (within 1 day)"""
        try:
            cached_time = datetime.fromisoformat(cache_entry.get('timestamp', ''))
            age = datetime.now() - cached_time
            return age < self.cache_ttl
        except Exception as e:
            logger.debug(f"Invalid cache timestamp format: {e}")
            return False
    
    def _get_cached_predictions(self, researcher_output: Dict[str, Any], target_date: Optional[date]) -> Optional[Dict[str, Any]]:
        """Get cached predictions if available and valid"""
        cache_key = self._get_cache_key(researcher_output, target_date)
        cache_entry = self.cache.get(cache_key)
        
        if cache_entry and self._is_cache_valid(cache_entry):
            age = datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])
            logger.info(f"Using cached modeler predictions (age: {age})")
            return cache_entry.get('predictions')
        return None
    
    def _cache_predictions(self, researcher_output: Dict[str, Any], target_date: Optional[date], predictions: Dict[str, Any]) -> None:
        """Cache predictions for future use"""
        cache_key = self._get_cache_key(researcher_output, target_date)
        games = researcher_output.get("games", [])
        self.cache[cache_key] = {
            'timestamp': datetime.now().isoformat(),
            'predictions': predictions,
            'game_count': len(games),
            'date': target_date.isoformat() if target_date else None
        }
        self._save_cache()
        logger.debug(f"Cached modeler predictions for {len(games)} games")
    
    def process(
        self,
        researcher_output: Dict[str, Any],
        betting_lines: Optional[List] = None,
        historical_data: Optional[Dict[str, Any]] = None,
        target_date: Optional[date] = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Generate predictions using LLM with batch processing
        
        Args:
            researcher_output: Output from Researcher agent (game insights)
            betting_lines: Optional list of BettingLine objects (will be converted to dicts)
            historical_data: Optional historical performance data
            target_date: Target date for predictions
            force_refresh: Force refresh even if cached
            
        Returns:
            LLM response with predictions and edge estimates
        """
        if not self.is_enabled():
            self.log_warning("Modeler agent is disabled")
            return {"game_models": []}
        
        games = researcher_output.get("games", [])
        
        # Check cache first (unless force_refresh is True)
        if not force_refresh:
            cached_predictions = self._get_cached_predictions(researcher_output, target_date)
            if cached_predictions:
                self.log_info(f"Using cached modeler predictions for {len(games)} games")
                return cached_predictions
        
        if force_refresh:
            self.log_info("🔄 Force refresh enabled - bypassing cache")
        
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
        
        # Process games in batches - increased from 3 to 5 for better efficiency
        # Since Picker handles 12 games with both researcher+modeler output, 
        # Modeler can safely handle 5 games (matching Researcher batch size)
        batch_size = self.config.get('modeler_batch_size', 5)  # Process 5 games at a time
        all_game_models = []
        failed_batches = []
        # Track which games have been processed
        processed_game_ids = set()
        
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
                # Track which games were successfully processed
                for model in batch_models:
                    game_id = model.get('game_id')
                    if game_id:
                        processed_game_ids.add(str(game_id))
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_models)} predictions")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate predictions")
        
        # Track which games failed to process (no fallbacks - only primary models)
        missing_games = []
        for game in games:
            game_id = game.get('game_id')
            if game_id is None:
                continue
            try:
                game_id_str = str(int(game_id))
            except (ValueError, TypeError):
                logger.warning(f"Invalid game_id type: {game_id} (type: {type(game_id)})")
                continue
            if game_id_str not in processed_game_ids:
                missing_games.append(game_id_str)
                self.log_warning(f"⚠️  No model generated for game {game_id_str} (LLM processing failed)")
        
        result = {"game_models": all_game_models}
        
        if failed_batches or missing_games:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed, {len(missing_games)} games have no models")
            self.log_warning(f"Total models: {len(all_game_models)}/{len(games)} games (only successfully processed games included)")
        else:
            self.log_info(f"✅ Successfully generated predictions for all {len(all_game_models)} games")
        
        # Warn if we don't have models for all games (expected when some batches fail)
        if len(all_game_models) != len(games):
            self.log_warning(
                f"Game count mismatch: Expected {len(games)} games, got {len(all_game_models)} models. "
                f"Missing {len(missing_games)} games due to processing failures."
            )
        
        # Cache the results (even if incomplete, to avoid re-processing successful batches)
        self._cache_predictions(researcher_output, target_date, result)
        
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
    
    def _dump_debug_info(self, batch_num: int, input_data: Dict[str, Any], response: Any, error: str = None) -> None:
        """Dump debug info to file for troubleshooting"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"data/debug/modeler_batch_{batch_num}_{timestamp}.json"
        try:
            # Create debug directory if it doesn't exist
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            
            debug_data = {
                "batch_num": batch_num,
                "timestamp": timestamp,
                "error": error,
                "input_game_ids": [g.get("game_id") for g in input_data.get("researcher_output", {}).get("games", [])],
                "response_keys": list(response.keys()) if isinstance(response, dict) else str(type(response)),
                "response_content": response
            }
            with open(filename, 'w') as f:
                json.dump(debug_data, f, indent=2, default=str)
            self.log_warning(f"📝 Dumped debug info to {filename}")
        except Exception as e:
            self.log_warning(f"Failed to dump debug info: {e}")

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

REQUIRED OUTPUT FIELDS FOR EACH GAME:
1. predictions: scores, margin, total, win_probs, confidence
2. market_edges: REQUIRED - Calculate and include edges for ALL available betting markets:
   - Spread markets (both home and away sides)
   - Total markets (both over and under)
   - Moneyline markets (both home and away if available)
   - For each edge, provide: market_type, market_line, model_estimated_probability, implied_probability, edge, edge_confidence
3. model_notes: REQUIRED - Provide a detailed, verbose explanation of your modeling process including:
   - Base pace calculation: (Away_AdjT + Home_AdjT) / 2
   - Pace trend adjustments applied
   - Final pace value
   - Efficiency baseline selection (109.0 or 104.0) and reasoning
   - Points per 100 calculations for both teams
   - Any context adjustments (injuries, mismatch penalty, turnstile, elite offense tax)
   - Raw scores and margins before calibration
   - Total calibration vs market (regression if |diff| > 6)
   - Blowout effects if applicable
   - Discrepancy shrinkage factors
   - Confidence tier selection and reasoning
   - Any data quality concerns or limitations

For games with limited data, use lower confidence scores (e.g., 0.3-0.5) and clearly note the data limitations in model_notes.

Be explicit, quantitative, and verbose in model_notes. The model_notes field should read like a detailed report explaining your entire modeling process and rationale."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.4,  # Slightly higher for modeling but still controlled
                parse_json=True,
                response_format=get_modeler_schema()
            )
            
            # Check if response indicates a parsing error
            if response.get("error_type") == "json_decode_error":
                self.log_error(
                    f"JSON parsing failed for Modeler batch response. "
                    f"Error: {response.get('parse_error', 'Unknown')}."
                )
                self.log_debug(f"Raw response: {response.get('raw_response', '')[:500]}")
                self._dump_debug_info(0, input_data, response, error="json_decode_error")
                return []
            
            # Ensure response has expected structure
            if not isinstance(response, dict) or "game_models" not in response:
                self.log_warning(f"Unexpected response structure from Modeler: {response.keys() if isinstance(response, dict) else type(response)}")
                self._dump_debug_info(0, input_data, response, error="unexpected_structure")
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
                
                # Dump debug info for incomplete batches
                self._dump_debug_info(0, input_data, response, error="incomplete_batch")
            
            # CRITICAL: Cap confidence at 0.3 (3/10) for games without advanced stats
            # Normalize game_id to int for consistent matching
            game_data_map = {}
            for g in batch_games:
                game_id = g.get('game_id')
                if game_id is not None:
                    try:
                        game_id_int = int(game_id)
                        game_data_map[game_id_int] = g
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid game_id type: {game_id} (type: {type(game_id)})")
            
            for model in game_models:
                game_id = model.get('game_id')
                try:
                    game_id_int = int(game_id) if game_id is not None else None
                    game_data = game_data_map.get(game_id_int, {}) if game_id_int is not None else {}
                except (ValueError, TypeError):
                    logger.warning(f"Invalid game_id in model response: {game_id} (type: {type(game_id)})")
                    game_data = {}
                
                if not has_advanced_stats(game_data):
                    # Cap all model_confidence values at 0.3
                    predictions = model.get('predictions', {})
                    for pred_type in ['spread', 'total', 'moneyline']:
                        if pred_type in predictions:
                            pred_value = predictions[pred_type]
                            # Handle case where pred_value might be a float instead of dict
                            if isinstance(pred_value, dict):
                                current_confidence = pred_value.get('model_confidence', 1.0)
                                if current_confidence > 0.3:
                                    pred_value['model_confidence'] = 0.3
                                    self.log_warning(
                                        f"⚠️  Capped model_confidence at 0.3 for {pred_type} in game {game_id} "
                                        f"(advanced stats unavailable, was {current_confidence:.2f})"
                                    )
                            else:
                                # If it's not a dict (e.g., a float), convert to dict structure
                                predictions[pred_type] = {
                                    'model_confidence': 0.3,
                                    'value': pred_value
                                }
                                self.log_warning(
                                    f"⚠️  Converted {pred_type} from {type(pred_value).__name__} to dict and "
                                    f"capped model_confidence at 0.3 for game {game_id} (advanced stats unavailable)"
                                )
                    
                    # Also cap edge_confidence in market_edges
                    market_edges = model.get('market_edges', [])
                    for edge in market_edges:
                        current_edge_conf = edge.get('edge_confidence', 1.0)
                        if current_edge_conf > 0.3:
                            edge['edge_confidence'] = 0.3
                    
                    # Add note about advanced stats requirement (only if not already present)
                    model_notes = model.get('model_notes', '')
                    if 'advanced stats unavailable' not in model_notes.lower() and 'confidence capped' not in model_notes.lower():
                        model['model_notes'] = (
                            f"{model_notes} | CRITICAL: Advanced stats unavailable - confidence capped at 0.3 (3/10). "
                            f"Requires AdjO/AdjD/AdjT or KenPom data for higher confidence."
                        ).strip(' |')
            
            # Post-process model_notes to remove redundant "Advanced stats available" messages
            for model in game_models:
                model_notes = model.get('model_notes', '')
                if model_notes:
                    # Remove common redundant phrases
                    redundant_patterns = [
                        r"Advanced stats \(AdjO/AdjD/AdjT/KP rank\) available for both teams\.?\s*",
                        r"Advanced stats \(AdjO/AdjD/AdjT/KenPom rank\) available for both teams\.?\s*",
                        r"Advanced stats available for both teams\.?\s*",
                        r"All advanced stats available\.?\s*",
                        r"Advanced stats \(.*?\) available\.?\s*",
                    ]
                    cleaned_notes = model_notes
                    for pattern in redundant_patterns:
                        cleaned_notes = re.sub(pattern, '', cleaned_notes, flags=re.IGNORECASE)
                    # Clean up extra whitespace and separators
                    cleaned_notes = re.sub(r'\s+\|\s+', ' | ', cleaned_notes)
                    cleaned_notes = re.sub(r'^\|\s+', '', cleaned_notes)
                    cleaned_notes = re.sub(r'\s+\|$', '', cleaned_notes)
                    cleaned_notes = cleaned_notes.strip()
                    if cleaned_notes != model_notes:
                        model['model_notes'] = cleaned_notes
            
            # Transform predictions to expected report format
            for model in game_models:
                self._transform_predictions_format(model)
            
            # CRITICAL: Validate that every model has confidence assigned
            validated_models = []
            for model in game_models:
                game_id = model.get('game_id', 'unknown')
                predictions = model.get('predictions', {})
                
                # Check all possible locations for confidence
                confidence = predictions.get('confidence')
                if confidence is None:
                    spread_data = predictions.get('spread', {})
                    if isinstance(spread_data, dict):
                        confidence = spread_data.get('model_confidence')
                
                if confidence is None:
                    total_data = predictions.get('total', {})
                    if isinstance(total_data, dict):
                        confidence = total_data.get('model_confidence')
                
                if confidence is None:
                    moneyline_data = predictions.get('moneyline', {})
                    if isinstance(moneyline_data, dict):
                        confidence = moneyline_data.get('model_confidence')
                
                if confidence is None:
                    self.log_error(
                        f"CRITICAL: Model for game_id={game_id} is missing confidence. "
                        f"Expected in predictions.confidence or predictions.{{spread|total|moneyline}}.model_confidence. "
                        f"Rejecting this model - LLM must provide confidence for all predictions."
                    )
                    continue  # Skip this model - confidence is required
                
                # Validate that market_edges and model_notes are present (warn if missing)
                market_edges = model.get('market_edges', [])
                if not market_edges:
                    self.log_warning(
                        f"⚠️  Model for game_id={game_id} is missing market_edges. "
                        f"This field is required for verbose reporting. LLM should calculate edges for all available betting markets."
                    )
                
                model_notes = model.get('model_notes', '')
                if not model_notes or len(model_notes.strip()) < 50:
                    self.log_warning(
                        f"⚠️  Model for game_id={game_id} has missing or insufficient model_notes "
                        f"(length: {len(model_notes) if model_notes else 0}). "
                        f"This field is required for verbose reporting and should explain the modeling rationale in detail."
                    )
                
                validated_models.append(model)
            
            if len(validated_models) < len(game_models):
                self.log_warning(
                    f"Rejected {len(game_models) - len(validated_models)} model(s) due to missing confidence. "
                    f"Only {len(validated_models)}/{len(game_models)} models passed validation."
                )
            
            return validated_models
            
        except Exception as e:
            self.log_error(f"Error in batch LLM modeling: {e}", exc_info=True)
            return []
    
    def _transform_predictions_format(self, model: Dict[str, Any]) -> None:
        """Normalize predictions to provide both flat and nested formats."""
        if not model:
            return

        predictions = model.get("predictions", {})
        if not predictions:
            return

        # 1. Handle Spread/Margin
        if "spread" not in predictions:
            predictions["spread"] = {}
        
        # Move top-level margin to spread.projected_margin
        if "margin" in predictions:
            predictions["spread"]["projected_margin"] = predictions["margin"]
            
        # Ensure confidence is in spread if not already there
        if "confidence" in predictions and "model_confidence" not in predictions["spread"]:
            predictions["spread"]["model_confidence"] = predictions["confidence"]

        # 2. Handle Scores and Total
        scores = predictions.get("scores", {})
        if scores and "away" in scores and "home" in scores:
            try:
                away_score = float(scores["away"])
                home_score = float(scores["home"])
                calculated_total = away_score + home_score
                
                # Ensure predicted_score exists at top level (for report generator)
                if "predicted_score" not in model:
                    model["predicted_score"] = {
                        "away_score": away_score,
                        "home_score": home_score
                    }
                
                # Update total prediction
                if "total" not in predictions:
                    predictions["total"] = {}
                
                # If total is a dict (expected)
                if isinstance(predictions["total"], dict):
                    if "projected_total" not in predictions["total"]:
                        predictions["total"]["projected_total"] = calculated_total
                    if "model_confidence" not in predictions["total"] and "confidence" in predictions:
                        predictions["total"]["model_confidence"] = predictions["confidence"]
            except (ValueError, TypeError):
                pass

        # 3. Handle Moneyline/Win Probs
        if "moneyline" not in predictions:
            predictions["moneyline"] = {}
            
        win_probs = predictions.get("win_probs", {})
        if win_probs:
            predictions["moneyline"]["away_win_prob"] = win_probs.get("away")
            predictions["moneyline"]["home_win_prob"] = win_probs.get("home")
            
        if "confidence" in predictions and "model_confidence" not in predictions["moneyline"]:
            predictions["moneyline"]["model_confidence"] = predictions["confidence"]
    