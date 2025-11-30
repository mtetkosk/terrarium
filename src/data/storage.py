"""Database storage and models"""

from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Date, Boolean, JSON, ForeignKey, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.orm import scoped_session
import json

from src.data.models import BetType, GameStatus, BetResult
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("data.storage")

Base = declarative_base()


class TeamModel(Base):
    """Team database model"""
    __tablename__ = 'teams'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    normalized_team_name = Column(String, nullable=False, unique=True)  # Canonical team name for matching
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    games_as_team1 = relationship("GameModel", foreign_keys="GameModel.team1_id", back_populates="team1_ref")
    games_as_team2 = relationship("GameModel", foreign_keys="GameModel.team2_id", back_populates="team2_ref")
    picks = relationship("PickModel", back_populates="team_ref")


class GameModel(Base):
    """Game database model"""
    __tablename__ = 'games'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    team1_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    team2_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    date = Column(Date, nullable=False)
    venue = Column(String, nullable=True)
    status = Column(SQLEnum(GameStatus), default=GameStatus.SCHEDULED)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    team1_ref = relationship("TeamModel", foreign_keys=[team1_id], back_populates="games_as_team1")
    team2_ref = relationship("TeamModel", foreign_keys=[team2_id], back_populates="games_as_team2")
    betting_lines = relationship("BettingLineModel", back_populates="game")
    insights = relationship("GameInsightModel", back_populates="game", uselist=False)
    predictions = relationship("PredictionModel", back_populates="game")
    picks = relationship("PickModel", back_populates="game")


class BettingLineModel(Base):
    """Betting line database model"""
    __tablename__ = 'betting_lines'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey('games.id'), nullable=False)
    book = Column(String, nullable=False)
    bet_type = Column(SQLEnum(BetType), nullable=False)
    line = Column(Float, nullable=False)
    odds = Column(Integer, nullable=False)
    team = Column(String, nullable=True)  # Team name for spread/moneyline, "over"/"under" for totals
    timestamp = Column(DateTime, default=datetime.now)
    
    # Unique constraint: one betting line per (game_id, book, bet_type, team) combination
    __table_args__ = (
        UniqueConstraint('game_id', 'book', 'bet_type', 'team', name='uq_betting_lines_game_book_type_team'),
    )
    
    # Relationships
    game = relationship("GameModel", back_populates="betting_lines")


class InjuryModel(Base):
    """Injury database model (embedded in GameInsight)"""
    __tablename__ = 'injuries'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_insight_id = Column(Integer, ForeignKey('game_insights.id'), nullable=False)
    player = Column(String, nullable=False)
    team = Column(String, nullable=False)
    injury = Column(String, nullable=False)
    status = Column(String, nullable=False)
    position = Column(String, nullable=True)


class GameInsightModel(Base):
    """Game insight database model"""
    __tablename__ = 'game_insights'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey('games.id'), nullable=False, unique=True)
    team1_stats = Column(JSON, nullable=True)
    team2_stats = Column(JSON, nullable=True)
    matchup_notes = Column(String, nullable=True)
    confidence_factors = Column(JSON, nullable=True)
    rest_days_team1 = Column(Integer, nullable=True)
    rest_days_team2 = Column(Integer, nullable=True)
    travel_impact = Column(String, nullable=True)
    rivalry = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    game = relationship("GameModel", back_populates="insights")
    injuries = relationship("InjuryModel", back_populates="insight")


InjuryModel.insight = relationship("GameInsightModel", back_populates="injuries")


class PredictionModel(Base):
    """Prediction database model"""
    __tablename__ = 'predictions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey('games.id'), nullable=False)
    prediction_date = Column(Date, nullable=False)
    model_type = Column(String, nullable=False)
    predicted_spread = Column(Float, nullable=False)
    predicted_total = Column(Float, nullable=True)
    win_probability_team1 = Column(Float, nullable=False)
    win_probability_team2 = Column(Float, nullable=False)
    ev_estimate = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=False)
    mispricing_detected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # Unique constraint: only one prediction per game_id per date
    __table_args__ = (
        UniqueConstraint('game_id', 'prediction_date', name='uq_predictions_game_date'),
    )
    
    # Relationships
    game = relationship("GameModel", back_populates="predictions")


