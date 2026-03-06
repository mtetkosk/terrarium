"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional, Dict, Any, Tuple, Set
from datetime import date, datetime, timedelta
import json
import hashlib
from pathlib import Path

from src.agents.base import BaseAgent
from src.agents.modeler_engine import GameContext, calculate_game_model
from src.agents.modeler_notes import (
    build_model_notes_context,
    format_model_notes,
)
from src.agents.modeler_validation import validate_score_team_consistency
from src.data.models import Prediction
from src.data.storage import Database
from src.prompts import MODELER_PROMPT, MODEL_NOTES_PROMPT
from src.utils.logging import get_logger
from src.utils.json_schemas import get_modeler_schema

logger = get_logger("agents.modeler")


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

    def _prepare_betting_lines(self, betting_lines: Optional[List]) -> Dict[Any, List[Dict[str, Any]]]:
        """Convert betting lines to dicts and return lines_by_game. Returns {} if None or empty."""
        if not betting_lines:
            return {}
        betting_lines_dict = []
        for line in betting_lines:
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
        lines_by_game: Dict[Any, List[Dict[str, Any]]] = {}
        for line in betting_lines_dict:
            game_id = line.get("game_id")
            if game_id not in lines_by_game:
                lines_by_game[game_id] = []
            lines_by_game[game_id].append(line)
        return lines_by_game

    def _process_all_batches(
        self,
        games: List[Dict[str, Any]],
        lines_by_game: Dict[Any, List[Dict[str, Any]]],
        historical_data: Optional[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Set[str], List[int]]:
        """Process games in batches; returns (all_game_models, processed_game_ids, failed_batches)."""
        batch_size = self.config.get('modeler_batch_size', 5)
        all_game_models: List[Dict[str, Any]] = []
        processed_game_ids: Set[str] = set()
        failed_batches: List[int] = []
        total_batches = (len(games) + batch_size - 1) // batch_size

        for i in range(0, len(games), batch_size):
            batch_games = games[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_games)} games)")
            batch_lines = []
            for game in batch_games:
                game_id = game.get('game_id')
                if game_id in lines_by_game:
                    batch_lines.extend(lines_by_game[game_id])
            batch_models = self._process_batch_with_retry(
                batch_games, batch_lines, historical_data, batch_num, max_retries=2
            )
            if batch_models and len(batch_models) > 0:
                all_game_models.extend(batch_models)
                for model in batch_models:
                    gid = model.get('game_id')
                    if gid:
                        processed_game_ids.add(str(gid))
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_models)} predictions")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate predictions")
        return (all_game_models, processed_game_ids, failed_batches)

    def _missing_game_ids(self, games: List[Dict[str, Any]], processed_game_ids: Set[str]) -> List[str]:
        """Return list of game IDs that were not in processed_game_ids."""
        missing = []
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
                missing.append(game_id_str)
        return missing

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

        if not force_refresh:
            cached_predictions = self._get_cached_predictions(researcher_output, target_date)
            if cached_predictions:
                self.log_info(f"Using cached modeler predictions for {len(games)} games")
                return cached_predictions

        if force_refresh:
            self.log_info("🔄 Force refresh enabled - bypassing cache")

        self.log_info(f"Modeling {len(games)} games using LLM (batch processing)")

        lines_by_game = self._prepare_betting_lines(betting_lines)
        all_game_models, processed_game_ids, failed_batches = self._process_all_batches(
            games, lines_by_game, historical_data
        )
        missing_games = self._missing_game_ids(games, processed_game_ids)

        for game_id_str in missing_games:
            self.log_warning(f"⚠️  No model generated for game {game_id_str} (LLM processing failed)")

        result = {"game_models": all_game_models}

        if failed_batches or missing_games:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed, {len(missing_games)} games have no models")
            self.log_warning(f"Total models: {len(all_game_models)}/{len(games)} games (only successfully processed games included)")
        else:
            self.log_info(f"✅ Successfully generated predictions for all {len(all_game_models)} games")

        if len(all_game_models) != len(games):
            self.log_warning(
                f"Game count mismatch: Expected {len(games)} games, got {len(all_game_models)} models. "
                f"Missing {len(missing_games)} games due to processing failures."
            )

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

    def _process_one_game(
        self,
        game: Dict[str, Any],
        batch_lines: List[Dict[str, Any]],
        game_ctx: GameContext,
    ) -> Optional[Dict[str, Any]]:
        """Process a single game: run model, attach notes, transform format, validate. Returns model dict or None on error."""
        game_id = game.get("game_id")
        game_lines = [line for line in batch_lines if str(line.get("game_id")) == str(game_id)]
        try:
            model = calculate_game_model(game_ctx, game_lines, has_adv_stats=True)
            model["model_notes"] = self._generate_model_notes(game_ctx, model)
            self._transform_predictions_format(model)
            validation_result = validate_score_team_consistency(model, game_ctx, game)
            if not validation_result["valid"]:
                self.log_error(
                    f"⚠️  SCORE INVERSION WARNING for game {game_id}: "
                    f"{validation_result.get('warning', 'Unknown issue')}. "
                    f"Details: {validation_result.get('details', '')}."
                )
                model_notes = model.get("model_notes", "")
                model["model_notes"] = (
                    f"{model_notes} | VALIDATION WARNING: {validation_result.get('warning', '')}"
                ).strip(" |")
            return model
        except Exception as e:
            self.log_error(f"Error modeling game {game_id}: {e}", exc_info=True)
            return None

    def _process_batch(
        self,
        batch_games: List[Dict[str, Any]],
        batch_lines: List[Dict[str, Any]],
        historical_data: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Process a single batch of games deterministically."""
        validated_models: List[Dict[str, Any]] = []
        for game in batch_games:
            game_id = game.get("game_id")
            ctx = GameContext.from_researcher_output(game)
            if not ctx:
                self.log_warning(f"⚠️  Skipping game {game_id}: missing advanced stats (AdjO/AdjD/AdjT).")
                continue
            model = self._process_one_game(game, batch_lines, ctx)
            if model is not None:
                validated_models.append(model)
        return validated_models
    
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

    def _generate_model_notes(self, game_ctx: GameContext, model: Dict[str, Any]) -> str:
        """Generate detailed model notes matching agentic modeler format."""
        notes_ctx = build_model_notes_context(game_ctx, model)
        return format_model_notes(notes_ctx)
    