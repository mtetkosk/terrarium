"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime, timedelta
import re
import json
import hashlib
from pathlib import Path

from src.agents.base import BaseAgent
from src.agents.modeler_engine import (
    GameStats,
    TeamAdvancedStats,
    calculate_game_model,
    EFF_BASELINE,
)
from src.data.models import Prediction
from src.data.storage import Database
from src.prompts import MODELER_PROMPT, MODEL_NOTES_PROMPT
from src.utils.logging import get_logger
from src.utils.json_schemas import get_modeler_schema

logger = get_logger("agents.modeler")


def validate_score_team_consistency(
    model: Dict[str, Any],
    game_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Validate that model scores are correctly assigned to teams using team IDs.
    
    Detects potential score inversion bugs where away/home scores are swapped.
    Uses team IDs for authoritative team identification when available.
    
    Args:
        model: The model output with predictions
        game_data: The input game data with team info and market data
        
    Returns:
        Dict with validation results: {
            'valid': bool,
            'warning': str or None,
            'corrected': bool,
            'details': str
        }
    """
    result = {'valid': True, 'warning': None, 'corrected': False, 'details': ''}
    
    try:
        predictions = model.get('predictions', {})
        scores = predictions.get('scores', {})
        margin = predictions.get('margin')
        model_teams = model.get('teams', {})
        input_teams = game_data.get('teams', {})
        game_id = model.get('game_id', 'unknown')
        
        if not scores or margin is None:
            return result
        
        away_score = scores.get('away')
        home_score = scores.get('home')
        
        if away_score is None or home_score is None:
            return result
        
        # Check 0: Verify team IDs match between input and output
        input_away_id = input_teams.get('away_id')
        input_home_id = input_teams.get('home_id')
        output_away_id = model_teams.get('away_id')
        output_home_id = model_teams.get('home_id')
        
        if input_away_id is not None and output_away_id is not None:
            if input_away_id != output_away_id:
                result['warning'] = (
                    f"TEAM ID MISMATCH: Input away_id={input_away_id} but output away_id={output_away_id}. "
                    f"This indicates a team assignment error."
                )
                result['valid'] = False
                logger.error(f"Game {game_id}: {result['warning']}")
        
        if input_home_id is not None and output_home_id is not None:
            if input_home_id != output_home_id:
                result['warning'] = (
                    f"TEAM ID MISMATCH: Input home_id={input_home_id} but output home_id={output_home_id}. "
                    f"This indicates a team assignment error."
                )
                result['valid'] = False
                logger.error(f"Game {game_id}: {result['warning']}")
        
        # Check 1: Verify margin = home_score - away_score
        calculated_margin = home_score - away_score
        margin_mismatch = abs(calculated_margin - margin) > 0.5
        
        if margin_mismatch:
            result['warning'] = (
                f"MARGIN MISMATCH: margin={margin} but home({home_score})-away({away_score})={calculated_margin:.1f}. "
                f"This may indicate a score inversion bug."
            )
            result['valid'] = False
            logger.error(f"Game {game_id}: {result['warning']}")
        
        # Check 2: Compare with market data for sanity check
        market = game_data.get('market', {})
        market_spread = market.get('spread')
        
        if market_spread and isinstance(market_spread, str):
            # Parse market spread to see if our margin agrees directionally
            # e.g., "stanford +8.5" means home team is underdog (away favored)
            away_team = input_teams.get('away', '').lower()
            home_team = input_teams.get('home', '').lower()
            
            spread_lower = market_spread.lower()
            
            # Try to determine market favorite
            market_home_favored = None
            if '+' in spread_lower:
                # Team with + is the underdog
                if home_team and home_team in spread_lower.split('+')[0]:
                    market_home_favored = False  # Home is underdog
                elif away_team and away_team in spread_lower.split('+')[0]:
                    market_home_favored = True  # Away is underdog, so home favored
            elif '-' in spread_lower and not spread_lower.startswith('-'):
                # Team with - is the favorite
                parts = spread_lower.split('-')
                if len(parts) >= 2:
                    team_part = parts[0].strip()
                    if home_team and home_team in team_part:
                        market_home_favored = True
                    elif away_team and away_team in team_part:
                        market_home_favored = False
            
            # If market strongly favors one team but model says opposite with large margin, flag it
            if market_home_favored is not None and abs(margin) > 8:
                model_home_favored = margin > 0
                if market_home_favored != model_home_favored:
                    result['details'] = (
                        f"DIRECTIONAL MISMATCH (POSSIBLE INVERSION): "
                        f"Market ({market_spread}) suggests {'home' if market_home_favored else 'away'} favored, "
                        f"but model margin={margin:.1f} suggests {'home' if model_home_favored else 'away'} favored. "
                        f"Team IDs: away={input_away_id} ({away_team}), home={input_home_id} ({home_team}). "
                        f"VERIFY team assignments are correct!"
                    )
                    logger.warning(f"Game {game_id}: {result['details']}")
        
        # Check 3: If win_probs exist, verify they're consistent with margin
        win_probs = predictions.get('win_probs', {})
        if win_probs:
            away_prob = win_probs.get('away', 0)
            home_prob = win_probs.get('home', 0)
            
            # If margin is positive (home wins), home_prob should be > 0.5
            # If margin is negative (away wins), away_prob should be > 0.5
            if margin > 2 and away_prob > home_prob:
                result['warning'] = (
                    f"WIN PROB INCONSISTENCY: margin={margin:.1f} (home wins) but "
                    f"away_prob={away_prob:.2f} > home_prob={home_prob:.2f}. "
                    f"Possible score inversion. Team IDs: away={input_away_id}, home={input_home_id}"
                )
                result['valid'] = False
                logger.error(f"Game {game_id}: {result['warning']}")
            elif margin < -2 and home_prob > away_prob:
                result['warning'] = (
                    f"WIN PROB INCONSISTENCY: margin={margin:.1f} (away wins) but "
                    f"home_prob={home_prob:.2f} > away_prob={away_prob:.2f}. "
                    f"Possible score inversion. Team IDs: away={input_away_id}, home={input_home_id}"
                )
                result['valid'] = False
                logger.error(f"Game {game_id}: {result['warning']}")
    
    except Exception as e:
        logger.error(f"Error validating score consistency for game {model.get('game_id', 'unknown')}: {e}")
    
    return result


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


def _get_adv_values(team_data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Extract AdjO/AdjD/AdjT from a team stats dict with flexible keys."""
    if not isinstance(team_data, dict):
        return None, None, None
    adjo = team_data.get("adjo") or team_data.get("adj_o") or team_data.get("adj_offense")
    adjd = team_data.get("adjd") or team_data.get("adj_d") or team_data.get("adj_defense")
    adjt = team_data.get("adjt") or team_data.get("adj_t") or team_data.get("adj_tempo")
    return adjo, adjd, adjt


def _parse_market_spread(game_data: Dict[str, Any]) -> Optional[float]:
    """
    Parse market spread string into home spread (home minus points).
    Example: "Duke -7.5" when Duke is home -> returns -7.5.
    """
    market = game_data.get("market", {})
    spread_val = market.get("spread")
    if spread_val is None:
        return None
    if isinstance(spread_val, (int, float)):
        return float(spread_val)
    if not isinstance(spread_val, str):
        return None

    home_name = str(game_data.get("teams", {}).get("home", "")).lower()
    away_name = str(game_data.get("teams", {}).get("away", "")).lower()

    # Extract numeric part
    num_match = re.search(r"[-+]?\d+(\.\d+)?", spread_val)
    if not num_match:
        return None
    try:
        line_val = float(num_match.group())
    except ValueError:
        return None

    lower = spread_val.lower()
    if home_name and home_name in lower:
        # Home listed in string; keep sign as-is
        return line_val
    if away_name and away_name in lower:
        # Away listed; invert to represent home perspective
        return -line_val
    # Fallback: assume negative means home favored
    return line_val


def _extract_game_stats(game_data: Dict[str, Any]) -> Optional[GameStats]:
    """Build GameStats from researcher output; returns None if required stats missing."""
    adv = game_data.get("adv", {})
    away_adv = adv.get("away", {}) if isinstance(adv, dict) else {}
    home_adv = adv.get("home", {}) if isinstance(adv, dict) else {}

    # Fallback to legacy advanced_stats schema
    if not away_adv or not home_adv:
        legacy = game_data.get("advanced_stats", {})
        away_adv = away_adv or legacy.get("team2", {}) or {}
        home_adv = home_adv or legacy.get("team1", {}) or {}

    away_adjo, away_adjd, away_adjt = _get_adv_values(away_adv)
    home_adjo, home_adjd, home_adjt = _get_adv_values(home_adv)

    if any(v is None for v in [away_adjo, away_adjd, away_adjt, home_adjo, home_adjd, home_adjt]):
        return None

    recent = game_data.get("recent", {})
    away_trend = (recent.get("away", {}) or {}).get("pace_trend")
    home_trend = (recent.get("home", {}) or {}).get("pace_trend")
    
    # Extract conference information for mismatch adjustment
    away_conference = away_adv.get("conference")
    home_conference = home_adv.get("conference")

    market_total = None
    market = game_data.get("market", {})
    if isinstance(market, dict):
        total_val = market.get("total")
        if isinstance(total_val, (int, float)):
            market_total = float(total_val)

    market_spread_home = _parse_market_spread(game_data)

    # Extract neutral site and rivalry info from context
    is_neutral = False
    is_rivalry = False
    context = game_data.get("context", [])
    if isinstance(context, list):
        for ctx_item in context:
            if isinstance(ctx_item, str):
                lower = ctx_item.lower()
                if "neutral site" in lower:
                    is_neutral = True
                if "rivalry" in lower:
                    is_rivalry = True

    return GameStats(
        away=TeamAdvancedStats(
            adjo=float(away_adjo),
            adjd=float(away_adjd),
            adjt=float(away_adjt),
            pace_trend=away_trend,
            conference=away_conference,
        ),
        home=TeamAdvancedStats(
            adjo=float(home_adjo),
            adjd=float(home_adjd),
            adjt=float(home_adjt),
            pace_trend=home_trend,
            conference=home_conference,
        ),
        market_total=market_total,
        market_spread_home=market_spread_home,
        is_neutral_site=is_neutral,
        is_rivalry=is_rivalry,
    )


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
        """Process a single batch of games deterministically."""
        validated_models: List[Dict[str, Any]] = []

        for game in batch_games:
            game_id = game.get("game_id")
            stats = _extract_game_stats(game)
            if not stats:
                self.log_warning(f"⚠️  Skipping game {game_id}: missing advanced stats (AdjO/AdjD/AdjT).")
                continue

            # Filter betting lines for this game_id
            game_lines = [l for l in batch_lines if str(l.get("game_id")) == str(game_id)]

            try:
                model = calculate_game_model(game, stats, game_lines, has_adv_stats=True)
                # Attach model notes (LLM or template fallback)
                model["model_notes"] = self._generate_model_notes(game, model)
                # Normalize predictions to expected nested structure
                self._transform_predictions_format(model)
                # Validate score/team consistency
                validation_result = validate_score_team_consistency(model, game)
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
                validated_models.append(model)
            except Exception as e:
                self.log_error(f"Error modeling game {game_id}: {e}", exc_info=True)

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

    def _generate_model_notes(self, game: Dict[str, Any], model: Dict[str, Any]) -> str:
        """Generate detailed model notes matching agentic modeler format."""
        teams = game.get("teams", {})
        away_name = teams.get("away", "Away")
        home_name = teams.get("home", "Home")
        preds = model.get("predictions", {})
        meta = model.get("meta", {})
        
        # Extract advanced stats from game data
        adv = game.get("adv", {})
        away_adv = adv.get("away", {}) if isinstance(adv, dict) else {}
        home_adv = adv.get("home", {}) if isinstance(adv, dict) else {}
        
        # Extract recent/pace trends
        recent = game.get("recent", {})
        away_trend_str = (recent.get("away", {}) or {}).get("pace_trend", "same")
        home_trend_str = (recent.get("home", {}) or {}).get("pace_trend", "same")
        
        # Get values from meta
        base_pace = meta.get("base_pace", 0.0)
        trend_adj = meta.get("trend_adjustment", 0.0)
        final_pace = meta.get("final_pace", 0.0)
        base_margin = meta.get("base_margin", 0.0)
        hca_adj = meta.get("hca_adjustment", 0.0)
        mismatch_adj = meta.get("mismatch_adjustment", 0.0)
        raw_margin = meta.get("raw_margin", 0.0)
        damp_applied = meta.get("dampening_applied", False)
        garbage_applied = meta.get("garbage_time_applied", False)
        regression_pct = meta.get("total_regression_pct", 0.0)
        is_neutral = meta.get("is_neutral_site", False)
        market_total = meta.get("market_total_used")
        market_spread_home = meta.get("market_spread_home")
        
        # Get predictions
        scores = preds.get("scores", {})
        away_score = scores.get("away", 0.0)
        home_score = scores.get("home", 0.0)
        margin = preds.get("margin", 0.0)
        total = preds.get("total", 0.0)
        away_prob = preds.get("win_probs", {}).get("away", 0.5)
        home_prob = preds.get("win_probs", {}).get("home", 0.5)
        confidence = preds.get("confidence", 0.3)
        
        # Calculate raw scores and totals from meta (if available) or reconstruct
        # We need to reverse engineer raw values from meta
        # For now, use the values we have and estimate raw values
        
        # Calculate raw values from pace and efficiency
        away_adjt = away_adv.get("adjt")
        home_adjt = home_adv.get("adjt")
        away_adjo = away_adv.get("adjo")
        away_adjd = away_adv.get("adjd")
        home_adjo = home_adv.get("adjo")
        home_adjd = home_adv.get("adjd")
        
        # Get raw values from meta (stored during calculation)
        away_pts_100 = meta.get("away_pts_per_100")
        home_pts_100 = meta.get("home_pts_per_100")
        raw_away = meta.get("raw_away_score")
        raw_home = meta.get("raw_home_score")
        raw_total_calc = meta.get("raw_total")
        calibrated_total = meta.get("calibrated_total", total)
        edge_mag = meta.get("edge_magnitude", 0.0)
        shrink_factor = meta.get("shrink_factor", 1.0)
        
        # Build notes string matching agentic format
        notes_lines = []
        
        # PACE section - use suppression model formula
        if away_adjt and home_adjt:
            slower = min(away_adjt, home_adjt)
            faster = max(away_adjt, home_adjt)
            # Suppression model: (Slower * 0.65) + (Faster * 0.35)
            pace_desc = f"Base pace=(Slower_AdjT {slower:.1f} * 0.65) + (Faster_AdjT {faster:.1f} * 0.35)={base_pace:.1f}"
        else:
            pace_desc = f"Base pace={base_pace:.1f}"
        
        trend_desc = f"Pace trends both '{away_trend_str if away_trend_str == home_trend_str else 'mixed'}'"
        if trend_adj != 0.0:
            trend_desc += f" => {trend_adj:+.1f}"
        else:
            trend_desc += " => +0.0"
        
        notes_lines.append(f"PACE: {pace_desc}. {trend_desc}. Final pace={final_pace:.1f}.")
        
        # EFFICIENCY BASELINE
        notes_lines.append(f"EFFICIENCY BASELINE: eff_baseline={EFF_BASELINE:.1f} per protocol.")
        
        # PTS/100 section (require all adv values to avoid NoneType format errors)
        if (
            away_pts_100 is not None
            and home_pts_100 is not None
            and away_adjo is not None
            and away_adjd is not None
            and home_adjo is not None
            and home_adjd is not None
        ):
            pts_desc = f"away_pts_per_100=(Away_AdjO {away_adjo:.1f} * Home_AdjD {home_adjd:.1f})/{EFF_BASELINE:.1f}={away_pts_100:.1f}. "
            pts_desc += f"home_pts_per_100=(Home_AdjO {home_adjo:.1f} * Away_AdjD {away_adjd:.1f})/{EFF_BASELINE:.1f}={home_pts_100:.1f}."
        else:
            pts_desc = "PTS/100: Missing advanced stats (fallback used)."
        notes_lines.append(f"PTS/100: {pts_desc}")
        
        # CONTEXT section
        hca_desc = f"HCA {hca_adj:+.1f}" if not is_neutral else "HCA 0.0 (neutral site)"
        inj_desc = "Injuries none"
        mismatch_desc = f"Mismatch {mismatch_adj:+.1f}" if mismatch_adj != 0.0 else "Mismatch 0.0"
        notes_lines.append(f"CONTEXT: {hca_desc}. {inj_desc}. {mismatch_desc}.")
        
        # RAW section
        if raw_away and raw_home and raw_total_calc:
            raw_desc = f"raw_away=({away_pts_100:.1f}/100)*{final_pace:.1f}={raw_away:.1f}. "
            raw_desc += f"raw_home=({home_pts_100:.1f}/100)*{final_pace:.1f}={raw_home:.1f}. "
            raw_desc += f"raw_total={raw_total_calc:.1f}. "
            raw_desc += f"raw_margin=({raw_home:.1f}-{raw_away:.1f})+{hca_adj:.1f}+{mismatch_adj:.1f}={raw_margin:.1f}."
        else:
            raw_desc = f"raw_margin={raw_margin:.1f} (base {base_margin:.1f} + HCA {hca_adj:.1f} + mismatch {mismatch_adj:.1f})."
        notes_lines.append(f"RAW: {raw_desc}")
        
        # TOTAL CALIBRATION
        if market_total is not None and raw_total_calc is not None:
            total_diff = raw_total_calc - market_total
            reg_pct_str = f"{int(regression_pct * 100)}%"
            if raw_total_calc > 155.0:
                range_desc = "Raw total >155"
            elif 140.0 <= raw_total_calc <= 155.0:
                range_desc = "Raw total in standard range"
            else:
                range_desc = "Raw total <140"
            
            notes_lines.append(f"TOTAL CALIBRATION: Market total={market_total:.1f}. total_diff={raw_total_calc:.1f}-{market_total:.1f}={total_diff:.1f}. {range_desc} => {reg_pct_str} regression: calibrated_total={raw_total_calc:.1f}-{regression_pct:.2f}*({total_diff:.1f})={calibrated_total:.1f}.")
        elif market_total:
            notes_lines.append(f"TOTAL CALIBRATION: Market total={market_total:.1f}.")
        
        # BLOWOUT/GARBAGE TIME
        if abs(raw_margin) > 22:
            notes_lines.append(f"BLOWOUT: |raw_margin|={abs(raw_margin):.1f}>22 => garbage_time_adj=-4.0 => final total={total:.1f}.")
        else:
            notes_lines.append(f"BLOWOUT: |raw_margin|={abs(raw_margin):.1f} not >22 => no garbage-time adjustment.")
        
        # FINAL scores
        notes_lines.append(f"FINAL: final_home={total:.1f}/2+{margin:.1f}/2={home_score:.1f}; final_away={away_score:.1f}.")
        
        # WIN PROBS
        # Calculate raw prob before shrinkage (approximate)
        from src.agents.modeler_engine import calculate_win_probability, WIN_PROB_SCALE
        raw_away_prob, raw_home_prob = calculate_win_probability(margin)
        
        # Check if shrinkage was applied
        shrink_info = ""
        shrinkage_applied = meta.get("discrepancy_shrinkage_applied", False)
        if shrinkage_applied and shrink_factor < 1.0:
            shrink_pct = int((1.0 - shrink_factor) * 100)
            shrink_info = f" Discrepancy shrinkage: edge_mag={edge_mag:.1f} >{'8' if edge_mag > 8.0 else '6'} => shrink_factor={shrink_factor:.2f}."
        else:
            shrink_info = " No discrepancy shrinkage applied."
        
        notes_lines.append(f"WIN PROBS: p_home_raw=1/(1+exp(-{margin:.1f}/{WIN_PROB_SCALE}))={raw_home_prob:.3f}.{shrink_info} p_home={home_prob:.3f}.")
        
        # CONFIDENCE
        if abs(raw_margin) > 20:
            conf_desc = f"Blowout tier caps at 0.60"
        else:
            conf_desc = "Standard tier"
        
        data_quality = "good" if (away_adjo and away_adjd and away_adjt and home_adjo and home_adjd and home_adjt) else "limited"
        # Confidence formula: base 0.45 + (edge_mag/40.0), with penalty if edge_mag > 6.0
        # Edge magnitude is the difference between model predictions and market (spread or total)
        # Small edge (model ≈ market) = low confidence, large edge = higher confidence (up to 12.0 points)
        notes_lines.append(f"CONFIDENCE: {conf_desc}; data quality {data_quality}. Base 0.45 + (edge_mag {edge_mag:.1f}/40.0) = {confidence:.2f}.")
        
        return "\n".join(notes_lines)
    