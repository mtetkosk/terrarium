"""Analytics service for tracking game analytics"""

from datetime import date
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from src.data.storage import (
    Database,
    AnalyticsGameModel,
    AnalyticsOddsModel,
    AnalyticsPredictionModel,
    AnalyticsResultModel,
    BettingLineModel,
    PredictionModel,
    GameModel,
    PickModel,
    BetModel,
    BetType,
    BetResult
)
from src.utils.logging import get_logger

logger = get_logger("data.analytics")


class AnalyticsService:
    """Service for managing analytics data"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize analytics service"""
        self.db = db or Database()
    
    def save_game_analytics(
        self,
        game_id: int,
        game_date: date,
        home_team: str,
        away_team: str,
        home_conference: Optional[str] = None,
        away_conference: Optional[str] = None
    ) -> bool:
        """
        Save or update game analytics
        
        Args:
            game_id: Game ID from games table
            game_date: Date of the game
            home_team: Home team name
            away_team: Away team name
            home_conference: Home team conference (optional)
            away_conference: Away team conference (optional)
            
        Returns:
            True if successful, False otherwise
        """
        session = self.db.get_session()
        try:
            # Check if record exists
            existing = session.query(AnalyticsGameModel).filter_by(
                game_id=game_id,
                date=game_date
            ).first()
            
            if existing:
                # Update existing record
                existing.home_team = home_team
                existing.away_team = away_team
                existing.home_conference = home_conference
                existing.away_conference = away_conference
            else:
                # Create new record
                analytics_game = AnalyticsGameModel(
                    game_id=game_id,
                    date=game_date,
                    home_team=home_team,
                    away_team=away_team,
                    home_conference=home_conference,
                    away_conference=away_conference
                )
                session.add(analytics_game)
            
            session.commit()
            logger.debug(f"Saved game analytics for game_id={game_id}, date={game_date}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving game analytics: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def save_odds_analytics(
        self,
        game_id: int,
        game_date: date,
        primary_book: str = "draftkings"
    ) -> bool:
        """
        Aggregate betting lines into analytics odds format
        
        Args:
            game_id: Game ID from games table
            game_date: Date of the game
            primary_book: Primary book to use for odds (default: draftkings)
            
        Returns:
            True if successful, False otherwise
        """
        session = self.db.get_session()
        try:
            # Get home/away teams from analytics_games
            analytics_game = session.query(AnalyticsGameModel).filter_by(
                game_id=game_id,
                date=game_date
            ).first()
            
            if not analytics_game:
                logger.warning(f"No analytics game found for game_id={game_id}, date={game_date}. Cannot aggregate odds.")
                return False
            
            home_team = analytics_game.home_team
            away_team = analytics_game.away_team
            
            # Get betting lines for this game
            betting_lines = session.query(BettingLineModel).filter_by(
                game_id=game_id
            ).all()
            
            # Initialize odds data
            home_spread = None
            home_spread_odds = None
            away_spread = None
            away_spread_odds = None
            total = None
            over_odds = None
            under_odds = None
            
            # Filter by primary book first, then fall back to any book
            primary_book_lines = [line for line in betting_lines if line.book.lower() == primary_book.lower()]
            lines_to_use = primary_book_lines if primary_book_lines else betting_lines
            
            for line in lines_to_use:
                if line.bet_type == BetType.SPREAD:
                    # Determine if this is home or away spread
                    if line.team and line.team.lower() == home_team.lower():
                        home_spread = line.line
                        home_spread_odds = line.odds
                    elif line.team and line.team.lower() == away_team.lower():
                        away_spread = line.line
                        away_spread_odds = line.odds
                    # If team doesn't match, try to infer from line sign
                    elif line.team is None:
                        # Negative spread typically means home team (favorite)
                        if line.line < 0:
                            home_spread = line.line
                            home_spread_odds = line.odds
                            away_spread = -line.line
                            away_spread_odds = line.odds
                        else:
                            away_spread = line.line
                            away_spread_odds = line.odds
                            home_spread = -line.line
                            home_spread_odds = line.odds
                
                elif line.bet_type == BetType.TOTAL:
                    total = line.line
                    # Determine over/under odds
                    if line.team:
                        team_lower = line.team.lower()
                        if "over" in team_lower:
                            over_odds = line.odds
                        elif "under" in team_lower:
                            under_odds = line.odds
                    # If team is None, we need to find both over and under lines
                    # For now, if we only have one, use it for both (common case where odds are same)
                    if over_odds is None and under_odds is None:
                        over_odds = line.odds
                        under_odds = line.odds
            
            # If we still don't have over/under odds, try to find them from all lines
            if over_odds is None or under_odds is None:
                for line in betting_lines:
                    if line.bet_type == BetType.TOTAL:
                        team_lower = (line.team or "").lower()
                        if "over" in team_lower and over_odds is None:
                            over_odds = line.odds
                        elif "under" in team_lower and under_odds is None:
                            under_odds = line.odds
            
            # Check if record exists
            existing = session.query(AnalyticsOddsModel).filter_by(
                game_id=game_id,
                date=game_date
            ).first()
            
            if existing:
                # Update existing record
                existing.home_spread = home_spread
                existing.home_spread_odds = home_spread_odds
                existing.away_spread = away_spread
                existing.away_spread_odds = away_spread_odds
                existing.total = total
                existing.over_odds = over_odds
                existing.under_odds = under_odds
            else:
                # Create new record
                analytics_odds = AnalyticsOddsModel(
                    game_id=game_id,
                    date=game_date,
                    home_spread=home_spread,
                    home_spread_odds=home_spread_odds,
                    away_spread=away_spread,
                    away_spread_odds=away_spread_odds,
                    total=total,
                    over_odds=over_odds,
                    under_odds=under_odds
                )
                session.add(analytics_odds)
            
            session.commit()
            logger.debug(f"Saved odds analytics for game_id={game_id}, date={game_date}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving odds analytics: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def save_prediction_analytics(
        self,
        game_id: int,
        game_date: date
    ) -> bool:
        """
        Convert prediction model to analytics prediction format
        
        Args:
            game_id: Game ID from games table
            game_date: Date of the game
            
        Returns:
            True if successful, False otherwise
        """
        session = self.db.get_session()
        try:
            # Get the most recent prediction for this game
            prediction = session.query(PredictionModel).filter_by(
                game_id=game_id
            ).order_by(PredictionModel.created_at.desc()).first()
            
            if not prediction:
                logger.warning(f"No prediction found for game_id={game_id}")
                return False
            
            # Extract predicted spread and total
            projected_spread = prediction.predicted_spread
            projected_total = prediction.predicted_total
            
            # Calculate home and away projected scores
            # Formula: home_score = (total + spread) / 2, away_score = (total - spread) / 2
            home_projected_score = None
            away_projected_score = None
            
            if projected_total is not None and projected_spread is not None:
                home_projected_score = (projected_total + projected_spread) / 2.0
                away_projected_score = (projected_total - projected_spread) / 2.0
            
            # Check if record exists
            existing = session.query(AnalyticsPredictionModel).filter_by(
                game_id=game_id,
                date=game_date
            ).first()
            
            if existing:
                # Update existing record
                existing.home_projected_score = home_projected_score
                existing.away_projected_score = away_projected_score
                existing.projected_total = projected_total
                existing.projected_spread = projected_spread
            else:
                # Create new record
                analytics_prediction = AnalyticsPredictionModel(
                    game_id=game_id,
                    date=game_date,
                    home_projected_score=home_projected_score,
                    away_projected_score=away_projected_score,
                    projected_total=projected_total,
                    projected_spread=projected_spread
                )
                session.add(analytics_prediction)
            
            session.commit()
            logger.debug(f"Saved prediction analytics for game_id={game_id}, date={game_date}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving prediction analytics: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def save_result_analytics(
        self,
        game_id: int,
        game_date: date
    ) -> bool:
        """
        Extract results from games.result JSON and save to analytics_results
        
        Args:
            game_id: Game ID from games table
            game_date: Date of the game
            
        Returns:
            True if successful, False otherwise
        """
        session = self.db.get_session()
        try:
            # Get game with result
            game = session.query(GameModel).filter_by(id=game_id).first()
            
            if not game:
                logger.warning(f"No game found for game_id={game_id}")
                return False
            
            if not game.result:
                logger.debug(f"No result available for game_id={game_id}")
                return False
            
            # Extract scores from result JSON
            result_data = game.result
            home_score = result_data.get('home_score')
            away_score = result_data.get('away_score')
            
            # Also try alternative field names
            if home_score is None:
                home_score = result_data.get('homeScore')
            if away_score is None:
                away_score = result_data.get('awayScore')
            
            # Calculate total
            actual_total = None
            if home_score is not None and away_score is not None:
                try:
                    actual_total = int(home_score) + int(away_score)
                except (ValueError, TypeError):
                    pass
            
            # Check if record exists
            existing = session.query(AnalyticsResultModel).filter_by(
                game_id=game_id,
                date=game_date
            ).first()
            
            if existing:
                # Update existing record
                existing.home_actual_score = int(home_score) if home_score is not None else None
                existing.away_actual_score = int(away_score) if away_score is not None else None
                existing.actual_total = actual_total
            else:
                # Create new record
                analytics_result = AnalyticsResultModel(
                    game_id=game_id,
                    date=game_date,
                    home_actual_score=int(home_score) if home_score is not None else None,
                    away_actual_score=int(away_score) if away_score is not None else None,
                    actual_total=actual_total
                )
                session.add(analytics_result)
            
            session.commit()
            logger.debug(f"Saved result analytics for game_id={game_id}, date={game_date}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving result analytics: {e}", exc_info=True)
            return False
        finally:
            session.close()
    
    def get_picks_for_date(self, target_date: date) -> List[PickModel]:
        """
        Get all picks for a specific date.
        
        CRITICAL: Returns only the latest pick per game_id (enforced by unique constraint).
        All picks are guaranteed to be unique by (game_id, pick_date).
        
        Args:
            target_date: Date to get picks for
            
        Returns:
            List of PickModel objects, one per game_id
        """
        session = self.db.get_session()
        try:
            # Query by pick_date, with fallback to DATE(created_at) for legacy records without pick_date
            picks = session.query(PickModel).filter(
                or_(
                    PickModel.pick_date == target_date,
                    func.date(PickModel.created_at) == target_date
                )
            ).order_by(PickModel.created_at.desc()).all()
            
            # Ensure pick_date is set for any legacy records without it
            for pick in picks:
                if not pick.pick_date:
                    pick.pick_date = target_date
                    session.commit()
            
            logger.debug(f"Retrieved {len(picks)} picks for {target_date}")
            return picks
        except Exception as e:
            logger.error(f"Error getting picks for date {target_date}: {e}", exc_info=True)
            return []
        finally:
            session.close()
    
    def get_results_for_date(self, target_date: date) -> Dict[str, Any]:
        """
        Get all bet results for picks made on a specific date.
        
        Args:
            target_date: Date to get results for (date when picks were made)
            
        Returns:
            Dictionary with:
            - picks: List of PickModel objects
            - bets: List of BetModel objects (matched by pick_id)
            - stats: Dictionary with summary statistics
        """
        session = self.db.get_session()
        try:
            # Get picks for this date
            picks = self.get_picks_for_date(target_date)
            
            if not picks:
                return {
                    'picks': [],
                    'bets': [],
                    'stats': {
                        'total_picks': 0,
                        'settled_bets': 0,
                        'wins': 0,
                        'losses': 0,
                        'pushes': 0,
                        'pending': 0
                    }
                }
            
            # Get bets for these picks
            pick_ids = [p.id for p in picks if p.id]
            bets = session.query(BetModel).filter(
                BetModel.pick_id.in_(pick_ids)
            ).all() if pick_ids else []
            
            # Create bet lookup
            bet_map = {bet.pick_id: bet for bet in bets}
            
            # Calculate stats
            stats = {
                'total_picks': len(picks),
                'settled_bets': len([b for b in bets if b.result != BetResult.PENDING]),
                'wins': len([b for b in bets if b.result == BetResult.WIN]),
                'losses': len([b for b in bets if b.result == BetResult.LOSS]),
                'pushes': len([b for b in bets if b.result == BetResult.PUSH]),
                'pending': len([b for b in bets if b.result == BetResult.PENDING])
            }
            
            logger.debug(f"Retrieved {len(picks)} picks and {len(bets)} bets for {target_date}")
            return {
                'picks': picks,
                'bets': bets,
                'bet_map': bet_map,
                'stats': stats
            }
        except Exception as e:
            logger.error(f"Error getting results for date {target_date}: {e}", exc_info=True)
            return {
                'picks': [],
                'bets': [],
                'bet_map': {},
                'stats': {
                    'total_picks': 0,
                    'settled_bets': 0,
                    'wins': 0,
                    'losses': 0,
                    'pushes': 0,
                    'pending': 0
                }
            }
        finally:
            session.close()
    
    def get_betting_lines_for_date(self, target_date: date) -> List[BettingLineModel]:
        """
        Get all betting lines for games on a specific date.
        
        Args:
            target_date: Date to get betting lines for
            
        Returns:
            List of BettingLineModel objects
        """
        session = self.db.get_session()
        try:
            # Get games for this date
            games = session.query(GameModel).filter(
                GameModel.date == target_date
            ).all()
            
            if not games:
                return []
            
            game_ids = [g.id for g in games]
            
            # Get betting lines for these games
            lines = session.query(BettingLineModel).filter(
                BettingLineModel.game_id.in_(game_ids)
            ).all()
            
            logger.debug(f"Retrieved {len(lines)} betting lines for {target_date}")
            return lines
        except Exception as e:
            logger.error(f"Error getting betting lines for date {target_date}: {e}", exc_info=True)
            return []
        finally:
            session.close()

