"""Picker agent for bet selection"""

from typing import List, Optional
from src.agents.base import BaseAgent
from src.data.models import Prediction, GameInsight, Pick, BettingLine, BetType
from src.data.storage import Database, PickModel
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.picker")


class Picker(BaseAgent):
    """Picker agent for selecting bets"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Picker agent"""
        super().__init__("Picker", db)
        self.max_picks = self.config.get('max_picks_per_day', 10)
        self.min_ev = config.get_betting_config().get('min_ev', 0.05)
    
    def process(
        self,
        predictions: List[Prediction],
        insights: List[GameInsight],
        lines: List[BettingLine]
    ) -> List[Pick]:
        """Select picks from predictions"""
        if not self.is_enabled():
            self.log_warning("Picker agent is disabled")
            return []
        
        self.log_info(f"Selecting picks from {len(predictions)} predictions")
        
        # Create a mapping of game_id to insight
        insights_by_game = {insight.game_id: insight for insight in insights}
        
        # Group lines by game
        lines_by_game = {}
        for line in lines:
            if line.game_id not in lines_by_game:
                lines_by_game[line.game_id] = []
            lines_by_game[line.game_id].append(line)
        
        # Select picks
        picks = self.select_picks(predictions, insights_by_game, lines_by_game)
        
        # Check for contradictions
        picks = self.check_contradictions(picks)
        
        # Check for correlation
        picks = self.check_correlation(picks)
        
        # Limit to max picks
        if len(picks) > self.max_picks:
            picks = sorted(picks, key=lambda p: p.expected_value, reverse=True)[:self.max_picks]
        
        self.log_info(f"Selected {len(picks)} picks")
        return picks
    
    def select_picks(
        self,
        predictions: List[Prediction],
        insights_by_game: dict,
        lines_by_game: dict
    ) -> List[Pick]:
        """Select highest-EV picks"""
        picks = []
        
        for prediction in predictions:
            if prediction.ev_estimate < self.min_ev:
                continue
            
            game_lines = lines_by_game.get(prediction.game_id, [])
            insight = insights_by_game.get(prediction.game_id)
            
            # Find best line for this game
            best_line = None
            best_ev = 0.0
            
            for line in game_lines:
                # Calculate EV for this line
                ev = self._calculate_line_ev(prediction, line)
                
                if ev > best_ev and ev >= self.min_ev:
                    best_ev = ev
                    best_line = line
            
            if best_line and best_ev >= self.min_ev:
                pick = self._create_pick(
                    prediction, insight, best_line, best_ev
                )
                picks.append(pick)
        
        return picks
    
    def _calculate_line_ev(
        self,
        prediction: Prediction,
        line: BettingLine
    ) -> float:
        """Calculate EV for a specific line"""
        from src.models.predictive import SimpleLinearModel
        model = SimpleLinearModel()
        
        # Calculate win probability
        if line.bet_type == BetType.SPREAD:
            win_prob = model.calculate_win_probability(
                prediction.predicted_spread, line.line, line.bet_type
            )
        elif line.bet_type == BetType.TOTAL:
            # Would need predicted total
            win_prob = 0.5  # Placeholder
        elif line.bet_type == BetType.MONEYLINE:
            if line.line == 0:  # Team 1
                win_prob = prediction.win_probability_team1
            else:
                win_prob = prediction.win_probability_team2
        else:
            win_prob = 0.5
        
        # Calculate EV
        from src.models.kelly import calculate_ev
        ev = calculate_ev(win_prob, line.odds, 1.0)
        
        return ev
    
    def _create_pick(
        self,
        prediction: Prediction,
        insight: Optional[GameInsight],
        line: BettingLine,
        ev: float
    ) -> Pick:
        """Create a pick from prediction and line"""
        # Build rationale
        rationale = self._build_rationale(prediction, insight, line, ev)
        
        # Calculate confidence (combine model confidence with EV)
        confidence = (prediction.confidence_score + min(ev / 0.2, 1.0)) / 2
        confidence = min(confidence, 0.95)  # Cap at 95%
        
        pick = Pick(
            game_id=prediction.game_id,
            bet_type=line.bet_type,
            line=line.line,
            odds=line.odds,
            stake_units=0.0,  # Will be set by Banker
            stake_amount=0.0,  # Will be set by Banker
            rationale=rationale,
            confidence=confidence,
            expected_value=ev,
            book=line.book
        )
        
        return pick
    
    def _build_rationale(
        self,
        prediction: Prediction,
        insight: Optional[GameInsight],
        line: BettingLine,
        ev: float
    ) -> str:
        """Build rationale for pick"""
        rationale_parts = []
        
        # Model prediction
        if line.bet_type == BetType.SPREAD:
            rationale_parts.append(
                f"Model predicts {prediction.predicted_spread:.1f} point spread, "
                f"line is {line.line:.1f}"
            )
        elif line.bet_type == BetType.TOTAL:
            if prediction.predicted_total:
                rationale_parts.append(
                    f"Model predicts {prediction.predicted_total:.1f} total, "
                    f"line is {line.line:.1f}"
                )
        elif line.bet_type == BetType.MONEYLINE:
            prob = prediction.win_probability_team1 if line.line == 0 else prediction.win_probability_team2
            rationale_parts.append(
                f"Model gives {prob*100:.1f}% win probability"
            )
        
        # EV
        rationale_parts.append(f"Expected value: {ev:.3f}")
        
        # Insight notes
        if insight and insight.matchup_notes:
            rationale_parts.append(f"Context: {insight.matchup_notes[:100]}")
        
        # Mispricing
        if prediction.mispricing_detected:
            rationale_parts.append("Mispricing detected")
        
        return ". ".join(rationale_parts)
    
    def check_contradictions(self, picks: List[Pick]) -> List[Pick]:
        """Check for contradictory bets (same game, opposite sides)"""
        picks_by_game = {}
        
        for pick in picks:
            if pick.game_id not in picks_by_game:
                picks_by_game[pick.game_id] = []
            picks_by_game[pick.game_id].append(pick)
        
        filtered_picks = []
        
        for game_id, game_picks in picks_by_game.items():
            if len(game_picks) == 1:
                filtered_picks.append(game_picks[0])
            else:
                # Multiple picks for same game - keep highest EV
                best_pick = max(game_picks, key=lambda p: p.expected_value)
                filtered_picks.append(best_pick)
                self.log_warning(
                    f"Removed {len(game_picks)-1} contradictory picks for game {game_id}"
                )
        
        return filtered_picks
    
    def check_correlation(self, picks: List[Pick]) -> List[Pick]:
        """Check for correlated bets and reduce exposure"""
        # Simple implementation: limit picks per game
        # More sophisticated: check for correlated outcomes
        
        picks_by_game = {}
        for pick in picks:
            if pick.game_id not in picks_by_game:
                picks_by_game[pick.game_id] = []
            picks_by_game[pick.game_id].append(pick)
        
        filtered_picks = []
        for game_id, game_picks in picks_by_game.items():
            # Limit to 2 picks per game
            if len(game_picks) > 2:
                game_picks = sorted(game_picks, key=lambda p: p.expected_value, reverse=True)[:2]
                self.log_warning(f"Limited to 2 picks for game {game_id} due to correlation")
            filtered_picks.extend(game_picks)
        
        return filtered_picks
    
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
            self.log_error(f"Error saving pick: {e}")
            session.rollback()
        finally:
            session.close()

