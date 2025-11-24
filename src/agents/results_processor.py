"""Results Processor agent for fetching game results and settling bets"""

from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path

from src.agents.base import BaseAgent
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import Database, BetModel, PickModel, GameModel
from src.data.analytics import AnalyticsService
from src.data.scrapers.games_scraper import GamesScraper
from sqlalchemy import func
from src.utils.logging import get_logger
from src.utils.team_normalizer import normalize_team_name, get_team_name_variations

logger = get_logger("agents.results_processor")


class ResultsProcessor(BaseAgent):
    """Results Processor agent for fetching game results and settling bets"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Results Processor agent"""
        super().__init__("ResultsProcessor", db, llm_client)
        self.analytics_service = AnalyticsService(db) if db else None
        self.games_scraper = GamesScraper()
        self.cache_dir = Path("data/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "results_cache.json"
        self.cache_ttl_hours = 24  # Cache results for 24 hours
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Results Processor"""
        # This agent doesn't use LLM, it's purely data processing
        return ""
    
    def process(self, target_date: Optional[date] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Process yesterday's results: fetch game results, settle bets, calculate statistics
        
        Args:
            target_date: Date to process results for (default: today, processes yesterday)
            force_refresh: If True, bypass cache and fetch fresh results
            
        Returns:
            Dictionary with statistics and report data
        """
        if target_date is None:
            target_date = date.today()
        
        # Process yesterday's results (bets placed yesterday that settled)
        yesterday = target_date - timedelta(days=1)
        
        self.log_info(f"Processing results for {yesterday} (bets placed on {yesterday})")
        
        # Check if results already processed (unless force_refresh)
        if not force_refresh:
            # Check cache first
            cached_results = self._get_cached_results(yesterday)
            if cached_results:
                self.log_info(f"Using cached results for {yesterday}")
                return cached_results
            
            # Check if bets are already settled
            session = self.db.get_session()
            try:
                picks = session.query(PickModel).filter(
                    func.date(PickModel.created_at) == yesterday
                ).all()
                
                if picks:
                    # Check if all bets are already settled
                    # Extract all pick IDs while session is active to avoid detached instance errors
                    pick_ids = []
                    for pick in picks:
                        try:
                            pick_ids.append(pick.id)
                        except Exception as e:
                            self.logger.debug(f"Could not extract ID from pick object: {e}")
                            continue
                    
                    all_settled = True
                    for pick_id in pick_ids:
                        bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
                        if bet and bet.result == BetResult.PENDING:
                            all_settled = False
                            break
                    
                    if all_settled and len(picks) > 0:
                        self.log_info(f"All bets for {yesterday} already settled, calculating stats from existing data")
                        stats = self._calculate_statistics(picks, session, yesterday)
                        # Cache and return
                        self._cache_results(yesterday, stats)
                        return stats
            finally:
                session.close()
        
        # Fetch game results from ESPN
        self.log_info(f"Fetching game results for {yesterday}")
        games_with_results = self._fetch_game_results(yesterday)
        
        # Save results to database
        session = self.db.get_session()
        try:
            saved_count = 0
            for game_id, result_data in games_with_results.items():
                game = session.query(GameModel).filter_by(id=game_id).first()
                if game and result_data.get("result"):
                    # Only update if result doesn't exist or is different
                    if not game.result or game.result != result_data["result"]:
                        game.result = result_data["result"]
                        game.status = GameStatus.FINAL
                        saved_count += 1
            if saved_count > 0:
                session.commit()
                self.log_info(f"Saved {saved_count} game results to database")
        except Exception as e:
            self.log_error(f"Error saving game results to database: {e}")
            session.rollback()
        
        # Get bets from yesterday using analytics service
        try:
            if not self.analytics_service:
                self.log_error("Analytics service not available")
                picks = []
            else:
                picks = self.analytics_service.get_picks_for_date(yesterday)
            
            if not picks:
                self.log_info(f"No bets found for {yesterday}")
                result = {
                    "date": yesterday.isoformat(),
                    "total_picks": 0,
                    "wins": 0,
                    "losses": 0,
                    "pushes": 0,
                    "accuracy": 0.0,
                    "profit_loss_units": 0.0,
                    "profit_loss_dollars": 0.0,
                    "total_wagered_units": 0.0,
                    "total_wagered_dollars": 0.0,
                    "settled_bets": 0
                }
                self._cache_results(yesterday, result)
                return result
            
            # Settle bets based on game results
            settled_count = self._settle_bets(picks, games_with_results, session, yesterday)
            
            # Calculate statistics
            stats = self._calculate_statistics(picks, session, yesterday)
            
            # Generate and save daily report
            report_text = self.generate_daily_report(stats)
            self._save_daily_report(yesterday, report_text)
            
            # Append to master report
            self._append_to_master_report(stats)
            
            # Cache results
            self._cache_results(yesterday, stats)
            
            return stats
            
        except Exception as e:
            self.log_error(f"Error processing results: {e}", exc_info=True)
            return {
                "date": yesterday.isoformat(),
                "error": str(e),
                "total_picks": 0,
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "accuracy": 0.0,
                "profit_loss_units": 0.0,
                "profit_loss_dollars": 0.0
            }
        finally:
            session.close()
    
    def _fetch_game_results(self, game_date: date) -> Dict[int, Dict[str, Any]]:
        """Fetch game results from ESPN API and match with database games by game ID"""
        # Get all game IDs from the database for this date
        session = self.db.get_session()
        results = {}
        
        try:
            # Query database games directly by date and get their IDs
            # Use team references if available, otherwise fall back to old columns
            try:
                db_games = session.query(
                    GameModel.id,
                    GameModel.team1_id,
                    GameModel.team2_id,
                    GameModel.result
                ).filter(
                    func.date(GameModel.date) == game_date
                ).all()
                # Convert team IDs to names for backwards compatibility
                from src.data.storage import TeamModel
                team_map = {}
                for game_id, team1_id, team2_id, result in db_games:
                    if team1_id and team1_id not in team_map:
                        team = session.query(TeamModel).filter_by(id=team1_id).first()
                        if team:
                            team_map[team1_id] = team.normalized_team_name
                    if team2_id and team2_id not in team_map:
                        team = session.query(TeamModel).filter_by(id=team2_id).first()
                        if team:
                            team_map[team2_id] = team.normalized_team_name
                
                # Convert to format expected by rest of code
                db_games = [
                    (game_id, team_map.get(team1_id, ''), team_map.get(team2_id, ''), result)
                    for game_id, team1_id, team2_id, result in db_games
                ]
            except Exception:
                # Fallback to old schema
                db_games = session.query(
                    GameModel.id,
                    GameModel.team1,
                    GameModel.team2,
                    GameModel.result
                ).filter(
                    func.date(GameModel.date) == game_date
                ).all()
            
            self.log_info(f"Found {len(db_games)} games in database for {game_date}")
            
            # For each database game, check if it has a result or fetch from scraper
            # Use game_id directly - no team name matching needed
            game_ids = [game_id for game_id, _, _, _ in db_games]
            
            # Fetch results from scraper (for games that don't have results yet)
            scraped_games = self.games_scraper.scrape_games(game_date)
            self.log_info(f"Fetched {len(scraped_games)} games from ESPN for {game_date}")
            
            # Create a map of game IDs to database game info
            db_game_info = {}
            for game_id, team1, team2, result in db_games:
                db_game_info[game_id] = {
                    "game_id": game_id,
                    "team1": team1,
                    "team2": team2,
                    "existing_result": result
                }
            
            # Match scraped games to database games by game_id
            # Since scraped games don't have database IDs, we need to match by team names
            # But we'll prioritize using existing database results when available
            scraped_results_by_teams = {}
            for scraped_game in scraped_games:
                if scraped_game.status == GameStatus.FINAL and scraped_game.result:
                    # Create a key from team names for matching
                    key = (scraped_game.team1, scraped_game.team2)
                    scraped_results_by_teams[key] = scraped_game.result
                    # Also try reverse order
                    key_reverse = (scraped_game.team2, scraped_game.team1)
                    scraped_results_by_teams[key_reverse] = scraped_game.result
            
            # For each database game, use existing result or match with scraped result
            matched_count = 0
            for game_id, team1, team2, existing_result in db_games:
                if existing_result:
                    # Already have result in database, use it
                    results[game_id] = {
                        "game_id": game_id,
                        "team1": team1,
                        "team2": team2,
                        "status": "final",
                        "result": existing_result
                    }
                    matched_count += 1
                else:
                    # Try to find scraped result by team names
                    key = (team1, team2)
                    key_reverse = (team2, team1)
                    scraped_result = scraped_results_by_teams.get(key) or scraped_results_by_teams.get(key_reverse)
                    
                    if scraped_result:
                        results[game_id] = {
                            "game_id": game_id,
                            "team1": team1,
                            "team2": team2,
                            "status": "final",
                            "result": scraped_result
                        }
                        matched_count += 1
            
            self.log_info(f"Matched {matched_count} games with results out of {len(db_games)} database games")
            
        finally:
            session.close()
        
        return results
    
    def _determine_bet_result_from_team_id(
        self,
        pick_game_id: int,
        pick_bet_type,
        pick_line: float,
        pick_team_id: Optional[int],
        game_result: Dict[str, Any],
        session
    ) -> Optional[BetResult]:
        """Determine bet result using team_id (simpler and more reliable)"""
        if not pick_team_id:
            return None  # Can't determine without team_id
        
        result_data = game_result.get("result")
        if not result_data:
            return None
        
        # Convert scores to integers
        def safe_int(value, default=0):
            if isinstance(value, int):
                return value
            if isinstance(value, (float, str)):
                try:
                    return int(float(str(value)))
                except (ValueError, TypeError):
                    return default
            return default
        
        home_score = safe_int(result_data.get("home_score", 0), 0)
        away_score = safe_int(result_data.get("away_score", 0), 0)
        home_team_id = result_data.get("home_team_id")  # May not be in result yet
        away_team_id = result_data.get("away_team_id")  # May not be in result yet
        
        # Get game to determine which team is home/away
        from src.data.storage import GameModel, TeamModel
        game = session.query(GameModel).filter_by(id=pick_game_id).first()
        if not game:
            return None
        
        # Determine if pick_team_id is home or away
        pick_team_is_home = (game.team1_id == pick_team_id)
        pick_team_is_away = (game.team2_id == pick_team_id)
        
        if not pick_team_is_home and not pick_team_is_away:
            # Team ID doesn't match game teams - fall back to old method
            return None
        
        # Calculate result based on bet type
        if pick_bet_type == BetType.SPREAD:
            if pick_team_is_home:
                # Home team spread
                margin = home_score - away_score
                line_negated = -pick_line
                if abs(margin - line_negated) < 0.01:
                    return BetResult.PUSH
                elif margin > line_negated:
                    return BetResult.WIN
                else:
                    return BetResult.LOSS
            elif pick_team_is_away:
                # Away team spread
                margin = away_score - home_score
                line_negated = -pick_line
                if abs(margin - line_negated) < 0.01:
                    return BetResult.PUSH
                elif margin > line_negated:
                    return BetResult.WIN
                else:
                    return BetResult.LOSS
        
        elif pick_bet_type == BetType.MONEYLINE:
            if pick_team_is_home and home_score > away_score:
                return BetResult.WIN
            elif pick_team_is_away and away_score > home_score:
                return BetResult.WIN
            elif home_score == away_score:
                return BetResult.PUSH
            else:
                return BetResult.LOSS
        
        # For totals, team_id is null, so this method won't be called
        return None
    
    def _settle_bets(self, picks: List[PickModel], games_with_results: Dict[int, Dict[str, Any]], session, pick_date: date) -> int:
        """Settle bets based on game results"""
        settled_count = 0
        no_bet_count = 0
        already_settled_count = 0
        re_settled_count = 0
        no_game_result_count = 0
        no_bet_result_count = 0
        
        self.log_info(f"Attempting to settle {len(picks)} picks. Found {len(games_with_results)} games with results.")
        
        # Extract pick IDs first while session might still be active
        pick_ids = []
        for pick in picks:
            try:
                # Try to get ID - use merge if detached
                if hasattr(pick, '_sa_instance_state') and pick._sa_instance_state.session is None:
                    # Pick is detached, try to merge
                    pick = session.merge(pick)
                pick_ids.append(pick.id)
            except Exception as e:
                # If we can't get ID from pick object, skip it
                # We'll query picks directly by date instead
                self.log_debug(f"Could not extract ID from pick object: {e}")
                pick_ids = None
                break
        
        # If we couldn't extract IDs from pick objects, query directly
        if pick_ids is None or len(pick_ids) != len(picks):
            # Query picks directly using the same filter - this avoids detached instance issues
            picks_query = session.query(
                PickModel.id,
                PickModel.game_id,
                PickModel.bet_type,
                PickModel.line,
                PickModel.odds,
                PickModel.stake_amount,
                PickModel.rationale,
                PickModel.selection_text,
                PickModel.team_id
            ).filter(
                func.date(PickModel.created_at) == pick_date
            ).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale, selection_text, team_id in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or "",
                    "selection_text": selection_text or "",
                    "team_id": team_id
                })
        else:
            # We successfully extracted IDs, now query by ID to get fresh data
            picks_query = session.query(
                PickModel.id,
                PickModel.game_id,
                PickModel.bet_type,
                PickModel.line,
                PickModel.odds,
                PickModel.stake_amount,
                PickModel.rationale,
                PickModel.selection_text,
                PickModel.team_id
            ).filter(PickModel.id.in_(pick_ids)).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale, selection_text, team_id in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or "",
                    "selection_text": selection_text or "",
                    "team_id": team_id
                })
        
        # Now process picks using extracted data (no longer need pick objects)
        for pick_data in picks_data:
            pick_id = pick_data["id"]
            pick_game_id = pick_data["game_id"]
            pick_bet_type = pick_data["bet_type"]
            pick_line = pick_data["line"]
            pick_odds = pick_data["odds"]
            pick_stake_amount = pick_data["stake_amount"]
            pick_rationale = pick_data["rationale"]
            pick_selection_text = pick_data.get("selection_text", "")
            
            # Get or create bet record for this pick
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            if not bet:
                # Create bet record for picks that don't have one yet
                # This allows us to track all picks, not just "placed" ones
                bet = BetModel(
                    pick_id=pick_id,
                    placed_at=datetime.now(),
                    result=BetResult.PENDING
                )
                session.add(bet)
                session.flush()  # Flush to get the bet ID
            
            # Allow re-settlement if bet was previously settled (in case of logic fixes)
            # This allows fixing incorrectly settled bets when logic is updated
            was_already_settled = bet.result != BetResult.PENDING
            
            # Get game result
            game_result = games_with_results.get(pick_game_id)
            if not game_result:
                no_game_result_count += 1
                self.logger.debug(f"Game {pick_game_id} not found in results or not final yet")
                continue
            
            # Determine bet result based on pick and game result
            # Use team_id directly if available (much simpler!)
            pick_team_id = pick_data.get("team_id")
            bet_result = self._determine_bet_result_from_team_id(
                pick_game_id, pick_bet_type, pick_line, pick_team_id, game_result, session
            )
            
            # Fall back to old method if team_id not available (backwards compatibility)
            if not bet_result:
                team_identifier = pick_selection_text if pick_selection_text else pick_rationale
                bet_result = self._determine_bet_result_from_attrs(
                    pick_game_id, pick_bet_type, pick_line, team_identifier, game_result, session, 
                    use_selection_text=bool(pick_selection_text)
                )
            
            if not bet_result:
                no_bet_result_count += 1
                bet_type_str = pick_bet_type.value if hasattr(pick_bet_type, 'value') else str(pick_bet_type)
                self.logger.debug(f"Could not determine bet result for pick {pick_id} (game {pick_game_id}, type {bet_type_str})")
                continue
            
            # Calculate payout and profit/loss using extracted attributes
            payout, profit_loss = self._calculate_payout_from_attrs(
                pick_stake_amount, pick_odds, bet_result
            )
            
            bet.result = bet_result
            bet.payout = payout
            bet.profit_loss = profit_loss
            bet.settled_at = datetime.now()
            
            if was_already_settled:
                re_settled_count += 1
            else:
                settled_count += 1
            
            # Reconstruct selection text for logging
            bet_type_str = pick_bet_type.value if hasattr(pick_bet_type, 'value') else str(pick_bet_type)
            selection_desc = f"{bet_type_str} {pick_line:+.1f}"
            action = "Re-settled" if was_already_settled else "Settled"
            self.log_info(
                f"{action} bet: {selection_desc} - {bet_result.value} "
                f"(P&L: ${profit_loss:.2f})"
            )
        
        # Log summary
        self.log_info(
            f"Settlement summary: {settled_count} newly settled, "
            f"{re_settled_count} re-settled, "
            f"{no_bet_count} no bet record, "
            f"{no_game_result_count} no game result, "
            f"{no_bet_result_count} could not determine result"
        )
        
        session.commit()
        return settled_count
    
    def _determine_bet_result_from_attrs(
        self, 
        pick_game_id: int, 
        pick_bet_type, 
        pick_line: float, 
        pick_rationale: str, 
        game_result: Dict[str, Any], 
        session,
        use_selection_text: bool = False
    ) -> Optional[BetResult]:
        """Determine if a bet won, lost, or pushed based on game result (using extracted attributes)"""
        result_data = game_result.get("result")
        if not result_data:
            return None
        
        # Convert scores to integers (they might come as strings from the API)
        def safe_int(value, default=0):
            """Safely convert value to integer"""
            if isinstance(value, int):
                return value
            if isinstance(value, (float, str)):
                try:
                    return int(float(str(value)))
                except (ValueError, TypeError):
                    return default
            return default
        
        home_score_raw = result_data.get("home_score", 0)
        away_score_raw = result_data.get("away_score", 0)
        home_score = safe_int(home_score_raw, 0)
        away_score = safe_int(away_score_raw, 0)
        home_team = result_data.get("home_team", "")
        away_team = result_data.get("away_team", "")
        
        # Get pick details from extracted attributes
        bet_type = pick_bet_type
        line = pick_line
        
        # Get game information to determine which team the pick is for
        # We need to check the game model to see which team is home/away
        try:
            from src.data.storage import GameModel
            game = session.query(GameModel).filter_by(id=pick_game_id).first()
            if not game:
                return None
            
            # Extract game attributes while session is active
            # Use team references if available, otherwise fall back to old team1/team2 columns
            if hasattr(game, 'team1_ref') and game.team1_ref:
                game_home_team = game.team1_ref.normalized_team_name
            else:
                game_home_team = getattr(game, 'team1', '')
            
            if hasattr(game, 'team2_ref') and game.team2_ref:
                game_away_team = game.team2_ref.normalized_team_name
            else:
                game_away_team = getattr(game, 'team2', '')
        except Exception as e:
            self.log_error(f"Error accessing game attributes: {e}")
            return None
        
        # Determine which team the pick is for by checking rationale
        # The rationale should mention the team name
        # Use normalized team names for better matching
        rationale_lower = pick_rationale.lower()
        
        # Normalize team names for matching
        norm_home_team = normalize_team_name(home_team, for_matching=True)
        norm_away_team = normalize_team_name(away_team, for_matching=True)
        norm_game_home_team = normalize_team_name(game_home_team, for_matching=True)
        norm_game_away_team = normalize_team_name(game_away_team, for_matching=True)
        
        # Also get team name variations for more flexible matching
        home_variations = get_team_name_variations(home_team) + get_team_name_variations(game_home_team)
        away_variations = get_team_name_variations(away_team) + get_team_name_variations(game_away_team)
        
        pick_team = None
        if bet_type == BetType.SPREAD or bet_type == BetType.MONEYLINE:
            # If we have selection_text, extract team name directly from it
            # Format is typically "Team Name +X.X" or "Team Name -X.X"
            if use_selection_text and pick_rationale:
                import re
                # Extract team name from selection_text (everything before the line)
                # Pattern: team name followed by optional space and then +/- followed by number
                line_pattern = r'([+-]?\d+\.?\d*)'
                match = re.search(line_pattern, pick_rationale)
                if match:
                    # Get text before the line number - this should be the team name
                    line_start = match.start()
                    team_name_from_selection = pick_rationale[:line_start].strip()
                    
                    # Normalize and match against home/away teams
                    norm_selection_team = normalize_team_name(team_name_from_selection, for_matching=True)
                    
                    # Check if it matches home or away team
                    if (norm_selection_team == norm_home_team or norm_selection_team == norm_game_home_team or
                        norm_selection_team in norm_home_team or norm_home_team in norm_selection_team):
                        pick_team = "home"
                        self.logger.debug(f"Team determined as HOME from selection_text: {team_name_from_selection}")
                    elif (norm_selection_team == norm_away_team or norm_selection_team == norm_game_away_team or
                          norm_selection_team in norm_away_team or norm_away_team in norm_selection_team):
                        pick_team = "away"
                        self.logger.debug(f"Team determined as AWAY from selection_text: {team_name_from_selection}")
            
            # Helper function for team matching
            def check_team_match(norm_team_name, rationale_text):
                """Check if team name matches rationale (exact or all words present)"""
                if norm_team_name in rationale_text:
                    return True
                # Check if all significant words appear (for cases like "nicholls colonels" where words appear separately)
                words = norm_team_name.split()
                skip_words = {'univ', 'university', 'of', 'the', 'st', 'state', 'univ'}
                significant_words = [w for w in words if w not in skip_words and len(w) > 2]
                if len(significant_words) >= 2:
                    # Check if all significant words appear in rationale
                    all_words_present = all(word in rationale_text for word in significant_words)
                    if all_words_present:
                        return True
                return False
            
            # Initialize match flags
            home_match = False
            away_match = False
            
            # If we couldn't determine from selection_text, fall back to rationale parsing
            if not pick_team:
                # Check if rationale mentions home or away team using normalized names
                # Check both exact normalized names and variations
                
                # Check normalized names first (most reliable)
                # Also check if all significant words from the normalized name appear in rationale
                if check_team_match(norm_home_team, rationale_lower) or check_team_match(norm_game_home_team, rationale_lower):
                    home_match = True
                if check_team_match(norm_away_team, rationale_lower) or check_team_match(norm_game_away_team, rationale_lower):
                    away_match = True
            
            # Check for common abbreviations (e.g., "LIU" for "Long Island University")
            # Extract first letters of each word to form potential abbreviations
            # Also check for abbreviations that skip common words like "univ", "university", "of"
            def get_abbreviations(team_name_normalized):
                """Get possible abbreviations from a normalized team name"""
                abbrevs = set()
                words = team_name_normalized.split()
                # Full abbreviation (all words)
                if words:
                    abbrevs.add(''.join([w[0] for w in words if len(w) > 0]))
                # Abbreviation skipping common words (univ, university, of, the)
                skip_words = {'univ', 'university', 'of', 'the', 'st', 'state'}
                significant_words = [w for w in words if w not in skip_words]
                if len(significant_words) >= 2:
                    abbrevs.add(''.join([w[0] for w in significant_words if len(w) > 0]))
                # Also check for patterns like "Long Island" -> "LI"
                if len(words) >= 2:
                    abbrevs.add(''.join([w[0] for w in words[:3] if len(w) > 0]))  # First 3 words
                return abbrevs
            
            if not home_match:
                home_abbrevs = get_abbreviations(norm_home_team) | get_abbreviations(norm_game_home_team)
                for abbrev in home_abbrevs:
                    if len(abbrev) >= 2 and abbrev.lower() in rationale_lower:
                        home_match = True
                        break
            if not away_match:
                away_abbrevs = get_abbreviations(norm_away_team) | get_abbreviations(norm_game_away_team)
                for abbrev in away_abbrevs:
                    if len(abbrev) >= 2 and abbrev.lower() in rationale_lower:
                        away_match = True
                        break
            
            # If no normalized match, check variations (but skip very short/common words)
            # Check both teams' variations and find the best match
            if not home_match and not away_match:
                best_home_match = None
                best_away_match = None
                
                # Check home team variations - find the longest match
                for variation in home_variations:
                    if variation and len(variation) > 3:  # Skip very short variations that are too common
                        if variation.lower() in rationale_lower:
                            if best_home_match is None or len(variation) > len(best_home_match):
                                best_home_match = variation
                
                # Check away team variations - find the longest match
                for variation in away_variations:
                    if variation and len(variation) > 3:  # Skip very short variations that are too common
                        if variation.lower() in rationale_lower:
                            if best_away_match is None or len(variation) > len(best_away_match):
                                best_away_match = variation
                
                # Use the best match - prefer longer/more specific matches
                if best_home_match and best_away_match:
                    # Both matched - use the longer/more specific one
                    # If same length, prefer the one that's more team-specific
                    # (check if full normalized team name appears, or prefer away if equal)
                    if len(best_home_match) > len(best_away_match):
                        home_match = True
                    elif len(best_away_match) > len(best_home_match):
                        away_match = True
                    else:
                        # Same length - check if full normalized names appear
                        if norm_away_team in rationale_lower or norm_game_away_team in rationale_lower:
                            away_match = True
                        elif norm_home_team in rationale_lower or norm_game_home_team in rationale_lower:
                            home_match = True
                        else:
                            # Still tied - this is ambiguous, prefer neither
                            # (will fall through to return None)
                            pass
                elif best_home_match:
                    home_match = True
                elif best_away_match:
                    away_match = True
            
            # Determine team based on matches
            if home_match and away_match:
                # Both teams mentioned - try to disambiguate by checking which team is mentioned with the spread
                # Look for patterns like "Team +X" or "Team -X" or "Team X" where X is the line
                line_str = f"{abs(line):.1f}".rstrip('0').rstrip('.')
                
                # Get significant words from team names for matching
                def get_significant_words(team_name):
                    words = team_name.split()
                    skip_words = {'univ', 'university', 'of', 'the', 'st', 'state', 'univ'}
                    return [w for w in words if w not in skip_words and len(w) > 2]
                
                home_sig_words = get_significant_words(norm_home_team) + get_significant_words(norm_game_home_team)
                away_sig_words = get_significant_words(norm_away_team) + get_significant_words(norm_game_away_team)
                
                # Check if any significant word from home team appears with the spread line
                home_with_line = False
                for word in set(home_sig_words):
                    if (f"{word} {line_str}" in rationale_lower or 
                        f"{word} +{line_str}" in rationale_lower or
                        f"{word} -{line_str}" in rationale_lower):
                        home_with_line = True
                        break
                
                # Check if any significant word from away team appears with the spread line
                away_with_line = False
                for word in set(away_sig_words):
                    if (f"{word} {line_str}" in rationale_lower or 
                        f"{word} +{line_str}" in rationale_lower or
                        f"{word} -{line_str}" in rationale_lower):
                        away_with_line = True
                        break
                
                if away_with_line and not home_with_line:
                    pick_team = "away"
                    self.logger.debug(f"Both teams matched, but away team mentioned with spread line, determined as AWAY")
                elif home_with_line and not away_with_line:
                    pick_team = "home"
                    self.logger.debug(f"Both teams matched, but home team mentioned with spread line, determined as HOME")
                else:
                    # Still ambiguous, return None
                    self.logger.debug(f"Both teams matched in rationale, cannot determine pick team")
                    pick_team = None
            elif home_match:
                pick_team = "home"
                self.logger.debug(f"Team determined as HOME via name matching")
            elif away_match:
                pick_team = "away"
                self.logger.debug(f"Team determined as AWAY via name matching")
            else:
                # No match found - cannot determine team
                self.logger.debug(f"No team name found in rationale, cannot determine pick team")
                pick_team = None
        
        # If we couldn't determine which team, return None
        if not pick_team and bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
            self.logger.debug(f"Could not determine pick team for bet_type={bet_type}, rationale={pick_rationale[:100]}")
            return None
        
        # Debug logging for team determination
        if bet_type == BetType.SPREAD:
            self.logger.debug(f"Spread bet: pick_team={pick_team}, line={line}, rationale={pick_rationale[:100]}")
        
        # Calculate result based on bet type
        if bet_type == BetType.SPREAD:
            if pick_team == "home":
                # Home team spread: home needs to cover the spread
                # For home team with -7.5: home must win by more than 7.5 (margin > 7.5)
                # For home team with +7.5: home gets 7.5 points, wins if they lose by 7 or less (margin > -7.5)
                # Formula: home wins if (home_score + line) > away_score
                # Which simplifies to: home_score - away_score > -line
                # Or: margin > -line (where margin = home_score - away_score)
                margin = home_score - away_score
                line_negated = -line
                if abs(margin - line_negated) < 0.01:  # Floating point comparison with tolerance
                    self.logger.debug(f"Home spread PUSH: margin={margin}, line={line}, -line={line_negated}")
                    return BetResult.PUSH
                elif margin > line_negated:
                    self.logger.debug(f"Home spread WIN: margin={margin}, line={line}, -line={line_negated}, check: {margin} > {line_negated} = True")
                    return BetResult.WIN
                else:
                    self.logger.debug(f"Home spread LOSS: margin={margin}, line={line}, -line={line_negated}, check: {margin} > {line_negated} = False")
                    return BetResult.LOSS
            elif pick_team == "away":
                # Away team spread: away needs to cover the spread
                # For away team with +7.5: away gets 7.5 points, wins if they lose by 7 or less (margin >= -7)
                # For away team with -1.5: away must win by more than 1.5 (margin > 1.5)
                # The line is stored as positive for underdogs (+7.5 stored as 7.5) and negative for favorites
                # Formula: away wins if (away_score + line) > home_score
                # Which simplifies to: away_score - home_score > -line
                # Or: margin > -line (where margin = away_score - home_score)
                margin = away_score - home_score
                # For positive lines (underdog): line > 0, so -line < 0
                #   Example: line = 7.5, margin = -2, check: -2 > -7.5 → TRUE → WIN ✓
                #   Example: line = 7.5, margin = -8, check: -8 > -7.5 → FALSE → LOSS ✓
                # For negative lines (favorite): line < 0, so -line > 0  
                #   Example: line = -1.5, margin = 2, check: 2 > 1.5 → TRUE → WIN ✓
                #   Example: line = -1.5, margin = 1, check: 1 > 1.5 → FALSE → LOSS ✓
                # Check for exact equality first (for whole number lines that can push)
                # Use a small tolerance for floating point comparison
                line_negated = -line
                if abs(margin - line_negated) < 0.01:  # Floating point comparison with tolerance
                    self.logger.debug(f"Away spread PUSH: margin={margin}, line={line}, -line={line_negated}")
                    return BetResult.PUSH
                elif margin > line_negated:
                    self.logger.debug(f"Away spread WIN: margin={margin}, line={line}, -line={line_negated}, check: {margin} > {line_negated} = True")
                    return BetResult.WIN
                else:
                    # margin < -line
                    self.logger.debug(f"Away spread LOSS: margin={margin}, line={line}, -line={line_negated}, check: {margin} > {line_negated} = False")
                    return BetResult.LOSS
        
        elif bet_type == BetType.TOTAL:
            # Total: Over/Under
            # For totals, we need to determine if it's Over or Under
            # Check rationale to see if it mentions "over" or "under"
            total_score = home_score + away_score
            rationale_lower = pick_rationale.lower()
            
            if "over" in rationale_lower and "under" not in rationale_lower:
                # Over bet
                if total_score > line:
                    return BetResult.WIN
                elif total_score < line:
                    return BetResult.LOSS
                else:
                    return BetResult.PUSH
            elif "under" in rationale_lower:
                # Under bet
                if total_score < line:
                    return BetResult.WIN
                elif total_score > line:
                    return BetResult.LOSS
                else:
                    return BetResult.PUSH
            else:
                # Default: assume Over if we can't determine
                # This is a fallback - ideally rationale should specify
                if total_score > line:
                    return BetResult.WIN
                elif total_score < line:
                    return BetResult.LOSS
                else:
                    return BetResult.PUSH
        
        elif bet_type == BetType.MONEYLINE:
            # Moneyline: pick team to win
            if pick_team == "home" and home_score > away_score:
                return BetResult.WIN
            elif pick_team == "away" and away_score > home_score:
                return BetResult.WIN
            elif home_score == away_score:
                return BetResult.PUSH  # Tie (rare in basketball)
            else:
                return BetResult.LOSS
        
        elif bet_type == BetType.PARLAY:
            # Parlay: all legs must win
            # For now, we'll need to check each leg
            # This is complex - for MVP, we'll return None and handle separately
            return None
        
        return None
    
    def _calculate_payout_from_attrs(
        self, 
        stake_amount: float, 
        odds: int, 
        bet_result: BetResult
    ) -> Tuple[float, float]:
        """Calculate payout and profit/loss for a bet (using extracted attributes)"""
        stake = stake_amount
        
        if bet_result == BetResult.WIN:
            # Calculate payout based on odds
            if odds > 0:
                # Positive odds: payout = stake * (odds / 100) + stake
                payout = stake * (odds / 100) + stake
            else:
                # Negative odds: payout = stake * (100 / abs(odds)) + stake
                payout = stake * (100 / abs(odds)) + stake
            profit_loss = payout - stake
        elif bet_result == BetResult.PUSH:
            # Push: return stake
            payout = stake
            profit_loss = 0.0
        else:  # LOSS
            payout = 0.0
            profit_loss = -stake
        
        return payout, profit_loss
    
    def _calculate_statistics(self, picks: List[PickModel], session, report_date: date) -> Dict[str, Any]:
        """Calculate accuracy and P&L statistics"""
        total_picks = len(picks)
        wins = 0
        losses = 0
        pushes = 0
        total_wagered_units = 0.0
        total_wagered_dollars = 0.0
        total_profit_loss_units = 0.0
        total_profit_loss_dollars = 0.0
        
        # Extract pick IDs first while session is active
        pick_ids = []
        for pick in picks:
            try:
                # Try to get ID - if it fails, the pick is detached
                # Use session.merge to reattach if needed
                if hasattr(pick, '_sa_instance_state'):
                    # Check if pick is in session
                    try:
                        pick_id = pick.id
                    except Exception:
                        # Pick is detached, try to merge
                        try:
                            pick = session.merge(pick)
                            pick_id = pick.id
                        except Exception:
                            self.log_error(f"Could not reattach pick to session, skipping")
                            continue
                    pick_ids.append(pick_id)
                else:
                    # Not an ORM object, skip
                    continue
            except Exception as e:
                self.log_error(f"Error getting pick ID: {e}", exc_info=True)
                continue
        
        # Query pick data directly using IDs to avoid detached instance errors
        # This ensures we get fresh data from the database
        picks_data = []
        if pick_ids:
            picks_query = session.query(
                PickModel.id,
                PickModel.stake_units,
                PickModel.stake_amount
            ).filter(PickModel.id.in_(pick_ids)).all()
            
            for pick_id, stake_units, stake_amount in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "stake_units": stake_units or 0.0,
                    "stake_amount": stake_amount or 0.0
                })
        
        # Calculate unit value from stake data (if available)
        # Default to 1.0 if no stake data available
        unit_value = 1.0
        if picks_data:
            for pick_data in picks_data:
                stake_units = pick_data.get("stake_units", 0.0)
                stake_amount = pick_data.get("stake_amount", 0.0)
                if stake_units > 0 and stake_amount > 0:
                    unit_value = stake_amount / stake_units
                    break
        
        # Now process picks using extracted data
        for pick_data in picks_data:
            pick_id = pick_data["id"]
            stake_units = pick_data["stake_units"]
            stake_dollars = pick_data["stake_amount"]
            
            total_wagered_units += stake_units
            total_wagered_dollars += stake_dollars
            
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            if bet:
                if bet.result == BetResult.WIN:
                    wins += 1
                    total_profit_loss_dollars += bet.profit_loss
                    total_profit_loss_units += bet.profit_loss / unit_value if unit_value > 0 else 0
                elif bet.result == BetResult.LOSS:
                    losses += 1
                    total_profit_loss_dollars += bet.profit_loss
                    total_profit_loss_units += bet.profit_loss / unit_value if unit_value > 0 else 0
                elif bet.result == BetResult.PUSH:
                    pushes += 1
        
        # Calculate accuracy (wins / (wins + losses), excluding pushes)
        settled_bets = wins + losses
        accuracy = (wins / settled_bets * 100) if settled_bets > 0 else 0.0
        
        return {
            "date": report_date.isoformat(),
            "total_picks": total_picks,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "settled_bets": settled_bets,
            "accuracy": accuracy,
            "profit_loss_units": total_profit_loss_units,
            "profit_loss_dollars": total_profit_loss_dollars,
            "total_wagered_units": total_wagered_units,
            "total_wagered_dollars": total_wagered_dollars,
            "roi": (total_profit_loss_dollars / total_wagered_dollars * 100) if total_wagered_dollars > 0 else 0.0
        }
    
    def _get_cached_results(self, report_date: date) -> Optional[Dict[str, Any]]:
        """Get cached results if available and not expired"""
        if not self.cache_file.exists():
            return None
        
        try:
            with open(self.cache_file, 'r') as f:
                cache = json.load(f)
            
            cache_key = report_date.isoformat()
            cached_entry = cache.get(cache_key)
            
            if not cached_entry:
                return None
            
            # Check if cache is still valid
            cached_time = datetime.fromisoformat(cached_entry.get("cached_at", ""))
            age_hours = (datetime.now() - cached_time).total_seconds() / 3600
            
            if age_hours < self.cache_ttl_hours:
                return cached_entry.get("data")
            else:
                self.log_info(f"Cache expired for {report_date} (age: {age_hours:.1f} hours)")
                return None
                
        except Exception as e:
            self.log_warning(f"Error reading cache: {e}")
            return None
    
    def _cache_results(self, report_date: date, results: Dict[str, Any]) -> None:
        """Cache results for future use"""
        try:
            # Load existing cache
            cache = {}
            if self.cache_file.exists():
                with open(self.cache_file, 'r') as f:
                    cache = json.load(f)
            
            # Add new entry
            cache_key = report_date.isoformat()
            cache[cache_key] = {
                "cached_at": datetime.now().isoformat(),
                "data": results
            }
            
            # Save cache
            with open(self.cache_file, 'w') as f:
                json.dump(cache, f, indent=2)
            
            self.log_info(f"Cached results for {report_date}")
            
        except Exception as e:
            self.log_warning(f"Error caching results: {e}")
    
    def _append_to_master_report(self, stats: Dict[str, Any]) -> None:
        """Append statistics to master performance report"""
        master_report_file = Path("data/reports/master_performance.json")
        master_report_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Load existing master report
            master_report = []
            if master_report_file.exists():
                with open(master_report_file, 'r') as f:
                    master_report = json.load(f)
            
            # Check if entry already exists for this date
            date_str = stats["date"]
            existing_index = None
            for i, entry in enumerate(master_report):
                if entry.get("date") == date_str:
                    existing_index = i
                    break
            
            # Create entry
            entry = {
                "date": date_str,
                "total_picks": stats["total_picks"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "pushes": stats["pushes"],
                "settled_bets": stats.get("settled_bets", 0),
                "accuracy": stats["accuracy"],
                "profit_loss_units": stats["profit_loss_units"],
                "profit_loss_dollars": stats["profit_loss_dollars"],
                "total_wagered_units": stats["total_wagered_units"],
                "total_wagered_dollars": stats["total_wagered_dollars"],
                "roi": stats.get("roi", 0.0)
            }
            
            if existing_index is not None:
                # Update existing entry
                master_report[existing_index] = entry
            else:
                # Append new entry
                master_report.append(entry)
            
            # Sort by date
            master_report.sort(key=lambda x: x.get("date", ""))
            
            # Save master report
            with open(master_report_file, 'w') as f:
                json.dump(master_report, f, indent=2)
            
            self.log_info(f"Appended statistics to master report for {date_str}")
            
        except Exception as e:
            self.log_error(f"Error appending to master report: {e}", exc_info=True)
    
    def generate_daily_report(self, stats: Dict[str, Any]) -> str:
        """Generate a daily results report"""
        lines = [
            "=" * 80,
            f"DAILY RESULTS REPORT - {stats['date']}",
            "=" * 80,
            "",
            f"Report Date: {stats['date']}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "📊 STATISTICS",
            "-" * 80,
            f"Total Picks: {stats['total_picks']}",
            f"Settled Bets: {stats.get('settled_bets', 0)}",
            f"Wins: {stats['wins']}",
            f"Losses: {stats['losses']}",
            f"Pushes: {stats['pushes']}",
            "",
            f"Accuracy: {stats['accuracy']:.1f}%",
            "",
            "💰 PROFIT & LOSS",
            "-" * 80,
            f"Total Wagered (Units): {stats['total_wagered_units']:.2f}",
            f"Total Wagered (Dollars): ${stats['total_wagered_dollars']:.2f}",
            f"Profit/Loss (Units): {stats['profit_loss_units']:+.2f}",
            f"Profit/Loss (Dollars): ${stats['profit_loss_dollars']:+.2f}",
            f"ROI: {stats.get('roi', 0.0):+.2f}%",
            "",
            "=" * 80,
            f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80
        ]
        
        return "\n".join(lines)
    
    def _save_daily_report(self, report_date: date, report_text: str) -> None:
        """Save daily results report to file"""
        reports_dir = Path("data/reports/results_processor")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        filename = reports_dir / f"daily_results_{report_date.isoformat()}.txt"
        
        try:
            with open(filename, 'w') as f:
                f.write(report_text)
            self.log_info(f"Saved daily results report to {filename}")
        except Exception as e:
            self.log_error(f"Error saving daily report: {e}")

