"""Data models for the terrarium system"""

from __future__ import annotations

from datetime import datetime, date
from enum import Enum
from typing import List, Optional, Any, Dict
from dataclasses import dataclass, field


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


@dataclass
class Game:
    """Game model"""
    team1: str  # Keep for backwards compatibility, but prefer team1_id
    team2: str  # Keep for backwards compatibility, but prefer team2_id
    date: date
    id: Optional[int] = None
    team1_id: Optional[int] = None  # Team ID (preferred)
    team2_id: Optional[int] = None  # Team ID (preferred)
    venue: Optional[str] = None
    status: GameStatus = GameStatus.SCHEDULED
    result: Optional[Dict[str, Any]] = None


@dataclass
class BettingLine:
    """Betting line model"""
    game_id: int
    book: str
    bet_type: BetType
    line: float
    odds: int
    team: Optional[str] = None  # Team name for spread/moneyline, "over"/"under" for totals
    id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Injury:
    """Injury report model"""
    player: str
    team: str
    injury: str
    status: str
    position: Optional[str] = None


@dataclass
class TeamStats:
    """Team statistics model"""
    team: str
    wins: int
    losses: int
    points_per_game: float
    points_allowed_per_game: float
    offensive_rating: Optional[float] = None
    defensive_rating: Optional[float] = None
    pace: Optional[float] = None
    additional_stats: Optional[Dict[str, Any]] = None


@dataclass
class GameInsight:
    """Game insight bundle from Researcher"""
    game_id: int
    id: Optional[int] = None
    injuries: List[Injury] = field(default_factory=list)
    team1_stats: Optional[TeamStats] = None
    team2_stats: Optional[TeamStats] = None
    matchup_notes: str = ""
    confidence_factors: Dict[str, float] = field(default_factory=dict)
    rest_days_team1: Optional[int] = None
    rest_days_team2: Optional[int] = None
    travel_impact: Optional[str] = None
    rivalry: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Prediction:
    """Prediction model from Modeler"""
    game_id: int
    model_type: str
    predicted_spread: float
    win_probability_team1: float
    win_probability_team2: float
    ev_estimate: float
    confidence_score: float
    id: Optional[int] = None
    predicted_total: Optional[float] = None
    mispricing_detected: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Pick:
    """Pick model from Picker"""
    bet_type: BetType
    odds: int
    rationale: str
    confidence: float  # 0.0-1.0 probability confidence
    expected_value: float
    book: str
    id: Optional[int] = None
    game_id: int = 0
    line: float = 0.0
    stake_units: float = 0.0
    stake_amount: float = 0.0
    parlay_legs: Optional[List[int]] = None
    selection_text: Optional[str] = None  # Original selection text from Picker (e.g., "Team A +3.5", "Over 160.5")
    team_name: Optional[str] = None  # Team name for spread/moneyline bets (null for totals) - deprecated, use team_id
    team_id: Optional[int] = None  # Team ID for spread/moneyline bets (null for totals) - preferred
    best_bet: bool = False  # True if this is a "best bet" (will be reviewed by President)
    favorite: bool = False  # Deprecated: use best_bet instead. Kept for backwards compatibility
    confidence_score: int = 5  # 1-10 confidence score (1 = low, 10 = high)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Bet:
    """Bet model (placed bet)"""
    pick_id: int
    id: Optional[int] = None
    placed_at: datetime = field(default_factory=datetime.now)
    result: BetResult = BetResult.PENDING
    payout: float = 0.0
    profit_loss: float = 0.0
    settled_at: Optional[datetime] = None


@dataclass
class Bankroll:
    """Bankroll model"""
    balance: float
    id: Optional[int] = None
    date: date = field(default_factory=date.today)
    total_wagered: float = 0.0
    total_profit: float = 0.0
    active_bets: int = 0
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ComplianceResult:
    """Compliance validation result"""
    pick_id: int
    approved: bool
    risk_level: str
    reasons: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RevisionRequest:
    """Request for revision from President"""
    request_type: RevisionRequestType
    target_agent: str
    feedback: str
    id: Optional[int] = None
    original_output_id: Optional[int] = None
    priority: str = "medium"
    created_at: datetime = field(default_factory=datetime.now)
    resolved: bool = False
    resolved_at: Optional[datetime] = None


@dataclass
class CardReview:
    """Card review from President"""
    approved: bool
    date: date = field(default_factory=date.today)
    picks_approved: List[int] = field(default_factory=list)
    picks_rejected: List[int] = field(default_factory=list)
    review_notes: str = ""
    strategic_directives: Dict[str, Any] = field(default_factory=dict)
    revision_requests: List[RevisionRequest] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class DailyReport:
    """Daily performance report from Auditor"""
    total_picks: int
    date: date = field(default_factory=date.today)
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    win_rate: float = 0.0
    total_wagered: float = 0.0
    total_payout: float = 0.0
    profit_loss: float = 0.0
    roi: float = 0.0
    accuracy_metrics: Dict[str, float] = field(default_factory=dict)
    insights: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class AccuracyMetrics:
    """Accuracy metrics from Auditor"""
    total_picks: int
    wins: int
    losses: int
    pushes: int
    win_rate: float
    roi: float
    average_confidence: float
    ev_realized: float
    period_start: date
    period_end: date


@dataclass
class Conflict:
    """Conflict between agents"""
    conflict_type: str
    description: str
    involved_agents: List[str]
    severity: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Resolution:
    """Conflict resolution"""
    resolution: str
    decision: str
    resolved_by: str
    conflict_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
