"""Picker agent for bet selection"""

from typing import List, Optional
import random
import math
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
        self.parlay_enabled = self.config.get('parlay_enabled', True)
        self.parlay_probability = self.config.get('parlay_probability', 0.15)
        self.parlay_min_legs = self.config.get('parlay_min_legs', 2)
        self.parlay_max_legs = self.config.get('parlay_max_legs', 4)
        self.parlay_min_confidence = self.config.get('parlay_min_confidence', 0.65)
    
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
        
        # Save picks first so we have IDs for parlay legs
        for pick in picks:
            self._save_pick(pick)
        
        # Occasionally create a parlay for fun
        if self.parlay_enabled and len(picks) >= self.parlay_min_legs:
            parlay = self._maybe_create_parlay(picks)
            if parlay:
                picks.append(parlay)
                self._save_pick(parlay)  # Save parlay
                self.log_info(f"🎲 Created parlay with {len(parlay.parlay_legs or [])} legs for fun!")
        
        self.log_info(f"Selected {len(picks)} picks (including {sum(1 for p in picks if p.bet_type == BetType.PARLAY)} parlay(s))")
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
    
    def _maybe_create_parlay(self, picks: List[Pick]) -> Optional[Pick]:
        """Occasionally create a parlay from high-confidence picks"""
        # Check probability
        if random.random() > self.parlay_probability:
            return None
        
        # Filter picks that meet parlay criteria
        eligible_picks = [
            p for p in picks
            if p.bet_type != BetType.PARLAY  # Don't parlay parlays
            and p.confidence >= self.parlay_min_confidence
            and p.expected_value > 0
        ]
        
        if len(eligible_picks) < self.parlay_min_legs:
            return None
        
        # Select random number of legs within range
        num_legs = random.randint(
            self.parlay_min_legs,
            min(self.parlay_max_legs, len(eligible_picks))
        )
        
        # Select random picks for parlay (prioritize higher confidence)
        eligible_picks_sorted = sorted(
            eligible_picks,
            key=lambda p: (p.confidence, p.expected_value),
            reverse=True
        )
        parlay_legs = eligible_picks_sorted[:num_legs]
        
        # Calculate parlay odds and EV
        parlay_odds, parlay_ev, parlay_confidence = self._calculate_parlay_metrics(parlay_legs)
        
        # Only create parlay if it has positive EV (even if lower than individual picks)
        if parlay_ev <= 0:
            return None
        
        # Build rationale
        leg_descriptions = [
            f"Leg {i+1}: {self._get_pick_description(leg)}"
            for i, leg in enumerate(parlay_legs)
        ]
        rationale = f"Parlay ({num_legs} legs) for fun! " + " | ".join(leg_descriptions)
        rationale += f" Combined EV: {parlay_ev:.3f}"
        
        # Create parlay pick (use pick IDs that should already be saved)
        parlay_leg_ids = [leg.id if leg.id else 0 for leg in parlay_legs]
        
        parlay = Pick(
            game_id=0,  # Parlays don't have a single game
            bet_type=BetType.PARLAY,
            line=0.0,
            odds=parlay_odds,
            stake_units=0.0,  # Will be set by Banker
            stake_amount=0.0,  # Will be set by Banker
            rationale=rationale,
            confidence=parlay_confidence,
            expected_value=parlay_ev,
            book=parlay_legs[0].book if parlay_legs else "draftkings",
            parlay_legs=parlay_leg_ids if any(parlay_leg_ids) else None
        )
        
        return parlay
    
    def _calculate_parlay_metrics(self, legs: List[Pick]) -> tuple[int, float, float]:
        """Calculate parlay odds, EV, and combined confidence"""
        if not legs:
            return 0, 0.0, 0.0
        
        # Convert each leg's odds to decimal, multiply together, convert back to American
        decimal_multiplier = 1.0
        win_probability = 1.0
        confidence_sum = 0.0
        
        for leg in legs:
            # Convert American odds to decimal
            if leg.odds > 0:
                decimal_odds = (leg.odds / 100) + 1
            else:
                decimal_odds = (100 / abs(leg.odds)) + 1
            
            decimal_multiplier *= decimal_odds
            
            # Estimate win probability from confidence (simplified)
            # Higher confidence = higher win probability
            leg_win_prob = 0.5 + (leg.confidence - 0.5) * 0.4  # Scale confidence to 0.3-0.7 range
            win_probability *= leg_win_prob
            
            confidence_sum += leg.confidence
        
        # Convert back to American odds
        if decimal_multiplier >= 2.0:
            parlay_odds = int((decimal_multiplier - 1) * 100)
        else:
            parlay_odds = int(-100 / (decimal_multiplier - 1))
        
        # Calculate EV (simplified - assumes $1 stake)
        payout = decimal_multiplier
        ev = (win_probability * payout) - (1 - win_probability)
        
        # Combined confidence (geometric mean for independent events)
        combined_confidence = math.pow(
            math.prod([leg.confidence for leg in legs]),
            1.0 / len(legs)
        )
        
        return parlay_odds, ev, combined_confidence
    
    def _get_pick_description(self, pick: Pick) -> str:
        """Get a short description of a pick for parlay rationale"""
        bet_type_str = pick.bet_type.value.upper()
        if pick.bet_type == BetType.SPREAD:
            return f"{bet_type_str} {pick.line:+.1f}"
        elif pick.bet_type == BetType.TOTAL:
            return f"{bet_type_str} {pick.line:.1f}"
        elif pick.bet_type == BetType.MONEYLINE:
            return f"{bet_type_str} {pick.odds:+d}"
        return bet_type_str
    
    def _save_pick(self, pick: Pick) -> None:
        """Save pick to database"""
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            # Convert parlay_legs to JSON if present
            parlay_legs_json = pick.parlay_legs if pick.parlay_legs else None
            
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
                parlay_legs=parlay_legs_json
            )
            session.add(pick_model)
            session.commit()
            pick.id = pick_model.id
        except Exception as e:
            self.log_error(f"Error saving pick: {e}")
            session.rollback()
        finally:
            session.close()

