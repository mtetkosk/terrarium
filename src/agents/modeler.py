"""Modeler agent for predictions and EV calculations"""

from typing import List, Optional
import numpy as np

from src.agents.base import BaseAgent
from src.data.models import GameInsight, Prediction, BettingLine, BetType
from src.data.storage import Database, PredictionModel
from src.models.predictive import SimpleLinearModel
from src.models.kelly import calculate_ev
from src.utils.logging import get_logger

logger = get_logger("agents.modeler")


class Modeler(BaseAgent):
    """Modeler agent for predictive modeling"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Modeler agent"""
        super().__init__("Modeler", db)
        self.model_type = self.config.get('model_type', 'simple_linear')
        self.model = SimpleLinearModel()
    
    def process(
        self,
        insights: List[GameInsight],
        lines: List[BettingLine]
    ) -> List[Prediction]:
        """Generate predictions for games"""
        if not self.is_enabled():
            self.log_warning("Modeler agent is disabled")
            return []
        
        self.log_info(f"Generating predictions for {len(insights)} games")
        predictions = []
        
        # Group lines by game
        lines_by_game = {}
        for line in lines:
            if line.game_id not in lines_by_game:
                lines_by_game[line.game_id] = []
            lines_by_game[line.game_id].append(line)
        
        for insight in insights:
            try:
                game_lines = lines_by_game.get(insight.game_id, [])
                prediction = self.predict_game(insight, game_lines)
                predictions.append(prediction)
                self._save_prediction(prediction)
            except Exception as e:
                self.log_error(f"Error predicting game {insight.game_id}: {e}")
                continue
        
        # Identify mispricing
        mispriced = self.identify_mispricing(predictions, lines)
        self.log_info(f"Identified {len(mispriced)} mispriced lines")
        
        return predictions
    
    def predict_game(
        self,
        insight: GameInsight,
        lines: List[BettingLine]
    ) -> Prediction:
        """Predict a single game"""
        self.log_info(f"Predicting game {insight.game_id}")
        
        # Get team stats
        team1_stats = insight.team1_stats
        team2_stats = insight.team2_stats
        
        # Predict spread
        predicted_spread = self.model.predict_spread(
            team1_stats, team2_stats, insight
        )
        
        # Predict total
        predicted_total = self.model.predict_total(team1_stats, team2_stats)
        
        # Calculate win probabilities
        win_prob_team1 = 1 / (1 + np.exp(-predicted_spread / 5.0))
        win_prob_team1 = max(0.05, min(0.95, win_prob_team1))
        win_prob_team2 = 1 - win_prob_team1
        
        # Calculate EV for best line
        best_ev = 0.0
        best_line = None
        
        for line in lines:
            if line.bet_type == BetType.SPREAD:
                win_prob = self.model.calculate_win_probability(
                    predicted_spread, line.line, line.bet_type
                )
                # Calculate EV (simplified - would use actual stake)
                ev = self.calculate_ev(
                    Prediction(
                        game_id=insight.game_id,
                        model_type=self.model_type,
                        predicted_spread=predicted_spread,
                        predicted_total=predicted_total,
                        win_probability_team1=win_prob_team1,
                        win_probability_team2=win_prob_team2,
                        ev_estimate=0.0,
                        confidence_score=0.0
                    ),
                    line
                )
                
                if ev > best_ev:
                    best_ev = ev
                    best_line = line
        
        # Calculate confidence score
        confidence_score = self.model.calculate_confidence_score(
            insight, predicted_spread
        )
        
        # Check for mispricing
        mispricing_detected = best_ev > 0.05 if best_line else False
        
        prediction = Prediction(
            game_id=insight.game_id,
            model_type=self.model_type,
            predicted_spread=predicted_spread,
            predicted_total=predicted_total,
            win_probability_team1=win_prob_team1,
            win_probability_team2=win_prob_team2,
            ev_estimate=best_ev,
            confidence_score=confidence_score,
            mispricing_detected=mispricing_detected
        )
        
        return prediction
    
    def calculate_ev(
        self,
        prediction: Prediction,
        line: BettingLine
    ) -> float:
        """Calculate expected value for a bet"""
        # Determine win probability based on bet type
        if line.bet_type == BetType.SPREAD:
            win_prob = self.model.calculate_win_probability(
                prediction.predicted_spread, line.line, line.bet_type
            )
        elif line.bet_type == BetType.TOTAL:
            # Would need predicted total vs line
            win_prob = 0.5  # Placeholder
        elif line.bet_type == BetType.MONEYLINE:
            if line.line == 0:  # Team 1 moneyline
                win_prob = prediction.win_probability_team1
            else:
                win_prob = prediction.win_probability_team2
        else:
            win_prob = 0.5
        
        # Calculate EV using $1 stake
        stake = 1.0
        ev = calculate_ev(win_prob, line.odds, stake)
        
        return ev
    
    def identify_mispricing(
        self,
        predictions: List[Prediction],
        lines: List[BettingLine]
    ) -> List[Prediction]:
        """Identify mispriced lines"""
        mispriced = []
        
        for prediction in predictions:
            if prediction.mispricing_detected:
                mispriced.append(prediction)
        
        return mispriced
    
    def _save_prediction(self, prediction: Prediction) -> None:
        """Save prediction to database"""
        if not self.db or prediction.game_id == 0:
            return
        
        session = self.db.get_session()
        try:
            prediction_model = PredictionModel(
                game_id=prediction.game_id,
                model_type=prediction.model_type,
                predicted_spread=prediction.predicted_spread,
                predicted_total=prediction.predicted_total,
                win_probability_team1=prediction.win_probability_team1,
                win_probability_team2=prediction.win_probability_team2,
                ev_estimate=prediction.ev_estimate,
                confidence_score=prediction.confidence_score,
                mispricing_detected=prediction.mispricing_detected
            )
            session.add(prediction_model)
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving prediction: {e}")
            session.rollback()
        finally:
            session.close()

