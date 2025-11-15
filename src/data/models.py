"""Pydantic data models for the terrarium system"""

from datetime import datetime, date
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class BetType(str, Enum):
    """Types of bets"""
    SPREAD = "spread"
    TOTAL = "total"
    MONEYLINE = "moneyline"
    PARLAY = "parlay"


class GameStatus(str, Enum):
    """Game status"""
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class BetResult(str, Enum):
    """Bet result"""
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    PENDING = "pending"


class RevisionRequestType(str, Enum):
    """Types of revision requests"""
    RESEARCH = "research"
    MODELING = "modeling"
    SELECTION = "selection"
    STAKE_ALLOCATION = "stake_allocation"
    VALIDATION = "validation"


class Game(BaseModel):
    """Game model"""
    id: Optional[int] = None
    team1: str = Field(..., description="Home team name")
    team2: str = Field(..., description="Away team name")
    date: date = Field(..., description="Game date")
    venue: Optional[str] = Field(None, description="Venue name")
    status: GameStatus = Field(GameStatus.SCHEDULED, description="Game status")
    result: Optional[Dict[str, Any]] = Field(None, description="Game result with scores")
    
    class Config:
        use_enum_values = True


class BettingLine(BaseModel):
    """Betting line model"""
    id: Optional[int] = None
    game_id: int = Field(..., description="Associated game ID")
    book: str = Field(..., description="Sportsbook name")
    bet_type: BetType = Field(..., description="Type of bet")
    line: float = Field(..., description="Line value (spread/total) or 0 for moneyline")
    odds: int = Field(..., description="American odds (e.g., -110, +150)")
    timestamp: datetime = Field(default_factory=datetime.now, description="When line was captured")
    
    class Config:
        use_enum_values = True


class Injury(BaseModel):
    """Injury report model"""
    player: str = Field(..., description="Player name")
    team: str = Field(..., description="Team name")
    injury: str = Field(..., description="Injury description")
    status: str = Field(..., description="Status (out, questionable, probable)")
    position: Optional[str] = Field(None, description="Player position")


class TeamStats(BaseModel):
    """Team statistics model"""
    team: str = Field(..., description="Team name")
    wins: int = Field(..., description="Number of wins")
    losses: int = Field(..., description="Number of losses")
    points_per_game: float = Field(..., description="Points per game")
    points_allowed_per_game: float = Field(..., description="Points allowed per game")
    offensive_rating: Optional[float] = Field(None, description="Offensive rating")
    defensive_rating: Optional[float] = Field(None, description="Defensive rating")
    pace: Optional[float] = Field(None, description="Pace of play")
    additional_stats: Optional[Dict[str, Any]] = Field(None, description="Additional statistics")


class GameInsight(BaseModel):
    """Game insight bundle from Researcher"""
    id: Optional[int] = None
    game_id: int = Field(..., description="Associated game ID")
    injuries: List[Injury] = Field(default_factory=list, description="Injury reports")
    team1_stats: Optional[TeamStats] = Field(None, description="Team 1 statistics")
    team2_stats: Optional[TeamStats] = Field(None, description="Team 2 statistics")
    matchup_notes: str = Field(default="", description="Matchup context and notes")
    confidence_factors: Dict[str, float] = Field(
        default_factory=dict,
        description="Confidence factors (e.g., data_quality, injury_impact)"
    )
    rest_days_team1: Optional[int] = Field(None, description="Days of rest for team 1")
    rest_days_team2: Optional[int] = Field(None, description="Days of rest for team 2")
    travel_impact: Optional[str] = Field(None, description="Travel impact notes")
    rivalry: bool = Field(False, description="Is this a rivalry game?")
    created_at: datetime = Field(default_factory=datetime.now)


class Prediction(BaseModel):
    """Prediction model from Modeler"""
    id: Optional[int] = None
    game_id: int = Field(..., description="Associated game ID")
    model_type: str = Field(..., description="Model type used")
    predicted_spread: float = Field(..., description="Predicted point spread (team1 - team2)")
    predicted_total: Optional[float] = Field(None, description="Predicted total points")
    win_probability_team1: float = Field(..., description="Win probability for team 1")
    win_probability_team2: float = Field(..., ge=0.0, le=1.0, description="Win probability for team 2")
    ev_estimate: float = Field(..., description="Expected value estimate")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Model confidence")
    mispricing_detected: bool = Field(False, description="Whether mispricing was detected")
    created_at: datetime = Field(default_factory=datetime.now)


class Pick(BaseModel):
    """Pick model from Picker"""
    id: Optional[int] = None
    game_id: int = Field(0, description="Associated game ID (0 for parlays)")
    bet_type: BetType = Field(..., description="Type of bet")
    line: float = Field(0.0, description="Line value (0 for parlays)")
    odds: int = Field(..., description="American odds")
    stake_units: float = Field(0.0, description="Stake in units (assigned by Banker)")
    stake_amount: float = Field(0.0, description="Stake in dollars")
    rationale: str = Field(..., description="Reasoning for the pick")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence level")
    expected_value: float = Field(..., description="Expected value")
    book: str = Field(..., description="Sportsbook")
    parlay_legs: Optional[List[int]] = Field(None, description="List of pick IDs that make up this parlay (if bet_type is PARLAY)")
    created_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        use_enum_values = True


