"""Results Processor agent for fetching game results and settling bets"""

from typing import List, Optional, Dict, Any, Tuple
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path

from src.agents.base import BaseAgent
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import Database, BetModel, PickModel, GameModel
from src.data.scrapers.games_scraper import GamesScraper
from sqlalchemy import func
from src.utils.logging import get_logger
from src.utils.team_normalizer import normalize_team_name

logger = get_logger("agents.results_processor")


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
                        except Exception:
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
        
        # Get bets from yesterday
        session = self.db.get_session()
        try:
            picks = session.query(PickModel).filter(
                func.date(PickModel.created_at) == yesterday
            ).all()
            
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
        """Fetch game results from ESPN API and match with database games"""
        games = self.games_scraper.scrape_games(game_date)
        results = {}
        
        self.log_info(f"Fetched {len(games)} games from ESPN for {game_date}")
        
        # Also check database for saved games
        session = self.db.get_session()
        db_game_map = {}
        try:
            # Use with_entities to select only the columns we need
            # This returns tuples instead of ORM objects, avoiding detached instance errors
            db_games = session.query(
                GameModel.id,
                GameModel.team1,
                GameModel.team2
            ).filter(
                func.date(GameModel.date) == game_date
            ).all()
            
            self.log_info(f"Found {len(db_games)} games in database for {game_date}")
            
            # Create a map of team names to game IDs for matching
            # Use normalized team names for better matching across sources
            # Try both orderings (team1/team2 and team2/team1) since order might differ
            # db_games is now a list of tuples (id, team1, team2), not ORM objects
            for game_id, team1, team2 in db_games:
                # Use normalized names for keys
                norm1 = normalize_team_name(team1, for_matching=True)
                norm2 = normalize_team_name(team2, for_matching=True)
                key1 = (norm1, norm2)
                key2 = (norm2, norm1)
                db_game_map[key1] = game_id
                db_game_map[key2] = game_id  # Also map reverse order
        finally:
            session.close()
        
        # Now process games and match with database (db_game_map is a regular dict, doesn't need session)
        final_games = 0
        matched_games = 0
        
        for game in games:
            if game.status == GameStatus.FINAL:
                final_games += 1
                if game.result:
                    # Try to match with database game
                    game_id = game.id
                    if not game_id:
                        # Try to find in database by team names (try both orders)
                        # Use normalized names for matching
                        norm1 = normalize_team_name(game.team1, for_matching=True)
                        norm2 = normalize_team_name(game.team2, for_matching=True)
                        key1 = (norm1, norm2)
                        key2 = (norm2, norm1)
                        game_id = db_game_map.get(key1) or db_game_map.get(key2)
                    
                    if game_id:
                        matched_games += 1
                        results[game_id] = {
                            "game_id": game_id,
                            "team1": game.team1,
                            "team2": game.team2,
                            "status": game.status.value,
                            "result": game.result  # Contains final scores
                        }
                    else:
                        self.log_debug(f"Could not match game: {game.team1} vs {game.team2}")
                else:
                    self.log_debug(f"Game {game.team1} vs {game.team2} is FINAL but has no result data")
        
        self.log_info(f"Found {final_games} final games, matched {matched_games} to database, {len(results)} with results")
        
        return results
    
    def _settle_bets(self, picks: List[PickModel], games_with_results: Dict[int, Dict[str, Any]], session, pick_date: date) -> int:
        """Settle bets based on game results"""
        settled_count = 0
        no_bet_count = 0
        already_settled_count = 0
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
                PickModel.rationale
            ).filter(
                func.date(PickModel.created_at) == pick_date
            ).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or ""
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
                PickModel.rationale
            ).filter(PickModel.id.in_(pick_ids)).all()
            
            picks_data = []
            for pick_id, game_id, bet_type, line, odds, stake_amount, rationale in picks_query:
                picks_data.append({
                    "id": pick_id,
                    "game_id": game_id,
                    "bet_type": bet_type,
                    "line": line or 0.0,
                    "odds": odds,
                    "stake_amount": stake_amount or 0.0,
                    "rationale": rationale or ""
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
            
            # Skip if already settled
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            if not bet:
                no_bet_count += 1
                continue
            
            if bet.result != BetResult.PENDING:
                already_settled_count += 1
                continue  # Already settled
            
            # Get game result
            game_result = games_with_results.get(pick_game_id)
            if not game_result:
                no_game_result_count += 1
                self.logger.debug(f"Game {pick_game_id} not found in results or not final yet")
                continue
            
            # Determine bet result based on pick and game result
            # Pass extracted attributes to avoid detached instance errors
            bet_result = self._determine_bet_result_from_attrs(
                pick_game_id, pick_bet_type, pick_line, pick_rationale, game_result, session
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
            
            settled_count += 1
            # Reconstruct selection text for logging
            bet_type_str = pick_bet_type.value if hasattr(pick_bet_type, 'value') else str(pick_bet_type)
            selection_desc = f"{bet_type_str} {pick_line:+.1f}"
            self.log_info(
                f"Settled bet: {selection_desc} - {bet_result.value} "
                f"(P&L: ${profit_loss:.2f})"
            )
        
        # Log summary
        self.log_info(
            f"Settlement summary: {settled_count} settled, "
            f"{no_bet_count} no bet record, "
            f"{already_settled_count} already settled, "
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
        session
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
            game_home_team = game.team1
            game_away_team = game.team2
        except Exception as e:
            self.log_error(f"Error accessing game attributes: {e}")
            return None
        
        # Determine which team the pick is for by checking rationale
        # The rationale should mention the team name
        rationale_lower = pick_rationale.lower()
        
        pick_team = None
        if bet_type == BetType.SPREAD or bet_type == BetType.MONEYLINE:
            # Check if rationale mentions home or away team
            # Try to match with both the result team names and game team names
            if (home_team.lower() in rationale_lower or 
                game_home_team.lower() in rationale_lower):
                pick_team = "home"
            elif (away_team.lower() in rationale_lower or 
                  game_away_team.lower() in rationale_lower):
                pick_team = "away"
            else:
                # Fallback: use line sign for spread bets
                # Negative line typically means home team favored
                if bet_type == BetType.SPREAD:
                    if line < 0:
                        pick_team = "home"
                    elif line > 0:
                        pick_team = "away"
        
        # If we couldn't determine which team, return None
        if not pick_team and bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
            return None
        
        # Calculate result based on bet type
        if bet_type == BetType.SPREAD:
            if pick_team == "home":
                # Home team spread: home needs to win by more than |line|
                # Line is negative for home team (e.g., -7.5 means home favored by 7.5)
                margin = home_score - away_score
                if margin > abs(line):
                    return BetResult.WIN
                elif margin < abs(line):
                    return BetResult.LOSS
                else:
                    return BetResult.PUSH
            elif pick_team == "away":
                # Away team spread: away needs to lose by less than line (or win)
                # Line is positive for away team (e.g., +7.5 means away getting 7.5 points)
                margin = away_score - home_score
                if margin > line:
                    return BetResult.WIN
                elif margin < line:
                    return BetResult.LOSS
                else:
                    return BetResult.PUSH
        
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

