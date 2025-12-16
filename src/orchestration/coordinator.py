"""Coordinator for agent workflow"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta

from src.data.models import (
    Game, BettingLine, GameInsight, Prediction, Pick, Bet,
    CardReview, BetType, RevisionRequest, RevisionRequestType,
    BetResult
)
from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.storage import Database, GameModel, BettingLineModel, BetModel, PickModel
from src.agents.researcher import Researcher
from src.agents.modeler import Modeler
from src.agents.picker import Picker
from src.agents.president import President
from src.agents.auditor import Auditor
from src.agents.results_processor import ResultsProcessor
from src.orchestration.data_converter import DataConverter
from src.orchestration.prediction_persistence import PredictionPersistenceService
from src.orchestration.persistence_service import PersistenceService
from src.utils.logging import get_logger
from src.utils.reporting import ReportGenerator
from src.utils.google_sheets import GoogleSheetsService

logger = get_logger("orchestration.coordinator")


class Coordinator:
    """Coordinates agent workflow"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize coordinator"""
        self.db = db or Database()
        
        # Initialize scrapers
        self.games_scraper = GamesScraper()
        self.lines_scraper = LinesScraper()
        
        # Initialize agents
        self.results_processor = ResultsProcessor(self.db)
        self.researcher = Researcher(self.db)
        self.modeler = Modeler(self.db)
        self.picker = Picker(self.db)
        self.president = President(self.db)
        self.auditor = Auditor(self.db)
        
        # Initialize utilities (create once, reuse throughout)
        self.data_converter = DataConverter()
        self.report_generator = ReportGenerator(self.db)
        self.google_sheets_service = GoogleSheetsService(self.db)
        
        # Initialize persistence services
        self.persistence_service = PersistenceService(self.db)
        self.prediction_persistence_service = PredictionPersistenceService(self.db)
    
    def run_daily_workflow(self, target_date: Optional[date] = None, max_revisions: int = 2, test_limit: Optional[int] = None, force_refresh: bool = False, single_game_id: Optional[int] = None) -> CardReview:
        """Run the daily betting workflow with revision support
        
        Args:
            target_date: Date to run workflow for (default: today)
            max_revisions: Maximum number of revision cycles (default: 2)
            test_limit: If set, limit processing to this many games (default: None for all games)
            force_refresh: If True, bypass cache and fetch fresh data
            single_game_id: If set, process only this specific game ID
        """
        if target_date is None:
            target_date = date.today()
        
        logger.info("=" * 80)
        logger.info(f"🚀 STARTING DAILY WORKFLOW FOR {target_date}")
        if single_game_id is not None:
            logger.info(f"🎯 SINGLE GAME MODE: Processing only game ID {single_game_id}")
        elif test_limit is not None:
            logger.info(f"🧪 TEST MODE: Processing only first {test_limit} games")
        from src.utils.config import config
        if config.is_debug_mode():
            logger.info("🐛 DEBUG MODE ENABLED: Detailed data logging active")
        logger.info("=" * 80)
        
        # Reset token usage tracking at start of workflow
        self._reset_all_agent_token_usage()
        
        try:
            # Step 0: Process yesterday's results
            self._step_process_results(target_date, force_refresh)
            
            # Step 1: Scrape games
            games = self._step_scrape_games(target_date, test_limit, single_game_id)
            if not games:
                logger.warning("No games found. Ending workflow.")
                return CardReview(
                    date=target_date,
                    approved=False,
                    picks_approved=[],
                    picks_rejected=[],
                    review_notes="No games found for today."
                )
            
            # Step 2: Scrape betting lines
            lines = self._step_scrape_lines(games)
            
            # Filter out games that don't have betting lines
            games_with_lines = set(line.game_id for line in lines if line.game_id)
            original_game_count = len(games)
            games = [g for g in games if g.id in games_with_lines]
            filtered_count = original_game_count - len(games)
            
            if filtered_count > 0:
                logger.warning(f"⚠️  Filtered out {filtered_count} games without betting lines (from {original_game_count} total)")
                logger.info(f"📊 Continuing with {len(games)} games that have betting lines")
            
            if not games:
                logger.warning("No games with betting lines found. Ending workflow.")
                return CardReview(
                    date=target_date,
                    approved=False,
                    picks_approved=[],
                    picks_rejected=[],
                    review_notes="No games with betting lines available."
                )
            
            # Main workflow
            insights = None
            predictions = None
            picks = None
            candidate_picks = None
            review = None
            president_response = None
            
            # Step 3: Researcher researches games
            insights = self._step_research(games, target_date, lines, force_refresh)
            
            # Step 4: Modeler generates predictions
            predictions = self._step_model(insights, lines, target_date, force_refresh)
            
            # Get historical performance data for learning
            historical_performance = self.db.get_historical_performance(target_date)
            
            # Step 5: Picker selects picks (one per game)
            picks, candidate_picks = self._step_pick(predictions, insights, lines, games, target_date, historical_performance)
            if picks is None:
                logger.warning("No picks selected. Ending workflow.")
                return CardReview(
                    date=target_date,
                    approved=False,
                    picks_approved=[],
                    picks_rejected=[],
                    review_notes="No picks met selection criteria."
                )
            
            # Step 6: President assigns units, selects best bets, and generates report
            review, president_response = self._step_president(
                candidate_picks, insights, predictions, target_date, historical_performance
            )
            
            # Step 7: Save betting card and place bets (if approved)
            self._step_finalize(review, picks, candidate_picks, insights, predictions, president_response, target_date)
            
            # Step 10: Generate daily report
            self._step_audit(target_date)
            
            # Log total token usage summary
            self._log_token_usage_summary()
            
            logger.info("=" * 80)
            logger.info(f"✅ DAILY WORKFLOW COMPLETED - Card {'APPROVED' if review.approved else 'REJECTED'}")
            logger.info("=" * 80)
            return review
            
        except Exception as e:
            logger.error(f"❌ Error in daily workflow: {e}", exc_info=True)
            # Log token usage even on error
            self._log_token_usage_summary()
            return CardReview(
                date=target_date,
                approved=False,
                picks_approved=[],
                picks_rejected=[],
                review_notes=f"Workflow error: {str(e)}"
            )
    
    def _step_process_results(self, target_date: date, force_refresh: bool) -> Dict[str, Any]:
        """Step 0: Process yesterday's results"""
        yesterday = target_date - timedelta(days=1)
        self.results_processor.interaction_logger.log_agent_start(
            "ResultsProcessor", 
            f"Processing results for {yesterday}"
        )
        yesterday_stats = self.results_processor.process(target_date, force_refresh=force_refresh)
        self.results_processor.interaction_logger.log_agent_complete(
            "ResultsProcessor",
            f"Processed {yesterday_stats.get('settled_bets', 0)} settled bets, "
            f"Accuracy: {yesterday_stats.get('accuracy', 0):.1f}%, "
            f"P&L: ${yesterday_stats.get('profit_loss_dollars', 0):+.2f}"
        )
        
        # Save result analytics for games with results
        session = self.db.get_session()
        try:
            from src.data.storage import GameModel
            from sqlalchemy import func
            
            # Get all games from yesterday that have results
            # Use with_entities to select only the ID column to avoid detached instance errors
            game_ids = session.query(GameModel.id).filter(
                func.date(GameModel.date) == yesterday,
                GameModel.result.isnot(None)
            ).all()
            
            # Extract IDs from tuples (with_entities returns tuples)
            game_ids = [game_id for (game_id,) in game_ids if game_id]
            
            # Result analytics are now stored directly in GameModel.result JSON - no need for separate analytics table
            pass
        finally:
            session.close()
        
        # Write picks to Google Sheets for yesterday (after results are processed)
        try:
            self.google_sheets_service.write_picks_to_sheet(yesterday)
        except Exception as e:
            logger.warning(f"Could not write picks to Google Sheets: {e}")
        
        return yesterday_stats
    
    def _step_scrape_games(self, target_date: date, test_limit: Optional[int], single_game_id: Optional[int] = None) -> List[Game]:
        """Step 1: Scrape games"""
        from src.utils.logging import log_data_object
        from src.utils.config import config
        
        self.researcher.interaction_logger.log_agent_start("GamesScraper", f"Scraping games for {target_date}")
        games = self.games_scraper.scrape_games(target_date)
        games = self.persistence_service.save_games(games)
        
        # CRITICAL: Deduplicate games by normalized matchup to ensure only one game per unique matchup
        # This prevents duplicate games from being passed to modeler/picker/president
        games = self._deduplicate_games_by_matchup(games)
        
        # Filter to single game if specified
        if single_game_id is not None:
            original_count = len(games)
            games = [g for g in games if g.id == single_game_id]
            if not games:
                logger.warning(f"🎯 SINGLE GAME MODE: Game ID {single_game_id} not found in {original_count} games")
            else:
                logger.info(f"🎯 SINGLE GAME MODE: Filtered to game ID {single_game_id} from {original_count} games")
                if config.is_debug_mode():
                    log_data_object(logger, f"Single game (ID {single_game_id})", games[0] if games else None)
        
        # Limit games in test mode
        if test_limit is not None and len(games) > test_limit:
            original_count = len(games)
            games = games[:test_limit]
            logger.info(f"🧪 TEST MODE: Limited games from {original_count} to {len(games)}")
            self.researcher.interaction_logger.log_agent_complete("GamesScraper", f"Found {original_count} games (limited to {len(games)} for testing)")
        else:
            self.researcher.interaction_logger.log_agent_complete("GamesScraper", f"Found {len(games)} games")
        
        if config.is_debug_mode():
            log_data_object(logger, "All games after scraping", games)
        
        # Note: Home/away teams are now determined dynamically from GameModel using utility functions
        # No need to save to AnalyticsGameModel anymore
        
        return games
    
    def _step_scrape_lines(self, games: List[Game]) -> List[BettingLine]:
        """Step 2: Scrape betting lines"""
        self.researcher.interaction_logger.log_agent_start("LinesScraper", f"Scraping lines for {len(games)} games")
        lines = self.lines_scraper.scrape_lines(games)
        lines = self.persistence_service.save_lines(lines, games)
        self.researcher.interaction_logger.log_agent_complete("LinesScraper", f"Found {len(lines)} betting lines")
        
        # Save odds analytics (will be updated after home/away is determined)
        # Odds are now stored directly in BettingLineModel - no need for separate analytics table
        
        return lines
    
    def _step_research(self, games: List[Game], target_date: date, lines: List[BettingLine], force_refresh: bool) -> Dict[str, Any]:
        """Step 3: Researcher researches games"""
        from src.utils.logging import log_data_object
        from src.utils.config import config
        
        self.researcher.interaction_logger.log_agent_start("Researcher", f"Researching {len(games)} games")
        self.researcher.interaction_logger.log_handoff("GamesScraper", "Researcher", "Games", len(games))
        
        if config.is_debug_mode():
            log_data_object(logger, "Games input to Researcher", games)
            log_data_object(logger, "Betting lines input to Researcher", lines)
        
        insights = self.researcher.process(games, target_date=target_date, betting_lines=lines, force_refresh=force_refresh)
        
        if config.is_debug_mode():
            log_data_object(logger, "Researcher insights output", insights)
        
        # CRITICAL VALIDATION: Ensure all games are passed through
        insights_games = insights.get("games", [])
        if len(insights_games) != len(games):
            logger.error(
                f"CRITICAL: Game count mismatch in Researcher! "
                f"Expected {len(games)} games, got {len(insights_games)} insights. "
                f"This should not happen - Researcher should return insights for ALL games."
            )
        else:
            logger.info(f"✅ Researcher validation passed: {len(insights_games)}/{len(games)} games")
        
        self.researcher.interaction_logger.log_agent_complete("Researcher", f"Generated {len(insights_games)} insights")
        
        # Save Researcher report
        self.report_generator.save_agent_report(
            "researcher",
            insights if isinstance(insights, dict) else {"games": insights},
            target_date,
            metadata={"games_researched": len(games), "insights_generated": len(insights_games)}
        )
        
        # Extract home/away teams and conferences from researcher output and update analytics_games
        self._extract_and_save_home_away(insights, games, target_date)
        
        # Save insights to GameInsightModel for KenPom ranks and other stats
        self._save_game_insights(insights, games, target_date)
        
        # Update odds analytics now that we have home/away information
        # Odds are now stored directly in BettingLineModel - no need for separate analytics table
        
        return insights
    
    def _extract_and_save_home_away(self, insights: Dict[str, Any], games: List[Game], target_date: date) -> None:
        """Extract home/away teams and conferences from researcher output and save to analytics"""
        insights_games = insights.get("games", [])
        
        # Create a map of game_id to game object
        game_map = {g.id: g for g in games if g.id}
        
        for insight_game in insights_games:
            game_id_str = insight_game.get("game_id")
            if not game_id_str:
                continue
            
            # Try to parse game_id (could be string or int)
            try:
                game_id = int(game_id_str)
            except (ValueError, TypeError):
                # Try to match by team names
                teams_data = insight_game.get("teams", {})
                home_team_name = teams_data.get("home", "")
                away_team_name = teams_data.get("away", "")
                
                # Find matching game
                matching_game = None
                for game in games:
                    if (home_team_name and away_team_name and
                        ((home_team_name.lower() in game.team1.lower() and away_team_name.lower() in game.team2.lower()) or
                         (home_team_name.lower() in game.team2.lower() and away_team_name.lower() in game.team1.lower()))):
                        matching_game = game
                        break
                
                if not matching_game or not matching_game.id:
                    continue
                game_id = matching_game.id
            
            # Extract teams from researcher output
            teams_data = insight_game.get("teams", {})
            home_team = teams_data.get("home", "")
            away_team = teams_data.get("away", "")
            
            # Extract conferences from advanced stats
            adv_data = insight_game.get("adv", {})
            home_conference = None
            away_conference = None
            
            if isinstance(adv_data, dict):
                home_stats = adv_data.get("home", {})
                away_stats = adv_data.get("away", {})
                
                if isinstance(home_stats, dict):
                    home_conference = home_stats.get("conference")
                if isinstance(away_stats, dict):
                    away_conference = away_stats.get("conference")
            
            # Note: Home/away teams are now determined dynamically from GameModel using utility functions
            # No need to save to AnalyticsGameModel anymore
            # Conference information from researcher is still available in the insights but not stored separately
    
    def _save_game_insights(self, insights: Dict[str, Any], games: List[Game], target_date: date) -> None:
        """Save researcher insights to GameInsightModel for KenPom ranks and other stats"""
        if not self.db:
            return
        
        insights_games = insights.get("games", [])
        if not insights_games:
            return
        
        session = self.db.get_session()
        try:
            from src.data.storage import GameInsightModel
            from src.utils.team_normalizer import normalize_team_name
            
            # Create a map of game_id to game object
            game_map = {g.id: g for g in games if g.id}
            
            for insight_game in insights_games:
                game_id_str = insight_game.get("game_id")
                if not game_id_str:
                    continue
                
                # Try to parse game_id (could be string or int)
                try:
                    game_id = int(game_id_str)
                except (ValueError, TypeError):
                    # Try to match by team names
                    teams_data = insight_game.get("teams", {})
                    home_team_name = teams_data.get("home", "")
                    away_team_name = teams_data.get("away", "")
                    
                    # Find matching game
                    matching_game = None
                    for game in games:
                        if (home_team_name and away_team_name and
                            ((home_team_name.lower() in game.team1.lower() and away_team_name.lower() in game.team2.lower()) or
                             (home_team_name.lower() in game.team2.lower() and away_team_name.lower() in game.team1.lower()))):
                            matching_game = game
                            break
                    
                    if not matching_game or not matching_game.id:
                        continue
                    game_id = matching_game.id
                
                if game_id not in game_map:
                    continue
                
                game = game_map[game_id]
                
                # Get team names from game to determine which is team1 (home) and team2 (away)
                # Standard format: team2 @ team1 (away @ home)
                team1_name = game.team1  # home
                team2_name = game.team2  # away
                
                # Extract advanced stats from researcher output
                adv_data = insight_game.get("adv", {})
                home_stats = adv_data.get("home", {}) if isinstance(adv_data, dict) else {}
                away_stats = adv_data.get("away", {}) if isinstance(adv_data, dict) else {}
                
                # Map home/away stats to team1/team2
                # team1 = home, team2 = away
                team1_stats = home_stats if isinstance(home_stats, dict) else {}
                team2_stats = away_stats if isinstance(away_stats, dict) else {}
                
                # Extract other insight data
                matchup_notes = insight_game.get("matchup_notes", "") or insight_game.get("context", [])
                if isinstance(matchup_notes, list):
                    matchup_notes = " | ".join(matchup_notes)
                
                confidence_factors = insight_game.get("confidence_factors", {})
                rest_days_team1 = insight_game.get("rest_days_team1")
                rest_days_team2 = insight_game.get("rest_days_team2")
                travel_impact = insight_game.get("travel_impact")
                rivalry = insight_game.get("rivalry", False)
                
                # Get or create GameInsightModel
                insight_model = session.query(GameInsightModel).filter_by(game_id=game_id).first()
                if insight_model:
                    # Update existing
                    insight_model.team1_stats = team1_stats
                    insight_model.team2_stats = team2_stats
                    insight_model.matchup_notes = matchup_notes
                    insight_model.confidence_factors = confidence_factors
                    insight_model.rest_days_team1 = rest_days_team1
                    insight_model.rest_days_team2 = rest_days_team2
                    insight_model.travel_impact = travel_impact
                    insight_model.rivalry = rivalry
                else:
                    # Create new
                    insight_model = GameInsightModel(
                        game_id=game_id,
                        team1_stats=team1_stats,
                        team2_stats=team2_stats,
                        matchup_notes=matchup_notes,
                        confidence_factors=confidence_factors,
                        rest_days_team1=rest_days_team1,
                        rest_days_team2=rest_days_team2,
                        travel_impact=travel_impact,
                        rivalry=rivalry
                    )
                    session.add(insight_model)
            
            session.commit()
            logger.info(f"💾 Saved {len(insights_games)} game insights to database")
            
        except Exception as e:
            logger.error(f"Error saving game insights: {e}", exc_info=True)
            session.rollback()
        finally:
            session.close()
    
    def _step_model(self, insights: Dict[str, Any], lines: List[BettingLine], target_date: date, force_refresh: bool = False) -> Dict[str, Any]:
        """Step 4: Modeler generates predictions"""
        insights_games = insights.get("games", [])
        self.modeler.interaction_logger.log_agent_start("Modeler", f"Modeling {len(insights_games)} games")
        self.modeler.interaction_logger.log_handoff("Researcher", "Modeler", "GameInsights", len(insights_games))
        predictions = self.modeler.process(insights, betting_lines=lines, target_date=target_date, force_refresh=force_refresh)
        
        # Save predictions using persistence service
        game_models = predictions.get("game_models", [])
        self.prediction_persistence_service.save_predictions(predictions, target_date)
        
        # CRITICAL VALIDATION: Ensure all games from Researcher are modeled
        if len(game_models) != len(insights_games):
            logger.error(
                f"CRITICAL: Game count mismatch in Modeler! "
                f"Expected {len(insights_games)} games from Researcher, got {len(game_models)} models. "
                f"This should not happen - Modeler should return models for ALL games."
            )
        else:
            logger.info(f"✅ Modeler validation passed: {len(game_models)}/{len(insights_games)} games")
        
        self.modeler.interaction_logger.log_agent_complete("Modeler", f"Generated {len(game_models)} predictions")
        
        # Save Modeler report
        self.report_generator.save_agent_report(
            "modeler",
            predictions,
            target_date,
            metadata={"games_modeled": len(insights_games), "predictions_generated": len(game_models)}
        )
        return predictions
    
    def _step_pick(self, predictions: Dict[str, Any], insights: Dict[str, Any], lines: List[BettingLine], games: List[Game], target_date: date, historical_performance: Optional[Dict[str, Any]] = None) -> tuple[Optional[List[Pick]], List[Dict[str, Any]]]:
        """Step 5: Picker selects picks
        
        Returns:
            Tuple of (picks, candidate_picks) or (None, []) if no picks selected
        """
        self.picker.interaction_logger.log_agent_start("Picker", f"Selecting from {len(predictions.get('game_models', []))} predictions")
        self.picker.interaction_logger.log_handoff("Modeler", "Picker", "Predictions", len(predictions.get('game_models', [])))
        
        # Pass arguments: researcher_output, modeler_output, historical_performance
        picker_response = self.picker.process(insights, predictions, historical_performance)
        candidate_picks = picker_response.get("candidate_picks", [])
        self.picker.interaction_logger.log_agent_complete("Picker", f"Selected {len(candidate_picks)} picks")
        
        # Save Picker report
        self.report_generator.save_agent_report(
            "picker",
            picker_response,
            target_date,
            metadata={"candidate_picks": len(candidate_picks)}
        )
        
        if not candidate_picks:
            return None, []
        
        # Convert candidate picks to Pick objects
        picks = self.data_converter.picks_from_json(candidate_picks, games)
        return picks, candidate_picks
    
    def _step_president(self, candidate_picks: List[Dict[str, Any]], 
                       insights: Dict[str, Any], predictions: Dict[str, Any], 
                       target_date: date, historical_performance: Optional[Dict[str, Any]] = None) -> tuple[CardReview, Dict[str, Any]]:
        """Step 6: President assigns units, selects best bets, and generates report"""
        self.president.interaction_logger.log_agent_start("President", f"Assigning units and selecting best bets from {len(candidate_picks)} picks")
        self.president.interaction_logger.log_handoff("Picker", "President", "CandidatePicks", len(candidate_picks))
        
        president_response = self.president.process(
            candidate_picks,
            researcher_output=insights,
            modeler_output=predictions,
            auditor_feedback=historical_performance
        )
        
        # Convert President's JSON response to CardReview object
        # Convert president response to CardReview
        # All picks are approved, with units and best_bet flags from President
        approved_picks = president_response.get("approved_picks", [])
        
        # Create CardReview - all picks are approved
        review = CardReview(
            date=target_date,
            approved=True,  # All picks are approved by default
            picks_approved=[p.get("game_id") for p in approved_picks],
            picks_rejected=[],  # No rejected picks
            review_notes=president_response.get("daily_report_summary", {}).get("strategic_notes", [])
        )
        
        return review, president_response
    
    def _step_finalize(self, review: CardReview, picks: List[Pick], candidate_picks: List[Dict[str, Any]], 
                      insights: Dict[str, Any], predictions: Dict[str, Any], president_response: Dict[str, Any], target_date: date) -> None:
        """Step 7: Save betting card and place bets"""
        # Update picks with units and best_bet flags from President response
        approved_picks_data = president_response.get("approved_picks", [])
        # Match by both game_id and bet_type to avoid mismatches
        from src.data.models import BetType
        from src.orchestration.data_converter import DataConverter
        
        approved_by_key = {}
        for p in approved_picks_data:
            game_id_str = str(p.get("game_id", ""))
            bet_type_str = p.get("bet_type", "").lower()
            try:
                bet_type = DataConverter.parse_bet_type(bet_type_str)
                key = (game_id_str, bet_type.value if hasattr(bet_type, 'value') else str(bet_type))
                approved_by_key[key] = p
            except (ValueError, AttributeError) as e:
                logger.warning(f"Could not parse bet_type '{bet_type_str}' for game_id {game_id_str}: {e}")
                # Fallback to game_id-only matching if bet_type parsing fails
                approved_by_key[game_id_str] = p
        
        # Log all approved picks from president for debugging
        logger.info(f"President approved {len(approved_picks_data)} picks")
        for p in approved_picks_data:
            logger.debug(f"  President approved: game_id={p.get('game_id')}, bet_type={p.get('bet_type')}, best_bet={p.get('best_bet')}, units={p.get('units')}")
        
        # Log all picks in the picks list for debugging
        logger.info(f"Processing {len(picks)} picks from picker")
        for pick in picks:
            logger.debug(f"  Picker pick: game_id={pick.game_id}, bet_type={pick.bet_type.value}, selection_text={pick.selection_text}")
        
        # Update picks with units and best_bet from President
        # Match by (game_id, bet_type) exactly - this is the only safe way
        matched_count = 0
        for pick in picks:
            bet_type_str = pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)
            key = (str(pick.game_id), bet_type_str)
            president_pick = approved_by_key.get(key)
            
            if president_pick:
                # Exact match - update this pick
                old_best_bet = pick.best_bet
                old_units = pick.stake_units
                pick.stake_units = president_pick.get("units", 1.0)
                pick.best_bet = president_pick.get("best_bet", False)
                # Update selection_text from president's response if available
                if president_pick.get("selection"):
                    pick.selection_text = president_pick.get("selection")
                # Update rationale with President's reasoning if available
                if president_pick.get("final_decision_reasoning"):
                    pick.rationale = f"{pick.rationale}\n\nPresident's Analysis: {president_pick.get('final_decision_reasoning')}"
                matched_count += 1
                logger.info(
                    f"Matched pick game_id={pick.game_id} bet_type={bet_type_str}: "
                    f"best_bet={old_best_bet}->{pick.best_bet}, units={old_units}->{pick.stake_units}"
                )
            else:
                # No exact match found - this pick was not approved by president
                # Set best_bet to False to ensure it's not marked as a best bet
                if pick.best_bet:
                    logger.warning(
                        f"Pick game_id={pick.game_id} bet_type={bet_type_str} was not matched to any president approval. "
                        f"Setting best_bet=False to avoid data corruption."
                    )
                    pick.best_bet = False
        
        # Check for president approvals that didn't match any picks
        unmatched_president_picks = []
        for key, president_pick in approved_by_key.items():
            if isinstance(key, tuple):
                game_id_str, bet_type_str = key
                # Check if any pick matched this
                matched = any(
                    str(pick.game_id) == game_id_str and 
                    (pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)) == bet_type_str
                    for pick in picks
                )
                if not matched:
                    unmatched_president_picks.append(president_pick)
        
        if unmatched_president_picks:
            unmatched_details = [
                f"game_id={p.get('game_id')} bet_type={p.get('bet_type')}"
                for p in unmatched_president_picks
            ]
            logger.error(
                f"CRITICAL: {len(unmatched_president_picks)} president-approved picks did not match any picker picks! "
                f"This indicates a data integrity issue. Unmatched picks: {unmatched_details}"
            )
        
        logger.info(f"Matched {matched_count}/{len(picks)} picks to president approvals")
        
        # Save all picks to database
        # Log what we're about to save for debugging
        logger.info(f"Saving {len(picks)} picks to database:")
        for pick in picks:
            logger.info(
                f"  Saving pick: game_id={pick.game_id}, bet_type={pick.bet_type.value}, "
                f"best_bet={pick.best_bet}, units={pick.stake_units}, "
                f"selection_text={pick.selection_text}"
            )
        
        for pick in picks:
            self.persistence_service.save_pick(pick, target_date=target_date)
            if pick.id:
                self.persistence_service.update_pick_stakes(pick)
        
        # Verify what was actually saved (query back from database)
        if self.db:
            session = self.db.get_session()
            try:
                from src.data.storage import PickModel
                saved_picks = session.query(PickModel).filter(
                    PickModel.pick_date == target_date
                ).all()
                logger.info(f"Verified {len(saved_picks)} picks in database after saving:")
                for saved_pick in saved_picks:
                    logger.info(
                        f"  DB pick: game_id={saved_pick.game_id}, bet_type={saved_pick.bet_type.value}, "
                        f"best_bet={saved_pick.best_bet}, selection_text={saved_pick.selection_text}"
                    )
            finally:
                session.close()
        
        if review.approved:
            # All picks are approved, but only best bets are placed
            best_bet_picks = [p for p in picks if p.best_bet]
            
            logger.info(f"💰 Placing {len(best_bet_picks)} best bet bets")
            self.persistence_service.place_bets(best_bet_picks)
        else:
            logger.warning("❌ Card not approved - no bets placed")
        
        # Generate and save President's comprehensive report
        presidents_report = self.report_generator.generate_presidents_report(
            picks,
            review,
            target_date,
            president_response=president_response,
            researcher_output=insights,
            modeler_output=predictions
        )
        report_path = self.report_generator.save_report_to_file(
            presidents_report,
            f"presidents_report_{target_date.isoformat()}.txt",
            output_dir="data/reports/president"
        )
        logger.info(f"📝 President's report saved to {report_path}")
    
    def _step_audit(self, target_date: date) -> None:
        """Step 10: Generate daily report (review previous day's results)"""
        logger.info("📊 Generating daily performance report")
        self.auditor.interaction_logger.log_agent_start("Auditor", "Reviewing previous day's results")
        
        # Review previous day's results
        previous_date = target_date - timedelta(days=1)
        auditor_output = self.auditor.process(previous_date)
        
        # Save Auditor report
        if isinstance(auditor_output, dict):
            self.report_generator.save_agent_report(
                "auditor",
                auditor_output,
                previous_date,
                metadata={"review_date": previous_date.isoformat()}
            )
        
        # Get daily_report for backward compatibility
        daily_report = auditor_output
        
        # Save report to file
        if daily_report.total_picks > 0:
            report_text = self.report_generator.generate_daily_report(previous_date)
            report_path = self.report_generator.save_report_to_file(
                report_text, 
                f"daily_report_{previous_date.isoformat()}.txt",
                output_dir="data/reports"
            )
            logger.info(f"📄 Daily report saved to {report_path}")
        else:
            logger.info(f"No picks to review for {previous_date}")
    
    def _reset_all_agent_token_usage(self) -> None:
        """Reset token usage tracking for all agents"""
        agents = [
            self.results_processor,
            self.researcher,
            self.modeler,
            self.picker,
            self.president,
            self.auditor
        ]
        for agent in agents:
            if hasattr(agent, 'llm_client') and agent.llm_client:
                agent.llm_client.reset_usage_stats()
    
    def _log_token_usage_summary(self) -> None:
        """Log token usage summary for all agents"""
        agents = [
            ("ResultsProcessor", self.results_processor),
            ("Researcher", self.researcher),
            ("Modeler", self.modeler),
            ("Picker", self.picker),
            ("President", self.president),
            ("Auditor", self.auditor)
        ]
        
        total_tokens = 0
        total_prompt = 0
        total_completion = 0
        agent_usage = []
        
        for agent_name, agent in agents:
            if hasattr(agent, 'llm_client') and agent.llm_client:
                stats = agent.llm_client.get_usage_stats()
                tokens = stats["total_tokens"]
                prompt = stats["prompt_tokens"]
                completion = stats["completion_tokens"]
                
                if tokens > 0:
                    total_tokens += tokens
                    total_prompt += prompt
                    total_completion += completion
                    agent_usage.append({
                        "name": agent_name,
                        "model": agent.llm_client.model,
                        "tokens": tokens,
                        "prompt": prompt,
                        "completion": completion
                    })
        
        if agent_usage:
            logger.info("=" * 80)
            logger.info("📊 TOKEN USAGE SUMMARY")
            logger.info("=" * 80)
            
            for usage in agent_usage:
                logger.info(
                    f"  {usage['name']:12} ({usage['model']:15}): "
                    f"{usage['tokens']:>8,} tokens "
                    f"({usage['prompt']:>6,} prompt + {usage['completion']:>6,} completion)"
                )
            
            logger.info("-" * 80)
            logger.info(
                f"  {'TOTAL':12} {'':15} "
                f"{total_tokens:>8,} tokens "
                f"({total_prompt:>6,} prompt + {total_completion:>6,} completion)"
            )
            logger.info("=" * 80)
        else:
            logger.info("📊 No token usage recorded (all agents may have used cache)")
    
    def _deduplicate_games_by_matchup(self, games: List[Game]) -> List[Game]:
        """
        Deduplicate games by normalized matchup to ensure only one game per unique matchup.
        
        This prevents duplicate games from being passed downstream to modeler/picker/president
        when the same game exists with different team_ids (e.g., due to duplicate team entries).
        
        Args:
            games: List of games to deduplicate
            
        Returns:
            Deduplicated list of games (one per unique matchup)
        """
        from src.utils.team_normalizer import normalize_team_name, remove_mascot_from_team_name
        from collections import defaultdict
        
        if not games:
            return games
        
        # Group games by normalized matchup (order-independent)
        matchup_map = defaultdict(list)  # matchup_key -> list of games
        
        for game in games:
            # Normalize team names for matching
            team1_name = game.team1 if game.team1 else ""
            team2_name = game.team2 if game.team2 else ""
            
            # Remove mascots and normalize
            norm_team1 = normalize_team_name(team1_name, for_matching=True)
            norm_team2 = normalize_team_name(team2_name, for_matching=True)
            cleaned_team1 = remove_mascot_from_team_name(norm_team1)
            cleaned_team2 = remove_mascot_from_team_name(norm_team2)
            
            # Create order-independent matchup key
            matchup_key = tuple(sorted([cleaned_team1, cleaned_team2]))
            matchup_map[matchup_key].append(game)
        
        # For each matchup, keep only one game (prefer lowest game_id, or first if no IDs)
        deduplicated = []
        duplicates_found = 0
        
        for matchup_key, game_list in matchup_map.items():
            if len(game_list) > 1:
                duplicates_found += len(game_list) - 1
                # Sort by game_id (None last), then keep the first one
                game_list.sort(key=lambda g: (g.id is None, g.id if g.id is not None else float('inf')))
                kept_game = game_list[0]
                duplicate_ids = [g.id for g in game_list[1:] if g.id]
                
                logger.warning(
                    f"Found {len(game_list)} duplicate games for matchup {matchup_key}: "
                    f"keeping game_id={kept_game.id} ({kept_game.team1} vs {kept_game.team2}), "
                    f"removing duplicates: {duplicate_ids}"
                )
                deduplicated.append(kept_game)
            else:
                deduplicated.append(game_list[0])
        
        if duplicates_found > 0:
            logger.warning(
                f"⚠️  Deduplicated {duplicates_found} duplicate game(s). "
                f"Returning {len(deduplicated)} unique games (from {len(games)} total)."
            )
        else:
            logger.debug(f"✅ No duplicate games found. All {len(games)} games are unique.")
        
        return deduplicated
    
    def close(self):
        """Close database connection"""
        if self.db:
            self.db.close()

