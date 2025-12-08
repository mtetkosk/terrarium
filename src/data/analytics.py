"""Analytics service wrapper around Database query helpers."""

from datetime import date
from typing import Any, Dict, List, Optional

from src.data.storage import (
    Database,
    BettingLineModel,
    BetModel,
    GameModel,
    PickModel,
)


class AnalyticsService:
    """Thin wrapper to maintain compatibility with existing tests."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def get_picks_for_date(self, target_date: date) -> List[PickModel]:
        return self.db.get_picks_for_date(target_date)

    def get_results_for_date(self, target_date: date) -> Dict[str, Any]:
        return self.db.get_results_for_date(target_date)

    def get_betting_lines_for_date(self, target_date: date) -> List[BettingLineModel]:
        return self.db.get_betting_lines_for_date(target_date)

    # These attributes are referenced by tests; expose models for convenience
    BetModel = BetModel
    PickModel = PickModel
    GameModel = GameModel
    BettingLineModel = BettingLineModel


__all__ = ["AnalyticsService"]

