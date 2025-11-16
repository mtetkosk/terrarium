"""Coordinator for agent workflow"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta
import re

from src.data.models import (
    Game, BettingLine, GameInsight, Prediction, Pick, Bet, Bankroll,
    ComplianceResult, CardReview, BetType, RevisionRequest, RevisionRequestType,
    BetResult
)
from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.storage import Database, GameModel, BettingLineModel, BetModel, PickModel
from src.agents.researcher import Researcher
from src.agents.modeler import Modeler
from src.agents.picker import Picker
from src.agents.banker import Banker
from src.agents.compliance import Compliance
from src.agents.president import President
from src.agents.auditor import Auditor
from src.utils.logging import get_logger

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
        self.researcher = Researcher(self.db)
        self.modeler = Modeler(self.db)
        self.picker = Picker(self.db)
        self.banker = Banker(self.db)
        self.compliance = Compliance(self.db)
        self.president = President(self.db)
        self.auditor = Auditor(self.db)
    
    def run_daily_workflow(self, target_date: Optional[date] = None, max_revisions: int = 2, test_mode: bool = False, force_refresh: bool = False) -> CardReview:
        """Run the daily betting workflow with revision support"""
        if target_date is None:
            target_date = date.today()
        
        logger.info("=" * 80)
        logger.info(f"🚀 STARTING DAILY WORKFLOW FOR {target_date}")
        if test_mode:
            logger.info("🧪 TEST MODE: Processing only first 5 games")
        logger.info("=" * 80)
        
        # Reset token usage tracking at start of workflow
        self._reset_all_agent_token_usage()
        
        try:
            # Step 1: Scrape games
            self.researcher.interaction_logger.log_agent_start("GamesScraper", f"Scraping games for {target_date}")
            games = self.games_scraper.scrape_games(target_date)
            games = self._save_games(games)
            
            # Limit to first 5 games in test mode
            if test_mode and len(games) > 5:
                original_count = len(games)
                games = games[:5]
                logger.info(f"🧪 TEST MODE: Limited games from {original_count} to {len(games)}")
                self.researcher.interaction_logger.log_agent_complete("GamesScraper", f"Found {original_count} games (limited to {len(games)} for testing)")
            else:
                self.researcher.interaction_logger.log_agent_complete("GamesScraper", f"Found {len(games)} games")
            
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
            self.researcher.interaction_logger.log_agent_start("LinesScraper", f"Scraping lines for {len(games)} games")
            lines = self.lines_scraper.scrape_lines(games)
            lines = self._save_lines(lines, games)
            self.researcher.interaction_logger.log_agent_complete("LinesScraper", f"Found {len(lines)} betting lines")
            
            # Main workflow loop with revision support
            revision_count = 0
            insights = None
            predictions = None
            picks = None
            
            while revision_count <= max_revisions:
                if revision_count > 0:
                    logger.info(f"🔄 REVISION CYCLE {revision_count}/{max_revisions}")
                
                # Step 3: Researcher researches games
                self.researcher.interaction_logger.log_agent_start("Researcher", f"Researching {len(games)} games")
                self.researcher.interaction_logger.log_handoff("GamesScraper", "Researcher", "Games", len(games))
                # Pass betting lines to researcher to avoid duplicate scraping
                insights = self.researcher.process(games, target_date=target_date, betting_lines=lines, force_refresh=force_refresh)
                self.researcher.interaction_logger.log_agent_complete("Researcher", f"Generated {len(insights)} insights")
                
                # Save Researcher report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "researcher",
                    insights if isinstance(insights, dict) else {"games": insights},
                    target_date,
                    metadata={"games_researched": len(games), "insights_generated": len(insights) if isinstance(insights, list) else len(insights.get("games", []))}
                )
                
                # Step 4: Modeler generates predictions
                self.modeler.interaction_logger.log_agent_start("Modeler", f"Modeling {len(insights)} games")
                self.modeler.interaction_logger.log_handoff("Researcher", "Modeler", "GameInsights", len(insights))
                predictions = self.modeler.process(insights, betting_lines=lines)
                self.modeler.interaction_logger.log_agent_complete("Modeler", f"Generated {len(predictions)} predictions")
                
                # Save Modeler report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "modeler",
                    predictions,
                    target_date,
                    metadata={"games_modeled": len(insights) if isinstance(insights, list) else len(insights.get("games", [])), "predictions_generated": len(predictions.get("game_models", []))}
                )
                
                # Step 5: Picker selects picks
                self.picker.interaction_logger.log_agent_start("Picker", f"Selecting from {len(predictions.get('game_models', []))} predictions")
                self.picker.interaction_logger.log_handoff("Modeler", "Picker", "Predictions", len(predictions.get('game_models', [])))
                picker_response = self.picker.process(predictions, insights, lines)
                candidate_picks = picker_response.get("candidate_picks", [])
                self.picker.interaction_logger.log_agent_complete("Picker", f"Selected {len(candidate_picks)} picks")
                
                # Save Picker report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "picker",
                    picker_response,
                    target_date,
                    metadata={"candidate_picks": len(candidate_picks)}
                )
                
                if not candidate_picks:
                    logger.warning("No picks selected. Ending workflow.")
                    return CardReview(
                        date=target_date,
                        approved=False,
                        picks_approved=[],
                        picks_rejected=[],
                        review_notes="No picks met selection criteria."
                    )
                
                # Convert candidate picks to Pick objects
                picks = self._convert_picks_from_json(candidate_picks, games)
                
                # Step 6: Banker allocates stakes (only to favorites)
                favorite_picks_data = [p for p in candidate_picks if p.get("favorite", False)]
                self.banker.interaction_logger.log_agent_start("Banker", f"Allocating stakes for {len(favorite_picks_data)} favorite picks (out of {len(candidate_picks)} total)")
                self.banker.interaction_logger.log_handoff("Picker", "Banker", "Picks", len(favorite_picks_data))
                banker_response = self.banker.process(favorite_picks_data)  # Only process favorites
                sized_picks_data = banker_response.get("sized_picks", [])
                self.banker.interaction_logger.log_agent_complete("Banker", f"Allocated stakes to {len(sized_picks_data)} favorite picks")
                
                # Save Banker report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "banker",
                    banker_response,
                    target_date,
                    metadata={"sized_picks": len(sized_picks_data)}
                )
                
                # Convert sized picks to Pick objects and update stakes (only for favorites)
                # Create a map of sized picks by game_id, bet_type, and line for matching
                sized_picks_map = {}
                for sized_pick_data in sized_picks_data:
                    try:
                        game_id_str = str(sized_pick_data.get("game_id", ""))
                        bet_type_str = sized_pick_data.get("bet_type", "").lower()
                        selection = sized_pick_data.get("selection", "")
                        
                        # Extract line from selection
                        line = 0.0
                        if selection:
                            match = re.search(r'([+-]?\d+\.?\d*)', str(selection))
                            if match:
                                line = float(match.group(1))
                        else:
                            line = sized_pick_data.get("line", 0.0)
                        
                        game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                        key = (game_id, bet_type_str, line)
                        sized_picks_map[key] = sized_pick_data
                    except Exception:
                        pass
                
                # Update all picks with stakes (favorites get stakes, others get 0)
                picks_with_stakes = []
                for pick in picks:
                    key = (pick.game_id, pick.bet_type.value, pick.line)
                    sized_pick_data = sized_picks_map.get(key)
                    
                    if sized_pick_data and pick.favorite:
                        # Update stake info for favorites
                        pick.stake_units = float(sized_pick_data.get("units", 0.0))
                        # Calculate stake_amount based on stake_units and initial bankroll
                        initial_bankroll = self.banker.initial
                        pick.stake_amount = pick.stake_units * (initial_bankroll * 0.01)  # 1 unit = 1% of initial bankroll
                    else:
                        # Non-favorites get 0 stakes
                        pick.stake_units = 0.0
                        pick.stake_amount = 0.0
                    
                    picks_with_stakes.append(pick)
                
                # Save all picks to database (both favorites and non-favorites)
                for pick in picks_with_stakes:
                    self._save_pick(pick)
                
                # Update saved picks with stakes
                for pick in picks_with_stakes:
                    if pick.id:
                        self._update_pick_stakes(pick)
                
                # Step 7: Compliance validates (only favorites need validation for betting)
                favorite_picks_with_stakes = [p for p in picks_with_stakes if p.favorite]
                self.compliance.interaction_logger.log_agent_start("Compliance", f"Validating {len(favorite_picks_with_stakes)} favorite picks (out of {len(picks_with_stakes)} total)")
                self.compliance.interaction_logger.log_handoff("Banker", "Compliance", "Picks", len(favorite_picks_with_stakes))
                bankroll = self.banker.get_current_bankroll()
                
                # Convert favorite picks to dict format for Compliance agent
                sized_picks_dict = [
                    {
                        "game_id": str(pick.game_id),
                        "bet_type": pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type),
                        "selection": f"{pick.line:+.1f}" if pick.line else "",
                        "odds": str(pick.odds),
                        "units": pick.stake_units,
                        "edge_estimate": pick.expected_value,
                        "confidence": pick.confidence,
                        "confidence_score": pick.confidence_score,
                        "favorite": pick.favorite,
                        "book": pick.book
                    }
                    for pick in favorite_picks_with_stakes
                ]
                
                compliance_response = self.compliance.process(
                    sized_picks_dict, 
                    picker_rationales=insights,
                    bankroll_status={"current_bankroll": bankroll.balance}
                )
                
                # Convert compliance JSON response to ComplianceResult objects
                # Only validate favorites, so map compliance results to favorite picks
                compliance_results = self._convert_compliance_results_from_json(
                    compliance_response.get("bet_reviews", []),
                    favorite_picks_with_stakes
                )
                
                approved_count = sum(1 for r in compliance_results if r.approved)
                self.compliance.interaction_logger.log_agent_complete(
                    "Compliance",
                    f"Approved {approved_count}/{len(compliance_results)} picks"
                )
                
                # Save Compliance report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "compliance",
                    compliance_response,
                    target_date,
                    metadata={"picks_reviewed": len(compliance_results), "approved": approved_count}
                )
                
                # Step 8: President reviews and approves
                self.president.interaction_logger.log_handoff("Compliance", "President", "ComplianceResults", len(compliance_results))
                
                # Convert picks to dict format for President
                # Include all picks but mark which are favorites
                all_picks_dict = [
                    {
                        "game_id": str(pick.game_id),
                        "bet_type": pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type),
                        "selection": f"{pick.line:+.1f}" if pick.line else "",
                        "odds": str(pick.odds),
                        "units": pick.stake_units,
                        "edge_estimate": pick.expected_value,
                        "confidence": pick.confidence,
                        "confidence_score": pick.confidence_score,
                        "favorite": pick.favorite,
                        "book": pick.book,
                        "rationale": pick.rationale
                    }
                    for pick in picks_with_stakes
                ]
                
                # For President review, focus on favorites but provide context of all picks
                sized_picks_dict = [p for p in all_picks_dict if p.get("favorite", False)]
                
                compliance_results_dict = [
                    {
                        "game_id": str(picks_with_stakes[i].game_id) if i < len(picks_with_stakes) else "",
                        "compliance_status": "approved" if r.approved else "rejected",
                        "issues": r.reasons,
                        "recommendations": []
                    }
                    for i, r in enumerate(compliance_results)
                ]
                
                president_response = self.president.process(
                    sized_picks_dict,
                    compliance_results_dict,
                    researcher_output=insights,
                    modeler_output=predictions,
                    banker_output={"current_bankroll": bankroll.balance}
                )
                
                # Convert President's JSON response to CardReview object
                review = self._convert_card_review_from_json(
                    president_response,
                    picks_with_stakes,
                    target_date
                )
                
                # Save President report
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_gen.save_agent_report(
                    "president",
                    president_response,
                    target_date,
                    metadata={
                        "approved": len(review.picks_approved),
                        "rejected": len(review.picks_rejected),
                        "revision_requests": len(review.revision_requests) if review.revision_requests else 0
                    }
                )
                
                # Check if revisions are needed
                if review.revision_requests and revision_count < max_revisions:
                    revision_count += 1
                    logger.warning(f"⚠️  REVISION REQUIRED: {len(review.revision_requests)} requests")
                    for req in review.revision_requests:
                        logger.warning(f"  - {req.target_agent}: {req.feedback}")
                    # Continue loop to process revisions
                    continue
                else:
                    # No revisions needed or max revisions reached
                    break
            
            # Step 9: Save betting card and place bets (if approved)
            if review.approved:
                # Only approve favorites that passed compliance
                approved_favorites = [
                    p for p in picks_with_stakes
                    if p.favorite and (p.id or 0) in review.picks_approved
                ]
                
                # All picks (favorites + others) for the betting card
                all_picks_for_card = picks_with_stakes
                approved_picks = approved_favorites  # Only favorites are actually bet
                
                # Save betting card for manual review
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                
                # Generate and save betting card (includes all picks, favorites at top)
                card_text = report_gen.generate_betting_card(all_picks_for_card, target_date)
                card_path = report_gen.save_report_to_file(
                    card_text, 
                    f"betting_card_{target_date.isoformat()}.txt",
                    output_dir="data/reports"
                )
                logger.info(f"📋 Betting card saved to {card_path} ({len(approved_picks)} favorites to bet, {len(all_picks_for_card) - len(approved_picks)} other picks for reference)")
                
                # Generate and save President's comprehensive report with rationale
                # Store president_response for the report generator
                presidents_report = report_gen.generate_presidents_report(
                    approved_picks,
                    review,
                    target_date,
                    president_response=president_response,
                    researcher_output=insights,
                    modeler_output=predictions
                )
                # Save President's report in president subdirectory
                report_path = report_gen.save_report_to_file(
                    presidents_report,
                    f"presidents_report_{target_date.isoformat()}.txt",
                    output_dir="data/reports/president"
                )
                logger.info(f"📝 President's report saved to {report_path}")
                
                logger.info(f"💰 Placing {len(approved_picks)} bets")
                self._place_bets(approved_picks)
                
                # Update bankroll
                self.banker.update_bankroll(approved_picks)
            else:
                logger.warning("❌ Card not approved - no bets placed")
            
            # Step 10: Generate daily report (review previous day's results)
            logger.info("📊 Generating daily performance report")
            self.auditor.interaction_logger.log_agent_start("Auditor", "Reviewing previous day's results")
            
            # Review previous day's results (yesterday's bets that settled today)
            previous_date = target_date - timedelta(days=1)
            auditor_output = self.auditor.process(previous_date)
            
            # Save Auditor report
            from src.utils.reporting import ReportGenerator
            report_gen = ReportGenerator(self.db)
            if isinstance(auditor_output, dict):
                report_gen.save_agent_report(
                    "auditor",
                    auditor_output,
                    previous_date,  # Report for the day being reviewed
                    metadata={"review_date": previous_date.isoformat()}
                )
            
            # Get daily_report for backward compatibility
            daily_report = auditor_output
            
            # Save report to file (use previous_date for filename since that's what we're reviewing)
            if daily_report.total_picks > 0:
                from src.utils.reporting import ReportGenerator
                report_gen = ReportGenerator(self.db)
                report_text = report_gen.generate_daily_report(previous_date)
                report_path = report_gen.save_report_to_file(
                    report_text, 
                    f"daily_report_{previous_date.isoformat()}.txt",
                    output_dir="data/reports"
                )
                logger.info(f"📄 Daily report saved to {report_path}")
            else:
                logger.info(f"No picks to review for {previous_date}")
            
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
    
    def _convert_picks_from_json(self, candidate_picks: List[Dict[str, Any]], games: List[Game]) -> List[Pick]:
        """Convert JSON candidate picks from Picker to Pick objects"""
        picks = []
        game_map = {g.id: g for g in games if g.id}
        
        for pick_data in candidate_picks:
            try:
                # Extract game_id - could be string or int
                game_id_str = str(pick_data.get("game_id", ""))
                game_id = None
                
                # Try to find matching game
                if game_id_str and game_id_str != "parlay":
                    # Try direct ID match
                    try:
                        game_id = int(game_id_str)
                    except ValueError:
                        # Try to match by team names in game_id string
                        for g in games:
                            if game_id_str in f"{g.team1}_{g.team2}" or game_id_str in f"{g.team2}_{g.team1}":
                                game_id = g.id
                                break
                
                # Parse bet type
                bet_type_str = pick_data.get("bet_type", "").lower()
                try:
                    bet_type = BetType(bet_type_str)
                except ValueError:
                    logger.warning(f"Invalid bet type: {bet_type_str}, defaulting to SPREAD")
                    bet_type = BetType.SPREAD
                
                # Parse odds (could be string like "-110" or int)
                odds_str = str(pick_data.get("odds", "-110"))
                odds = int(odds_str.replace("+", "").replace("-", ""))
                if "-" in odds_str or odds_str.startswith("-"):
                    odds = -odds
                
                # Parse line from selection or line field
                line = pick_data.get("line", 0.0)
                selection_text = pick_data.get("selection", "")
                if not line and selection_text:
                    # Try to extract line from selection string (e.g., "Team A +3.5")
                    match = re.search(r'([+-]?\d+\.?\d*)', str(selection_text))
                    if match:
                        line = float(match.group(1))
                
                # Combine justification into rationale
                justification = pick_data.get("justification", [])
                if isinstance(justification, list):
                    rationale = " | ".join(justification)
                else:
                    rationale = str(justification) or pick_data.get("notes", "")
                
                # Parse favorite flag and confidence score
                favorite = pick_data.get("favorite", False)
                confidence_score = pick_data.get("confidence_score", 5)
                # Ensure confidence_score is between 1-10
                confidence_score = max(1, min(10, int(confidence_score)))
                
                pick = Pick(
                    game_id=game_id or 0,
                    bet_type=bet_type,
                    line=float(line),
                    odds=odds,
                    rationale=rationale,
                    confidence=float(pick_data.get("confidence", 0.5)),
                    expected_value=float(pick_data.get("edge_estimate", 0.0)),
                    book=pick_data.get("book", "draftkings"),
                    selection_text=selection_text,  # Store original selection text
                    favorite=favorite,
                    confidence_score=confidence_score,
                    parlay_legs=None  # Will be set later if parlay
                )
                picks.append(pick)
            except Exception as e:
                logger.error(f"Error converting pick from JSON: {e}, pick_data: {pick_data}", exc_info=True)
        
        return picks
    
    def _convert_sized_picks_from_json(self, sized_picks_data: List[Dict[str, Any]], original_picks: List[Pick]) -> List[Pick]:
        """Convert sized picks from Banker JSON response and merge with original picks"""
        # Create multiple maps for flexible matching
        # Map 1: By game_id, bet_type, and line
        pick_map_exact = {}
        # Map 2: By game_id and bet_type only (for when line doesn't match exactly)
        pick_map_by_game_bet = {}
        for pick in original_picks:
            bet_type_val = pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)
            key_exact = (pick.game_id, bet_type_val, pick.line)
            key_game_bet = (pick.game_id, bet_type_val)
            pick_map_exact[key_exact] = pick
            if key_game_bet not in pick_map_by_game_bet:
                pick_map_by_game_bet[key_game_bet] = []
            pick_map_by_game_bet[key_game_bet].append(pick)
        
        picks_with_stakes = []
        
        for sized_data in sized_picks_data:
            try:
                # Try to match with original pick
                game_id_str = str(sized_data.get("game_id", ""))
                bet_type_str = sized_data.get("bet_type", "").lower()
                
                # Try to get line from multiple sources
                line = sized_data.get("line", 0.0)
                if not line and "selection" in sized_data:
                    selection = sized_data["selection"]
                    # Extract line from selection string (e.g., "Team A +3.5" or "Over 160.5")
                    match = re.search(r'([+-]?\d+\.?\d*)', str(selection))
                    if match:
                        line = float(match.group(1))
                
                # Find matching original pick
                matched_pick = None
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() and game_id_str != "parlay" else 0
                    bet_type = BetType(bet_type_str)
                    bet_type_val = bet_type.value
                    
                    # Try exact match first
                    key_exact = (game_id, bet_type_val, float(line))
                    matched_pick = pick_map_exact.get(key_exact)
                    
                    # If no exact match, try matching by game_id and bet_type only (take first match)
                    if not matched_pick:
                        key_game_bet = (game_id, bet_type_val)
                        candidates = pick_map_by_game_bet.get(key_game_bet, [])
                        if candidates:
                            matched_pick = candidates[0]  # Take first match
                            logger.debug(f"Matched pick by game_id+bet_type only (line may differ): {game_id}, {bet_type_val}")
                except (ValueError, KeyError) as e:
                    logger.debug(f"Error matching pick: {e}")
                    pass
                
                if matched_pick:
                    # Update stakes from Banker response
                    matched_pick.stake_units = float(sized_data.get("units", 0.0))
                    # Calculate stake amount from units and bankroll
                    # 1 unit = 1% of initial bankroll
                    from src.utils.config import config
                    initial_bankroll = config.get_bankroll_config().get('initial', 100.0)
                    matched_pick.stake_amount = matched_pick.stake_units * (initial_bankroll * 0.01)
                    picks_with_stakes.append(matched_pick)
                else:
                    # Create new pick if no match found
                    logger.warning(f"Could not match sized pick: {sized_data}, creating new pick")
                    try:
                        bet_type = BetType(bet_type_str)
                        odds_str = str(sized_data.get("odds", "-110"))
                        odds = int(odds_str.replace("+", "").replace("-", ""))
                        if "-" in odds_str or odds_str.startswith("-"):
                            odds = -odds
                        
                        # Calculate stake amount from units
                        from src.utils.config import config
                        initial_bankroll = config.get_bankroll_config().get('initial', 100.0)
                        stake_units = float(sized_data.get("units", 0.0))
                        stake_amount = stake_units * (initial_bankroll * 0.01)
                        
                        pick = Pick(
                            game_id=int(game_id_str) if game_id_str and game_id_str != "parlay" else 0,
                            bet_type=bet_type,
                            line=float(line),
                            odds=odds,
                            rationale=" | ".join(sized_data.get("stake_rationale", ["No rationale"])) if isinstance(sized_data.get("stake_rationale"), list) else str(sized_data.get("stake_rationale", "No rationale")),
                            confidence=float(sized_data.get("confidence", 0.5)),
                            expected_value=float(sized_data.get("edge_estimate", 0.0)),
                            book=sized_data.get("book", "draftkings"),
                            stake_units=stake_units,
                            stake_amount=stake_amount
                        )
                        picks_with_stakes.append(pick)
                    except Exception as e:
                        logger.error(f"Error creating pick from sized data: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Error converting sized pick: {e}", exc_info=True)
        
        return picks_with_stakes
    
    def _convert_compliance_results_from_json(
        self, 
        bet_reviews: List[Dict[str, Any]], 
        picks: List[Pick]
    ) -> List[ComplianceResult]:
        """Convert compliance JSON response to ComplianceResult objects"""
        results = []
        
        # Create a map of picks by game_id and bet_type for matching
        pick_map = {}
        for pick in picks:
            key = (pick.game_id, pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type), pick.line)
            pick_map[key] = pick
        
        for review_data in bet_reviews:
            try:
                # Try to match with a pick
                game_id_str = str(review_data.get("game_id", ""))
                bet_type_str = review_data.get("bet_type", "").lower()
                selection = review_data.get("selection", "")
                
                # Extract line from selection if needed
                line = 0.0
                if selection:
                    match = re.search(r'([+-]?\d+\.?\d*)', selection)
                    if match:
                        line = float(match.group(1))
                
                # Find matching pick
                matched_pick = None
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                    bet_type = BetType(bet_type_str)
                    key = (game_id, bet_type.value, line)
                    matched_pick = pick_map.get(key)
                except (ValueError, KeyError):
                    pass
                
                # Parse compliance status
                status = review_data.get("compliance_status", "approved").lower()
                approved = status == "approved" or status == "approved_with_warning"
                
                # Get issues and recommendations
                issues = review_data.get("issues", [])
                if isinstance(issues, str):
                    issues = [issues]
                recommendations = review_data.get("recommendations", [])
                if isinstance(recommendations, str):
                    recommendations = [recommendations]
                
                # Determine risk level based on status
                if status == "approved":
                    risk_level = "low"
                elif status == "approved_with_warning":
                    risk_level = "medium"
                else:
                    risk_level = "high"
                
                # Combine issues and recommendations into reasons
                reasons = issues + recommendations
                if not reasons:
                    reasons = ["No specific issues identified"]
                
                result = ComplianceResult(
                    pick_id=matched_pick.id if matched_pick else 0,
                    approved=approved,
                    risk_level=risk_level,
                    reasons=reasons
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Error converting compliance result: {e}, review_data: {review_data}", exc_info=True)
                # Create a default rejected result on error
                results.append(ComplianceResult(
                    pick_id=0,
                    approved=False,
                    risk_level="high",
                    reasons=[f"Error processing compliance review: {str(e)}"]
                ))
        
        return results
    
    def _convert_card_review_from_json(
        self,
        president_response: Dict[str, Any],
        picks: List[Pick],
        target_date: date
    ) -> CardReview:
        """Convert President's JSON response to CardReview object"""
        # Create a map of picks by game_id and bet_type for matching
        pick_map = {}
        for pick in picks:
            key = (pick.game_id, pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type), pick.line)
            pick_map[key] = pick
        
        # Extract approved pick IDs
        approved_pick_ids = []
        approved_picks_data = president_response.get("approved_picks", [])
        for approved_data in approved_picks_data:
            try:
                game_id_str = str(approved_data.get("game_id", ""))
                bet_type_str = approved_data.get("bet_type", "").lower()
                selection = approved_data.get("selection", "")
                
                # Extract line from selection
                line = 0.0
                if selection:
                    match = re.search(r'([+-]?\d+\.?\d*)', selection)
                    if match:
                        line = float(match.group(1))
                
                # Find matching pick
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                    bet_type = BetType(bet_type_str)
                    key = (game_id, bet_type.value, line)
                    matched_pick = pick_map.get(key)
                    if matched_pick and matched_pick.id:
                        approved_pick_ids.append(matched_pick.id)
                except (ValueError, KeyError):
                    pass
            except Exception as e:
                logger.error(f"Error matching approved pick: {e}")
        
        # Extract rejected pick IDs
        rejected_pick_ids = []
        rejected_picks_data = president_response.get("rejected_picks", [])
        for rejected_data in rejected_picks_data:
            try:
                # Could be a dict with game_id or just a game_id string
                if isinstance(rejected_data, dict):
                    game_id_str = str(rejected_data.get("game_id", ""))
                    reason = rejected_data.get("reason_rejected", "")
                else:
                    game_id_str = str(rejected_data)
                    reason = ""
                
                # Try to find matching pick
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                    # Find any pick for this game_id
                    for pick in picks:
                        if pick.game_id == game_id and pick.id and pick.id not in approved_pick_ids:
                            rejected_pick_ids.append(pick.id)
                            break
                except (ValueError, KeyError):
                    pass
            except Exception as e:
                logger.error(f"Error matching rejected pick: {e}")
        
        # Extract revision requests
        revision_requests = []
        revision_requests_data = president_response.get("revision_requests", [])
        for req_data in revision_requests_data:
            try:
                request_type_str = req_data.get("request_type", "").lower()
                target_agent = req_data.get("target_agent", "")
                feedback = req_data.get("feedback", "")
                
                try:
                    request_type = RevisionRequestType(request_type_str)
                except ValueError:
                    # Map string to enum
                    type_mapping = {
                        "research": RevisionRequestType.RESEARCH,
                        "modeling": RevisionRequestType.MODELING,
                        "selection": RevisionRequestType.SELECTION,
                        "stake_allocation": RevisionRequestType.STAKE_ALLOCATION,
                        "validation": RevisionRequestType.VALIDATION
                    }
                    request_type = type_mapping.get(request_type_str, RevisionRequestType.RESEARCH)
                
                revision_requests.append(RevisionRequest(
                    request_type=request_type,
                    target_agent=target_agent,
                    feedback=feedback
                ))
            except Exception as e:
                logger.error(f"Error converting revision request: {e}, req_data: {req_data}")
        
        # Extract strategy notes
        strategy_notes = president_response.get("high_level_strategy_notes", [])
        if isinstance(strategy_notes, list):
            review_notes = " | ".join(strategy_notes)
        else:
            review_notes = str(strategy_notes) if strategy_notes else ""
        
        # Determine if card is approved (at least one pick approved)
        approved = len(approved_pick_ids) > 0
        
        return CardReview(
            date=target_date,
            approved=approved,
            picks_approved=approved_pick_ids,
            picks_rejected=rejected_pick_ids,
            review_notes=review_notes,
            strategic_directives={},  # Could extract from response if needed
            revision_requests=revision_requests
        )
    
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
            self.researcher,
            self.modeler,
            self.picker,
            self.banker,
            self.compliance,
            self.president,
            self.auditor
        ]
        for agent in agents:
            if hasattr(agent, 'llm_client') and agent.llm_client:
                agent.llm_client.reset_usage_stats()
    
    def _log_token_usage_summary(self) -> None:
        """Log token usage summary for all agents"""
        agents = [
            ("Researcher", self.researcher),
            ("Modeler", self.modeler),
            ("Picker", self.picker),
            ("Banker", self.banker),
            ("Compliance", self.compliance),
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

