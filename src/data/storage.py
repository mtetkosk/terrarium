"""Database storage and models"""

from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Date, Boolean, JSON, ForeignKey, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.orm import scoped_session
import json

from src.data.models import BetType, GameStatus, BetResult
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("data.storage")

Base = declarative_base()


class GameModel(Base):
    """Game database model"""
    __tablename__ = 'games'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    team1 = Column(String, nullable=False)
    team2 = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    venue = Column(String, nullable=True)
    status = Column(SQLEnum(GameStatus), default=GameStatus.SCHEDULED)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    
    # Relationships
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
    model_type = Column(String, nullable=False)
    predicted_spread = Column(Float, nullable=False)
    predicted_total = Column(Float, nullable=True)
    win_probability_team1 = Column(Float, nullable=False)
    win_probability_team2 = Column(Float, nullable=False)
    ev_estimate = Column(Float, nullable=False)
    confidence_score = Column(Float, nullable=False)
    mispricing_detected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    
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
    best_bet = Column(Boolean, default=False)  # True if this is a "best bet" (reviewed by President)
    favorite = Column(Boolean, default=False)  # Deprecated: use best_bet instead. Kept for backwards compatibility
    confidence_score = Column(Integer, default=5)  # 1-10 confidence score
    created_at = Column(DateTime, default=datetime.now)
    
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


# Global database instance
db = Database()