class PickModel(Base):
    """Pick database model"""
    __tablename__ = 'picks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey('games.id'), nullable=False)
    bet_type = Column(SQLEnum(BetType), nullable=False)
    line = Column(Float, nullable=False)
    odds = Column(Integer, nullable=False)
    stake_units = Column(Float, default=0.0)
    stake_amount = Column(Float, default=0.0)
    rationale = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    expected_value = Column(Float, nullable=False)
    book = Column(String, nullable=False)
    parlay_legs = Column(JSON, nullable=True)  # List of pick IDs for parlays
    selection_text = Column(String, nullable=True)  # Original selection text from Picker
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=True)  # Team ID for spread/moneyline bets (null for totals)
    best_bet = Column(Boolean, default=False)  # True if this is a "best bet" (reviewed by President)
    
    # Relationships
    team_ref = relationship("TeamModel", back_populates="picks")
    favorite = Column(Boolean, default=False)  # Deprecated: use best_bet instead. Kept for backwards compatibility
    confidence_score = Column(Integer, default=5)  # 1-10 confidence score
    created_at = Column(DateTime, default=datetime.now)
    pick_date = Column(Date, nullable=True)  # Date of the pick (for unique constraint)
    
    # Unique constraint: only one pick per game_id per date
    __table_args__ = (
        UniqueConstraint('game_id', 'pick_date', name='uq_picks_game_date'),
    )
    
    # Relationships
    game = relationship("GameModel", back_populates="picks")
    bet = relationship("BetModel", back_populates="pick", uselist=False)
    compliance_result = relationship("ComplianceResultModel", back_populates="pick", uselist=False)


class BetModel(Base):
    """Bet database model"""
    __tablename__ = 'bets'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    pick_id = Column(Integer, ForeignKey('picks.id'), nullable=False, unique=True)
    placed_at = Column(DateTime, default=datetime.now)
    result = Column(SQLEnum(BetResult), default=BetResult.PENDING)
    payout = Column(Float, default=0.0)
    profit_loss = Column(Float, default=0.0)
    settled_at = Column(DateTime, nullable=True)
    
    # Relationships
    pick = relationship("PickModel", back_populates="bet")


class ComplianceResultModel(Base):
    """Compliance result database model"""
    __tablename__ = 'compliance_results'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    pick_id = Column(Integer, ForeignKey('picks.id'), nullable=False, unique=True)
    approved = Column(Boolean, nullable=False)
    reasons = Column(JSON, nullable=True)  # List of strings
    risk_level = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
    pick = relationship("PickModel", back_populates="compliance_result")


class BankrollModel(Base):
    """Bankroll database model"""
    __tablename__ = 'bankroll_history'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, default=date.today)
    balance = Column(Float, nullable=False)
    total_wagered = Column(Float, default=0.0)
    total_profit = Column(Float, default=0.0)
    active_bets = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)


class AgentLogModel(Base):
    """Agent log database model"""
    __tablename__ = 'agent_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.now)
    action = Column(String, nullable=False)
    data_json = Column(JSON, nullable=True)


class RevisionRequestModel(Base):
    """Revision request database model"""
    __tablename__ = 'revision_requests'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_type = Column(String, nullable=False)
    target_agent = Column(String, nullable=False)
    original_output_id = Column(Integer, nullable=True)
    feedback = Column(String, nullable=False)
    priority = Column(String, nullable=False, default='medium')
    created_at = Column(DateTime, default=datetime.now)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)


