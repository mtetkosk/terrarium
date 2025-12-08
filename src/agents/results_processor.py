"""Results Processor agent for fetching game results and settling bets"""

from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path

from src.agents.base import BaseAgent
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import Database, BetModel, PickModel, GameModel, TeamModel
from src.data.scrapers.games_scraper import GamesScraper
from sqlalchemy import func
from src.utils.logging import get_logger
from src.utils.team_normalizer import normalize_team_name, get_team_name_variations, determine_home_away_from_result

logger = get_logger("agents.results_processor")


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


class ResultsProcessor(BaseAgent):
    """Results Processor agent for fetching game results and settling bets"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Results Processor agent"""
        super().__init__("ResultsProcessor", db, llm_client)
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
        
        # Get bets from yesterday
        try:
            picks = self.db.get_picks_for_date(yesterday)
            
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
                # Fallback: query with team relationships
                from src.data.storage import TeamModel
                db_games = session.query(
                    GameModel.id,
                    GameModel.team1_id,
                    GameModel.team2_id,
                    GameModel.result
                ).filter(
                    func.date(GameModel.date) == game_date
                ).all()
                
                # Convert team IDs to names
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
            
            # Match scraped games to database games by team names
            # Normalize team names for matching since ESPN uses raw names and database uses normalized names
            from src.utils.team_normalizer import normalize_team_name, are_teams_matching
            
            # Build a list of scraped games with normalized team names for matching
            scraped_games_normalized = []
            for scraped_game in scraped_games:
                if scraped_game.status == GameStatus.FINAL and scraped_game.result:
                    # Normalize team names for matching
                    norm_team1 = normalize_team_name(scraped_game.team1, for_matching=True) if scraped_game.team1 else ""
                    norm_team2 = normalize_team_name(scraped_game.team2, for_matching=True) if scraped_game.team2 else ""
                    if norm_team1 and norm_team2:  # Only add if both team names are valid
                        scraped_games_normalized.append({
                            "team1": scraped_game.team1,  # Keep original for result data
                            "team2": scraped_game.team2,  # Keep original for result data
                            "norm_team1": norm_team1,
                            "norm_team2": norm_team2,
                            "result": scraped_game.result
                        })
            
            self.log_info(f"Found {len(scraped_games_normalized)} final games from ESPN with results for matching")
            
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
                    # Normalize database team names for matching
                    # CRITICAL: Missing team names indicate a data integrity problem
                    if not team1 or not team2:
                        self.log_error(f"CRITICAL: Game {game_id} is missing team names! team1={team1}, team2={team2}. "
                                     f"This indicates a database integrity issue and must be fixed. "
                                     f"Cannot process results for this game.")
                        continue
                    
                    norm_db_team1 = normalize_team_name(team1, for_matching=True)
                    norm_db_team2 = normalize_team_name(team2, for_matching=True)
                    
                    # Try to find scraped result by matching team names (using normalized names)
                    scraped_result = None
                    matched_scraped = None
                    is_reverse_match = False
                    for scraped in scraped_games_normalized:
                        # Check if teams match (in either order)
                        teams_match_forward = (are_teams_matching(norm_db_team1, scraped["norm_team1"]) and
                                             are_teams_matching(norm_db_team2, scraped["norm_team2"]))
                        teams_match_reverse = (are_teams_matching(norm_db_team1, scraped["norm_team2"]) and
                                             are_teams_matching(norm_db_team2, scraped["norm_team1"]))
                        
                        if teams_match_forward or teams_match_reverse:
                            scraped_result = scraped["result"]
                            matched_scraped = scraped
                            is_reverse_match = teams_match_reverse
                            break
                    
                    if scraped_result:
                        # If teams matched in reverse order, swap home/away scores and team names
                        # because ESPN's home/away doesn't match the database's team1/team2 order
                        if is_reverse_match:
                            # Create a new result dict with swapped scores
                            original_result = scraped_result.copy()
                            adjusted_result = {
                                'home_score': original_result.get('away_score', 0),
                                'away_score': original_result.get('home_score', 0),
                                'home_team': original_result.get('away_team', ''),
                                'away_team': original_result.get('home_team', '')
                            }
                            # Also swap team IDs if present
                            if 'home_team_id' in original_result or 'away_team_id' in original_result:
                                adjusted_result['home_team_id'] = original_result.get('away_team_id')
                                adjusted_result['away_team_id'] = original_result.get('home_team_id')
                            # Preserve any other fields
                            for key, value in original_result.items():
                                if key not in ['home_score', 'away_score', 'home_team', 'away_team', 'home_team_id', 'away_team_id']:
                                    adjusted_result[key] = value
                            scraped_result = adjusted_result
                            self.logger.debug(f"Reversed scores for game {game_id} due to reverse team match")
                        
                        results[game_id] = {
                            "game_id": game_id,
                            "team1": team1,
                            "team2": team2,
                            "status": "final",
                            "result": scraped_result
                        }
                        matched_count += 1
                        match_direction = "REVERSE" if is_reverse_match else "FORWARD"
                        self.logger.debug(f"Matched game {game_id} ({match_direction}): DB[{team1} vs {team2}] <-> ESPN[{matched_scraped['team1']} vs {matched_scraped['team2']}]")
                    else:
                        self.logger.debug(f"Could not match game {game_id}: DB[{team1} vs {team2}] (normalized: [{norm_db_team1} vs {norm_db_team2}])")
            
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
        home_score = safe_int(result_data.get("home_score", 0), 0)
        away_score = safe_int(result_data.get("away_score", 0), 0)
        home_team_id = result_data.get("home_team_id")  # May not be in result yet
        away_team_id = result_data.get("away_team_id")  # May not be in result yet
        home_team_name = result_data.get("home_team", "")
        away_team_name = result_data.get("away_team", "")
        
        # Get game to determine which team is home/away
        from src.data.storage import GameModel, TeamModel
        game = session.query(GameModel).filter_by(id=pick_game_id).first()
        if not game:
            return None
        
        # Determine which database team is home/away using utility function
        home_away_result = determine_home_away_from_result(
            game.team1_id, game.team2_id, result_data, session
        )
        
        if home_away_result is None:
            # Couldn't determine home/away - fall back to old method
            return None
        
        team1_is_home, team2_is_home = home_away_result
        
        # Determine if pick_team_id is home or away
        if game.team1_id == pick_team_id:
            pick_team_is_home = team1_is_home
            pick_team_is_away = not team1_is_home
        elif game.team2_id == pick_team_id:
            pick_team_is_home = team2_is_home
            pick_team_is_away = not team2_is_home
        else:
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
                PickModel.team_id,
                PickModel.book,
                PickModel.selection_text
            ).filter(
                func.date(PickModel.created_at) == pick_date
            ).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale, team_id, book, selection_text in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or "",
                    "team_id": team_id,
                    "book": book or "",
                    "selection_text": selection_text or ""
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
                PickModel.team_id,
                PickModel.book,
                PickModel.selection_text
            ).filter(PickModel.id.in_(pick_ids)).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale, team_id, book, selection_text in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or "",
                    "team_id": team_id,
                    "book": book or "",
                    "selection_text": selection_text or ""
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
            
            # For TOTAL bets, look up the betting line from database to get correct line and over/under direction
            betting_line_team = None  # Will be "over" or "under" for totals
            actual_line = pick_line  # Default to pick line, but will be updated from betting_lines if found
            
            if pick_bet_type == BetType.TOTAL:
                # CRITICAL: Look up betting line from database - this is the ONLY source of truth
                from src.data.storage import BettingLineModel
                pick_book = pick_data.get("book", "").lower() if pick_data.get("book") else ""
                
                if not pick_book:
                    self.log_error(
                        f"Cannot settle TOTAL bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"pick has no book specified. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
                
                # Parse selection_text to determine over/under direction
                # This is needed to match the correct betting line (only use for this purpose, not for grading)
                expected_direction = None
                if pick_selection_text:
                    import re
                    match = re.search(r'(over|under)\s+(\d+\.?\d*)', pick_selection_text.lower())
                    if match:
                        expected_direction = match.group(1).lower()
                if not expected_direction and pick_rationale:
                    rationale_lower = pick_rationale.lower()
                    if "over" in rationale_lower:
                        expected_direction = "over"
                    elif "under" in rationale_lower:
                        expected_direction = "under"
                
                if not expected_direction:
                    self.log_error(
                        f"Cannot settle TOTAL bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"could not determine over/under direction from selection_text: '{pick_selection_text}'. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
                
                # Match betting line by game_id, bet_type, book, AND direction
                betting_line = session.query(BettingLineModel).filter(
                    BettingLineModel.game_id == pick_game_id,
                    BettingLineModel.bet_type == BetType.TOTAL,
                    BettingLineModel.book == pick_book,
                    BettingLineModel.team == expected_direction
                ).first()
                
                if betting_line:
                    actual_line = betting_line.line
                    betting_line_team = betting_line.team
                else:
                    # Fall back to pick data when no matching betting line is stored
                    actual_line = pick_line
                    betting_line_team = expected_direction
                
                if not betting_line_team or betting_line_team.lower() not in ["over", "under"]:
                    self.log_error(
                        f"Cannot settle TOTAL bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"betting line team field is invalid: '{betting_line_team}'. Expected 'over' or 'under'. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
                
                self.logger.debug(
                    f"Found betting line for pick {pick_id}: {betting_line_team.upper()} {actual_line} "
                    f"(matched from selection_text: {pick_selection_text})"
                )
            
            # Determine bet result based on pick and game result
            # NO FALLBACK LOGIC - if we can't determine the result, fail loudly
            pick_team_id = pick_data.get("team_id")
            
            if pick_bet_type == BetType.TOTAL:
                # For totals, use betting_line_team from database (REQUIRED)
                if not betting_line_team:
                    self.log_error(
                        f"Cannot settle TOTAL bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"betting_line_team is required but not found in database. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
                
                # Calculate total score
                home_score = safe_int(game_result.get("result", {}).get("home_score", 0), 0)
                away_score = safe_int(game_result.get("result", {}).get("away_score", 0), 0)
                total_score = home_score + away_score
                
                betting_line_team_lower = betting_line_team.lower().strip()
                if betting_line_team_lower == "under":
                    if total_score < actual_line:
                        bet_result = BetResult.WIN
                    elif total_score > actual_line:
                        bet_result = BetResult.LOSS
                    else:
                        bet_result = BetResult.PUSH
                elif betting_line_team_lower == "over":
                    if total_score > actual_line:
                        bet_result = BetResult.WIN
                    elif total_score < actual_line:
                        bet_result = BetResult.LOSS
                    else:
                        bet_result = BetResult.PUSH
                else:
                    self.log_error(
                        f"Cannot settle TOTAL bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"Invalid betting_line_team: '{betting_line_team}'. Expected 'over' or 'under'. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
            else:
                # For spread/moneyline, use team_id (REQUIRED)
                if not pick_team_id:
                    game = session.query(GameModel).filter_by(id=pick_game_id).first()
                    if game:
                        if pick_line < 0:
                            pick_team_id = game.team1_id
                        elif pick_line > 0:
                            pick_team_id = game.team2_id
                        else:
                            pick_team_id = game.team1_id
                    if not pick_team_id:
                        self.log_error(
                            f"Cannot settle {pick_bet_type.value} bet for pick {pick_id} (game_id={pick_game_id}): "
                            f"team_id is required but is None. This indicates a data integrity issue. Skipping."
                        )
                        no_bet_result_count += 1
                        continue
                
                bet_result = self._determine_bet_result_from_team_id(
                    pick_game_id, pick_bet_type, actual_line, pick_team_id, game_result, session
                )
                
                if not bet_result:
                    # This means team_id doesn't match game teams - data integrity issue
                    game = session.query(GameModel).filter_by(id=pick_game_id).first()
                    pick_team = session.query(TeamModel).filter_by(id=pick_team_id).first()
                    game_team1 = session.query(TeamModel).filter_by(id=game.team1_id).first() if game else None
                    game_team2 = session.query(TeamModel).filter_by(id=game.team2_id).first() if game else None
                    
                    self.log_error(
                        f"Cannot settle {pick_bet_type.value} bet for pick {pick_id} (game_id={pick_game_id}): "
                        f"Pick team_id={pick_team_id} ({pick_team.normalized_team_name if pick_team else 'Unknown'}) "
                        f"does not match game teams (team1_id={game.team1_id if game else None} "
                        f"({game_team1.normalized_team_name if game_team1 else 'Unknown'}), "
                        f"team2_id={game.team2_id if game else None} "
                        f"({game_team2.normalized_team_name if game_team2 else 'Unknown'})). "
                        f"This is a DATA INTEGRITY ERROR. Pick must be fixed before it can be graded. Skipping."
                    )
                    no_bet_result_count += 1
                    continue
            
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

