"""Coordinator for agent workflow"""

from typing import List, Optional
from datetime import date, datetime

from src.data.models import (
    Game, BettingLine, GameInsight, Prediction, Pick, Bet, Bankroll,
    ComplianceResult, CardReview
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
    
    def run_daily_workflow(self, target_date: Optional[date] = None, max_revisions: int = 2) -> CardReview:
        """Run the daily betting workflow with revision support"""
        if target_date is None:
            target_date = date.today()
        
        logger.info("=" * 80)
        logger.info(f"🚀 STARTING DAILY WORKFLOW FOR {target_date}")
        logger.info("=" * 80)
        
        try:
            # Step 1: Scrape games
            self.researcher.interaction_logger.log_agent_start("GamesScraper", f"Scraping games for {target_date}")
            games = self.games_scraper.scrape_games(target_date)
            games = self._save_games(games)
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
                insights = self.researcher.process(games)
                self.researcher.interaction_logger.log_agent_complete("Researcher", f"Generated {len(insights)} insights")
                
                # Step 4: Modeler generates predictions
                self.modeler.interaction_logger.log_agent_start("Modeler", f"Modeling {len(insights)} games")
                self.modeler.interaction_logger.log_handoff("Researcher", "Modeler", "GameInsights", len(insights))
                predictions = self.modeler.process(insights, lines)
                self.modeler.interaction_logger.log_agent_complete("Modeler", f"Generated {len(predictions)} predictions")
                
                # Step 5: Picker selects picks
                self.picker.interaction_logger.log_agent_start("Picker", f"Selecting from {len(predictions)} predictions")
                self.picker.interaction_logger.log_handoff("Modeler", "Picker", "Predictions", len(predictions))
                picks = self.picker.process(predictions, insights, lines)
                self.picker.interaction_logger.log_agent_complete("Picker", f"Selected {len(picks)} picks")
                
                if not picks:
                    logger.warning("No picks selected. Ending workflow.")
                    return CardReview(
                        date=target_date,
                        approved=False,
                        picks_approved=[],
                        picks_rejected=[],
                        review_notes="No picks met selection criteria."
                    )
                
                # Step 6: Banker allocates stakes
                self.banker.interaction_logger.log_agent_start("Banker", f"Allocating stakes for {len(picks)} picks")
                self.banker.interaction_logger.log_handoff("Picker", "Banker", "Picks", len(picks))
                picks_with_stakes = self.banker.process(picks)
                self.banker.interaction_logger.log_agent_complete("Banker", f"Allocated stakes to {len(picks_with_stakes)} picks")
                
                # Save picks
                for pick in picks_with_stakes:
                    self._save_pick(pick)
                
                # Step 7: Compliance validates
                self.compliance.interaction_logger.log_agent_start("Compliance", f"Validating {len(picks_with_stakes)} picks")
                self.compliance.interaction_logger.log_handoff("Banker", "Compliance", "Picks", len(picks_with_stakes))
                bankroll = self.banker.get_current_bankroll()
                compliance_results = self.compliance.process(
                    picks_with_stakes, insights, bankroll
                )
                approved_count = sum(1 for r in compliance_results if r.approved)
                self.compliance.interaction_logger.log_agent_complete(
                    "Compliance",
                    f"Approved {approved_count}/{len(compliance_results)} picks"
                )
                
                # Step 8: President reviews and approves
                self.president.interaction_logger.log_handoff("Compliance", "President", "ComplianceResults", len(compliance_results))
                review = self.president.process(
                    picks_with_stakes, compliance_results, bankroll.balance,
                    insights, predictions
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
            
            # Step 9: Place bets (if approved)
            if review.approved:
                approved_picks = [
                    p for p in picks_with_stakes
                    if (p.id or 0) in review.picks_approved
                ]
                logger.info(f"💰 Placing {len(approved_picks)} bets")
                self._place_bets(approved_picks)
                
                # Update bankroll
                self.banker.update_bankroll(approved_picks)
            else:
                logger.warning("❌ Card not approved - no bets placed")
            
            logger.info("=" * 80)
            logger.info(f"✅ DAILY WORKFLOW COMPLETED - Card {'APPROVED' if review.approved else 'REJECTED'}")
            logger.info("=" * 80)
            return review
            
        except Exception as e:
            logger.error(f"❌ Error in daily workflow: {e}", exc_info=True)
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
    
    def _save_pick(self, pick: Pick) -> None:
        """Save pick to database"""
        if not self.db or pick.game_id == 0:
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
                book=pick.book
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
    
    def close(self):
        """Close database connection"""
        if self.db:
            self.db.close()