class CardReviewModel(Base):
    """Card review database model"""
    __tablename__ = 'card_reviews'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, default=date.today)
    approved = Column(Boolean, nullable=False)
    picks_approved = Column(JSON, nullable=True)  # List of pick IDs
    picks_rejected = Column(JSON, nullable=True)  # List of pick IDs
    review_notes = Column(String, nullable=True)
    strategic_directives = Column(JSON, nullable=True)
    revision_requests = Column(JSON, nullable=True)  # List of revision request data
    created_at = Column(DateTime, default=datetime.now)


class DailyReportModel(Base):
    """Daily report database model"""
    __tablename__ = 'daily_reports'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, default=date.today, unique=True)
    total_picks = Column(Integer, nullable=False)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    pushes = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    total_wagered = Column(Float, default=0.0)
    total_payout = Column(Float, default=0.0)
    profit_loss = Column(Float, default=0.0)
    roi = Column(Float, default=0.0)
    accuracy_metrics = Column(JSON, nullable=True)
    insights = Column(JSON, nullable=True)  # What went well and what needs improvement
    recommendations = Column(JSON, nullable=True)  # List of recommendations
    created_at = Column(DateTime, default=datetime.now)


class Database:
    """Database interface"""
    
    def __init__(self, database_url: Optional[str] = None):
        """Initialize database connection"""
        self.database_url = database_url or config.get_database_url()
        self.engine = create_engine(self.database_url, echo=False)
        self.SessionLocal = scoped_session(sessionmaker(bind=self.engine))
        Base.metadata.create_all(self.engine)
        # Run migrations for schema updates
        self._migrate_schema()
    
    def get_session(self) -> Session:
        """Get database session"""
        return self.SessionLocal()
    
    def close(self):
        """Close database connection"""
        self.SessionLocal.remove()
    
    def create_tables(self):
        """Create all tables"""
        Base.metadata.create_all(self.engine)
    
    def drop_tables(self):
        """Drop all tables (use with caution)"""
        Base.metadata.drop_all(self.engine)
    
    def _migrate_schema(self):
        """Migrate database schema to add new columns"""
        from sqlalchemy import inspect, text
        
        inspector = inspect(self.engine)
        table_names = inspector.get_table_names()
        
        # Migrate betting_lines table
        if 'betting_lines' in table_names:
            existing_columns = [col['name'] for col in inspector.get_columns('betting_lines')]
            
            # Add team column if missing
            if 'team' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE betting_lines ADD COLUMN team VARCHAR"))
                        conn.commit()
                    logger.info("✅ Added 'team' column to betting_lines table")
                except Exception as e:
                    logger.warning(f"Could not add 'team' column to betting_lines: {e}")
        
        # Migrate picks table
        if 'picks' in table_names:
            # Get existing columns
            existing_columns = [col['name'] for col in inspector.get_columns('picks')]
            
            # Add favorite column if missing
            if 'favorite' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        # SQLite uses INTEGER for booleans (0/1)
                        conn.execute(text("ALTER TABLE picks ADD COLUMN favorite INTEGER DEFAULT 0"))
                        conn.commit()
                    logger.info("✅ Added 'favorite' column to picks table")
                except Exception as e:
                    logger.warning(f"Could not add 'favorite' column: {e}")
            
            # Add confidence_score column if missing
            if 'confidence_score' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE picks ADD COLUMN confidence_score INTEGER DEFAULT 5"))
                        conn.commit()
                    logger.info("✅ Added 'confidence_score' column to picks table")
                except Exception as e:
                    logger.warning(f"Could not add 'confidence_score' column: {e}")
            
            # Add best_bet column if missing
            if 'best_bet' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        # SQLite uses INTEGER for booleans (0/1)
                        conn.execute(text("ALTER TABLE picks ADD COLUMN best_bet INTEGER DEFAULT 0"))
                        conn.commit()
                    logger.info("✅ Added 'best_bet' column to picks table")
                except Exception as e:
                    logger.warning(f"Could not add 'best_bet' column: {e}")
            
            # Add selection_text column if missing
            if 'selection_text' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE picks ADD COLUMN selection_text VARCHAR"))
                        conn.commit()
                    logger.info("✅ Added 'selection_text' column to picks table")
                except Exception as e:
                    logger.warning(f"Could not add 'selection_text' column: {e}")
            
            # Add pick_date column if missing and migrate existing data
            if 'pick_date' not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        # Add pick_date column
                        conn.execute(text("ALTER TABLE picks ADD COLUMN pick_date DATE"))
                        # Populate pick_date from created_at for existing records
                        conn.execute(text("UPDATE picks SET pick_date = DATE(created_at) WHERE pick_date IS NULL"))
                        conn.commit()
                    logger.info("Added 'pick_date' column to picks table")
                except Exception as e:
                    logger.warning(f"Could not add 'pick_date' column: {e}")
            
            # Add prediction_date column to predictions table if missing
            if 'predictions' in table_names:
                existing_columns = [col['name'] for col in inspector.get_columns('predictions')]
                
                if 'prediction_date' not in existing_columns:
                    try:
                        with self.engine.connect() as conn:
                            conn.execute(text("ALTER TABLE predictions ADD COLUMN prediction_date DATE"))
                            conn.execute(text("UPDATE predictions SET prediction_date = DATE(created_at) WHERE prediction_date IS NULL"))
                            conn.commit()
                        logger.info("Added 'prediction_date' column to predictions table")
                    except Exception as e:
                        logger.warning(f"Could not add 'prediction_date' column: {e}")
                
                # Add unique constraint on predictions if it doesn't exist
                try:
                    with self.engine.connect() as conn:
                        inspector = inspect(self.engine)
                        indexes = inspector.get_indexes('predictions')
                        constraint_exists = any(idx.get('name') == 'uq_predictions_game_date' for idx in indexes)
                        
                        if not constraint_exists:
                            conn.execute(text("""
                                CREATE UNIQUE INDEX IF NOT EXISTS uq_predictions_game_date 
                                ON predictions(game_id, prediction_date)
                                WHERE prediction_date IS NOT NULL
                            """))
                            conn.commit()
                            logger.info("Added unique constraint on predictions (game_id, prediction_date)")
                except Exception as e:
                    logger.warning(f"Could not add unique constraint on predictions: {e}")
            
            # Add unique constraint on picks if it doesn't exist
            try:
                with self.engine.connect() as conn:
                    # Check if constraint already exists
                    inspector = inspect(self.engine)
                    indexes = inspector.get_indexes('picks')
                    constraint_exists = any(idx.get('name') == 'uq_picks_game_date' for idx in indexes)
                    
                    if not constraint_exists:
                        # SQLite doesn't support adding unique constraints directly, so we use a unique index
                        conn.execute(text("""
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_picks_game_date 
                            ON picks(game_id, pick_date)
                            WHERE pick_date IS NOT NULL
                        """))
                        conn.commit()
                        logger.info("Added unique constraint on picks (game_id, pick_date)")
            except Exception as e:
                logger.warning(f"Could not add unique constraint on picks: {e}")


    # Query helper methods (moved from AnalyticsService)
    def get_picks_for_date(self, target_date: date) -> List['PickModel']:
        """
        Get all picks for a specific date.
        
        Args:
            target_date: Date to get picks for
            
        Returns:
            List of PickModel objects
        """
        session = self.get_session()
        try:
            from sqlalchemy import or_, func
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
        session = self.get_session()
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
    
    def get_betting_lines_for_date(self, target_date: date) -> List['BettingLineModel']:
        """
        Get all betting lines for games on a specific date.
        
        Args:
            target_date: Date to get betting lines for
            
        Returns:
            List of BettingLineModel objects
        """
        session = self.get_session()
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
    
    def get_historical_performance(self, target_date: date, days_back: int = 7) -> Optional[Dict[str, Any]]:
        """
        Get historical performance data from recent days for learning.
        
        Args:
            target_date: Current date
            days_back: Number of days to look back (default: 7)
            
        Returns:
            Dictionary with historical performance summary or None if no data
        """
        session = self.get_session()
        try:
            from datetime import timedelta
            
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


# Global database instance
db = Database()