class Bet(BaseModel):
    """Bet model (placed bet)"""
    id: Optional[int] = None
    pick_id: int = Field(..., description="Associated pick ID")
    placed_at: datetime = Field(default_factory=datetime.now, description="When bet was placed")
    result: BetResult = Field(BetResult.PENDING, description="Bet result")
    payout: float = Field(0.0, description="Payout amount")
    profit_loss: float = Field(0.0, description="Profit or loss")
    settled_at: Optional[datetime] = Field(None, description="When bet was settled")
    
    class Config:
        use_enum_values = True


class Bankroll(BaseModel):
    """Bankroll model"""
    id: Optional[int] = None
    date: date = Field(default_factory=date.today, description="Date")
    balance: float = Field(..., description="Current balance")
    total_wagered: float = Field(0.0, description="Total amount wagered")
    total_profit: float = Field(0.0, description="Total profit/loss")
    active_bets: int = Field(0, description="Number of active bets")
    created_at: datetime = Field(default_factory=datetime.now)


class ComplianceResult(BaseModel):
    """Compliance validation result"""
    pick_id: int = Field(..., description="Pick ID being validated")
    approved: bool = Field(..., description="Whether pick is approved")
    reasons: List[str] = Field(default_factory=list, description="Approval/rejection reasons")
    risk_level: str = Field(..., description="Risk level (low, medium, high)")
    created_at: datetime = Field(default_factory=datetime.now)


class RevisionRequest(BaseModel):
    """Request for revision from President"""
    id: Optional[int] = None
    request_type: RevisionRequestType = Field(..., description="Type of revision needed")
    target_agent: str = Field(..., description="Agent that needs to revise")
    original_output_id: Optional[int] = Field(None, description="ID of original output")
    feedback: str = Field(..., description="Feedback on what needs to be revised")
    priority: str = Field("medium", description="Priority (low, medium, high)")
    created_at: datetime = Field(default_factory=datetime.now)
    resolved: bool = Field(False, description="Whether revision is complete")
    resolved_at: Optional[datetime] = Field(None, description="When revision was resolved")


class CardReview(BaseModel):
    """Card review from President"""
    date: date = Field(default_factory=date.today)
    approved: bool = Field(..., description="Whether card is approved")
    picks_approved: List[int] = Field(default_factory=list, description="Approved pick IDs")
    picks_rejected: List[int] = Field(default_factory=list, description="Rejected pick IDs")
    review_notes: str = Field(default="", description="Review notes")
    strategic_directives: Dict[str, Any] = Field(default_factory=dict, description="Strategic adjustments")
    revision_requests: List[RevisionRequest] = Field(default_factory=list, description="Revision requests")
    created_at: datetime = Field(default_factory=datetime.now)


class DailyReport(BaseModel):
    """Daily performance report from Auditor"""
    date: date = Field(default_factory=date.today)
    total_picks: int = Field(..., description="Total picks made")
    wins: int = Field(0, description="Number of wins")
    losses: int = Field(0, description="Number of losses")
    pushes: int = Field(0, description="Number of pushes")
    win_rate: float = Field(0.0, description="Win rate")
    total_wagered: float = Field(0.0, description="Total wagered")
    total_payout: float = Field(0.0, description="Total payout")
    profit_loss: float = Field(0.0, description="Daily P&L")
    roi: float = Field(0.0, description="Return on investment")
    accuracy_metrics: Dict[str, float] = Field(default_factory=dict, description="Additional metrics")
    insights: Dict[str, Any] = Field(default_factory=dict, description="What went well and what needs improvement")
    recommendations: List[str] = Field(default_factory=list, description="Actionable recommendations")
    created_at: datetime = Field(default_factory=datetime.now)


class AccuracyMetrics(BaseModel):
    """Accuracy metrics from Auditor"""
    total_picks: int = Field(..., description="Total picks")
    wins: int = Field(..., description="Wins")
    losses: int = Field(..., description="Losses")
    pushes: int = Field(..., description="Pushes")
    win_rate: float = Field(..., description="Win rate")
    roi: float = Field(..., description="Return on investment")
    average_confidence: float = Field(..., description="Average confidence of picks")
    ev_realized: float = Field(..., description="Realized expected value")
    period_start: date = Field(..., description="Period start date")
    period_end: date = Field(..., description="Period end date")


class Conflict(BaseModel):
    """Conflict between agents"""
    conflict_type: str = Field(..., description="Type of conflict")
    description: str = Field(..., description="Conflict description")
    involved_agents: List[str] = Field(..., description="Agents involved")
    severity: str = Field(..., description="Severity (low, medium, high)")
    created_at: datetime = Field(default_factory=datetime.now)


class Resolution(BaseModel):
    """Conflict resolution"""
    conflict_id: Optional[int] = None
    resolution: str = Field(..., description="Resolution description")
    decision: str = Field(..., description="Decision made")
    resolved_by: str = Field(..., description="Agent that resolved")
    created_at: datetime = Field(default_factory=datetime.now)
