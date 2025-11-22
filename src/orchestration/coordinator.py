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
from src.data.analytics import AnalyticsService
from src.agents.researcher import Researcher
from src.agents.modeler import Modeler
from src.agents.picker import Picker
from src.agents.president import President
from src.agents.auditor import Auditor
from src.agents.results_processor import ResultsProcessor
from src.orchestration.data_converter import DataConverter
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
        self.analytics_service = AnalyticsService(self.db)
        self.google_sheets_service = GoogleSheetsService(self.db)
    
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
            historical_performance = self._get_historical_performance(target_date)
            
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
            games_with_results = session.query(GameModel).filter(
                func.date(GameModel.date) == yesterday,
                GameModel.result.isnot(None)
            ).all()
            
            for game in games_with_results:
                if game.id:
                    try:
                        self.analytics_service.save_result_analytics(
                            game_id=game.id,
                            game_date=yesterday
                        )
                    except Exception as e:
                        logger.debug(f"Could not save result analytics for game_id={game.id}: {e}")
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
        games = self._save_games(games)
        
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
        
        # Save initial game analytics (with team1/team2 as fallback)
        for game in games:
            if game.id:
                # Use team1 as home, team2 as away initially (will be updated after researcher)
                self.analytics_service.save_game_analytics(
                    game_id=game.id,
                    game_date=game.date,
                    home_team=game.team1,
                    away_team=game.team2,
                    home_conference=None,
                    away_conference=None
                )
        
        return games
    
    def _step_scrape_lines(self, games: List[Game]) -> List[BettingLine]:
        """Step 2: Scrape betting lines"""
        self.researcher.interaction_logger.log_agent_start("LinesScraper", f"Scraping lines for {len(games)} games")
        lines = self.lines_scraper.scrape_lines(games)
        lines = self._save_lines(lines, games)
        self.researcher.interaction_logger.log_agent_complete("LinesScraper", f"Found {len(lines)} betting lines")
        
        # Save odds analytics (will be updated after home/away is determined)
        # Note: This will use team1/team2 initially, but odds aggregation happens after researcher
        for game in games:
            if game.id:
                try:
                    self.analytics_service.save_odds_analytics(
                        game_id=game.id,
                        game_date=game.date
                    )
                except Exception as e:
                    logger.debug(f"Could not save odds analytics for game_id={game.id}: {e}")
        
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
        
        # Update odds analytics now that we have home/away information
        for game in games:
            if game.id:
                try:
                    self.analytics_service.save_odds_analytics(
                        game_id=game.id,
                        game_date=target_date
                    )
                except Exception as e:
                    logger.debug(f"Could not update odds analytics for game_id={game.id}: {e}")
        
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
            
            # Fallback to team1/team2 if home/away not found
            if not home_team or not away_team:
                game = game_map.get(game_id)
                if game:
                    home_team = home_team or game.team1
                    away_team = away_team or game.team2
            
            # Save to analytics
            if home_team and away_team:
                self.analytics_service.save_game_analytics(
                    game_id=game_id,
                    game_date=target_date,
                    home_team=home_team,
                    away_team=away_team,
                    home_conference=home_conference,
                    away_conference=away_conference
                )
    
    def _step_model(self, insights: Dict[str, Any], lines: List[BettingLine], target_date: date, force_refresh: bool = False) -> Dict[str, Any]:
        """Step 4: Modeler generates predictions"""
        insights_games = insights.get("games", [])
        self.modeler.interaction_logger.log_agent_start("Modeler", f"Modeling {len(insights_games)} games")
        self.modeler.interaction_logger.log_handoff("Researcher", "Modeler", "GameInsights", len(insights_games))
        predictions = self.modeler.process(insights, betting_lines=lines, target_date=target_date, force_refresh=force_refresh)
        
        # Save prediction analytics
        session = self.db.get_session()
        try:
            # Get all games that were modeled
            game_models = predictions.get("game_models", [])
            for game_model in game_models:
                game_id_str = game_model.get("game_id")
                if not game_id_str:
                    continue
                
                try:
                    game_id = int(game_id_str)
                except (ValueError, TypeError):
                    continue
                
                try:
                    self.analytics_service.save_prediction_analytics(
                        game_id=game_id,
                        game_date=target_date
                    )
                except Exception as e:
                    logger.debug(f"Could not save prediction analytics for game_id={game_id}: {e}")
        finally:
            session.close()
        
        # CRITICAL VALIDATION: Ensure all games from Researcher are modeled
        game_models = predictions.get("game_models", [])
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
        approved_by_game_id = {p.get("game_id"): p for p in approved_picks_data}
        
        # Update picks with units and best_bet from President
        for pick in picks:
            president_pick = approved_by_game_id.get(str(pick.game_id))
            if president_pick:
                pick.stake_units = president_pick.get("units", 1.0)
                pick.best_bet = president_pick.get("best_bet", False)
                # Update rationale with President's reasoning if available
                if president_pick.get("final_decision_reasoning"):
                    pick.rationale = f"{pick.rationale}\n\nPresident's Analysis: {president_pick.get('final_decision_reasoning')}"
        
        # Save all picks to database
        for pick in picks:
            self._save_pick(pick)
            if pick.id:
                self._update_pick_stakes(pick)
        
        if review.approved:
            # All picks are approved, but only best bets are placed
            best_bet_picks = [p for p in picks if p.best_bet]
            
            logger.info(f"💰 Placing {len(best_bet_picks)} best bet bets")
            self._place_bets(best_bet_picks)
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
    
    def _get_historical_performance(self, target_date: date, days_back: int = 7) -> Optional[Dict[str, Any]]:
        """Get historical performance data from recent days for learning
        
        Args:
            target_date: Current date
            days_back: Number of days to look back (default: 7)
            
        Returns:
            Dictionary with historical performance summary or None if no data
        """
        if not self.db:
            return None
        
        session = self.db.get_session()
        try:
            from src.data.storage import DailyReportModel, PickModel, BetModel
            from sqlalchemy import func
            from src.data.models import BetResult
            
            # Get daily reports from recent days
            start_date = target_date - timedelta(days=days_back)
            daily_reports = session.query(DailyReportModel).filter(
                DailyReportModel.date >= start_date,
                DailyReportModel.date < target_date
            ).order_by(DailyReportModel.date.desc()).all()
            
            if not daily_reports:
                return None
            
            # Aggregate performance metrics
            total_picks = sum(r.total_picks for r in daily_reports)
            total_wins = sum(r.wins for r in daily_reports)
            total_losses = sum(r.losses for r in daily_reports)
            total_pushes = sum(r.pushes for r in daily_reports)
            total_wagered = sum(r.total_wagered for r in daily_reports)
            total_profit = sum(r.profit_loss for r in daily_reports)
            
            # Calculate win rate and ROI
            win_rate = (total_wins / total_picks * 100) if total_picks > 0 else 0.0
            roi = (total_profit / total_wagered * 100) if total_wagered > 0 else 0.0
            
            # Get bet type performance
            bet_type_performance = {}
            for report in daily_reports:
                if report.accuracy_metrics:
                    metrics = report.accuracy_metrics
                    if isinstance(metrics, dict) and 'bet_type_performance' in metrics:
                        for bet_type, perf in metrics['bet_type_performance'].items():
                            if bet_type not in bet_type_performance:
                                bet_type_performance[bet_type] = {'wins': 0, 'losses': 0, 'wagered': 0.0, 'profit': 0.0}
                            bet_type_performance[bet_type]['wins'] += perf.get('wins', 0)
                            bet_type_performance[bet_type]['losses'] += perf.get('losses', 0)
                            bet_type_performance[bet_type]['wagered'] += perf.get('wagered', 0.0)
                            bet_type_performance[bet_type]['profit'] += perf.get('payout', 0.0) - perf.get('wagered', 0.0)
            
            # Get recent recommendations from daily reports
            recent_recommendations = []
            for report in daily_reports[:3]:  # Last 3 days
                if report.recommendations:
                    if isinstance(report.recommendations, list):
                        recent_recommendations.extend(report.recommendations)
                    elif isinstance(report.recommendations, str):
                        recent_recommendations.append(report.recommendations)
            
            # Get insights from recent reports
            recent_insights = []
            for report in daily_reports[:3]:
                if report.insights:
                    if isinstance(report.insights, dict):
                        recent_insights.append(report.insights)
                    elif isinstance(report.insights, str):
                        recent_insights.append({"note": report.insights})
            
            return {
                "period": f"{start_date} to {target_date - timedelta(days=1)}",
                "days_reviewed": len(daily_reports),
                "total_picks": total_picks,
                "wins": total_wins,
                "losses": total_losses,
                "pushes": total_pushes,
                "win_rate": round(win_rate, 2),
                "total_wagered": round(total_wagered, 2),
                "total_profit": round(total_profit, 2),
                "roi": round(roi, 2),
                "bet_type_performance": bet_type_performance,
                "recent_recommendations": recent_recommendations[:10],  # Limit to 10 most recent
                "recent_insights": recent_insights,
                "daily_summaries": [
                    {
                        "date": r.date.isoformat(),
                        "picks": r.total_picks,
                        "wins": r.wins,
                        "losses": r.losses,
                        "win_rate": round(r.win_rate * 100, 2) if r.win_rate else 0.0,
                        "profit": round(r.profit_loss, 2),
                        "roi": round(r.roi, 2) if r.roi else 0.0
                    }
                    for r in daily_reports[:7]  # Last 7 days
                ]
            }
        except Exception as e:
            logger.error(f"Error fetching historical performance: {e}", exc_info=True)
            return None
        finally:
            session.close()
    
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
    
    def _save_games(self, games: List[Game]) -> List[Game]:
        """Save games to database and return with IDs"""
        if not self.db:
            return games
        
        session = self.db.get_session()
        try:
            saved_games = []
            for game in games:
                # Check if game exists
                existing = session.query(GameModel).filter_by(
                    team1=game.team1,
                    team2=game.team2,
                    date=game.date
                ).first()
                
                if existing:
                    saved_games.append(Game(
                        id=existing.id,
                        team1=existing.team1,
                        team2=existing.team2,
                        date=existing.date,
                        venue=existing.venue,
                        status=existing.status,
                        result=existing.result
                    ))
                else:
                    game_model = GameModel(
                        team1=game.team1,
                        team2=game.team2,
                        date=game.date,
                        venue=game.venue,
                        status=game.status,
                        result=game.result
                    )
                    session.add(game_model)
                    session.flush()
                    saved_games.append(Game(
                        id=game_model.id,
                        team1=game_model.team1,
                        team2=game_model.team2,
                        date=game_model.date,
                        venue=game_model.venue,
                        status=game_model.status,
                        result=game_model.result
                    ))
            
            session.commit()
            return saved_games
            
        except Exception as e:
            logger.error(f"Error saving games: {e}")
            session.rollback()
            return games
        finally:
            session.close()
    
    def _save_lines(self, lines: List[BettingLine], games: List[Game]) -> List[BettingLine]:
        """Save betting lines to database"""
        if not self.db:
            return lines
        
        session = self.db.get_session()
        try:
            for line in lines:
                line_model = BettingLineModel(
                    game_id=line.game_id,
                    book=line.book,
                    bet_type=line.bet_type,
                    line=line.line,
                    odds=line.odds,
                    team=line.team,
                    timestamp=line.timestamp
                )
                session.add(line_model)
            
            session.commit()
            return lines
            
        except Exception as e:
            logger.error(f"Error saving lines: {e}")
            session.rollback()
            return lines
        finally:
            session.close()
    
    def _update_pick_stakes(self, pick: Pick) -> None:
        """Update pick stakes in database"""
        if not self.db or not pick.id:
            return
        
        session = self.db.get_session()
        try:
            pick_model = session.query(PickModel).filter_by(id=pick.id).first()
            if pick_model:
                pick_model.stake_units = pick.stake_units
                pick_model.stake_amount = pick.stake_amount
                session.commit()
        except Exception as e:
            logger.error(f"Error updating pick stakes: {e}")
            session.rollback()
        finally:
            session.close()
    
    def _save_pick(self, pick: Pick) -> None:
        """Save pick to database (used for new picks)"""
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            pick_model = PickModel(
                game_id=pick.game_id,
                bet_type=pick.bet_type,
                line=pick.line,
                odds=pick.odds,
                stake_units=pick.stake_units,
                stake_amount=pick.stake_amount,
                rationale=pick.rationale,
                confidence=pick.confidence,
                expected_value=pick.expected_value,
                book=pick.book,
                parlay_legs=pick.parlay_legs
            )
            session.add(pick_model)
            session.commit()
            pick.id = pick_model.id
        except Exception as e:
            logger.error(f"Error saving pick: {e}")
            session.rollback()
        finally:
            session.close()
    
    def _place_bets(self, picks: List[Pick]) -> List[Bet]:
        """Place bets (simulation mode)"""
        if not self.db:
            return []
        
        session = self.db.get_session()
        bets = []
        
        try:
            for pick in picks:
                if not pick.id:
                    continue
                
                # Create bet record
                bet_model = BetModel(
                    pick_id=pick.id,
                    placed_at=datetime.now(),
                    result=BetResult.PENDING
                )
                session.add(bet_model)
                session.flush()
                
                bet = Bet(
                    id=bet_model.id,
                    pick_id=bet_model.pick_id,
                    placed_at=bet_model.placed_at,
                    result=bet_model.result
                )
                bets.append(bet)
            
            session.commit()
            logger.info(f"Placed {len(bets)} bets")
            
        except Exception as e:
            logger.error(f"Error placing bets: {e}")
            session.rollback()
        finally:
            session.close()
        
        return bets
    
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
    
    def close(self):
        """Close database connection"""
        if self.db:
            self.db.close()

