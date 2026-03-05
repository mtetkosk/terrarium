"""Service for persisting predictions to the database"""

from datetime import date, datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from src.data.storage import Database, PredictionModel
from src.utils.logging import get_logger
from src.utils.prediction_adjustments import apply_all_adjustments, log_adjustments

logger = get_logger("orchestration.prediction_persistence")


class PredictionPersistenceService:
    """Service for saving predictions from modeler output to database"""
    
    def __init__(self, db: Database):
        """Initialize prediction persistence service"""
        self.db = db
    
    def save_predictions(
        self,
        predictions: Dict[str, Any],
        target_date: date,
        session: Optional[Session] = None
    ) -> int:
        """
        Save predictions from modeler output to database
        
        Args:
            predictions: Modeler output dictionary with 'game_models' key
            target_date: Date for the predictions
            session: Optional database session (creates new if not provided)
            
        Returns:
            Number of predictions saved
        """
        should_close = False
        if session is None:
            session = self.db.get_session()
            should_close = True
        
        try:
            game_models = predictions.get("game_models", [])
            saved_count = 0
            
            for game_model in game_models:
                try:
                    self._save_single_prediction(game_model, target_date, session)
                    saved_count += 1
                except Exception as e:
                    logger.error(f"Error saving prediction for game_model: {e}", exc_info=True)
                    continue
            
            if should_close:
                session.commit()
                logger.info(f"Saved {saved_count} PredictionModel records")
            
            return saved_count
        except Exception as e:
            if should_close:
                session.rollback()
            logger.error(f"Error saving predictions: {e}", exc_info=True)
            raise
        finally:
            if should_close:
                session.close()
    
    def _save_single_prediction(
        self,
        game_model: Dict[str, Any],
        target_date: date,
        session: Session
    ) -> None:
        """Save a single prediction to database"""
        game_id_str = game_model.get("game_id")
        if not game_id_str:
            return
        
        try:
            game_id = int(game_id_str)
        except (ValueError, TypeError):
            logger.warning(f"Invalid game_id in game_model: {game_id_str}")
            return
        
        # Extract prediction data from modeler output
        predicted_score = game_model.get("predicted_score", {})
        away_score = predicted_score.get("away_score")
        home_score = predicted_score.get("home_score")
        
        # Also check legacy structure (predictions.scores)
        pred_data = game_model.get("predictions", {})
        if away_score is None or home_score is None:
            scores = pred_data.get("scores", {})
            if away_score is None:
                away_score = scores.get("away")
            if home_score is None:
                home_score = scores.get("home")
        
        # Extract margin - try multiple locations
        margin = pred_data.get("margin")
        if margin is None:
            spread_data = pred_data.get("spread", {})
            margin = spread_data.get("projected_margin")
            if margin is None:
                margin = spread_data.get("margin")
        
        # Extract total - try multiple locations
        total = pred_data.get("total")
        if total is None:
            total_data = pred_data.get("total", {})
            if isinstance(total_data, dict):
                total = total_data.get("projected_total")
            elif isinstance(total_data, (int, float)):
                total = total_data
        
        # Extract win probabilities - try multiple locations
        win_probs = pred_data.get("win_probs", {})
        if not win_probs:
            moneyline_data = pred_data.get("moneyline", {})
            team_probs = moneyline_data.get("team_probabilities", {})
            if team_probs:
                away_p = team_probs.get("away")
                home_p = team_probs.get("home")
                if away_p is None or home_p is None:
                    logger.warning(
                        f"Missing win probability for game_id={game_id} (away={away_p}, home={home_p}). "
                        "Defaulting to 0.5 may skew EV."
                    )
                win_probs = {"away": away_p if away_p is not None else 0.5, "home": home_p if home_p is not None else 0.5}
        
        # Extract confidence - try multiple locations
        # Check all possible locations where confidence might be stored
        confidence = None
        
        # 1. Check top-level confidence
        if "confidence" in pred_data:
            confidence = pred_data.get("confidence")
        
        # 2. Check spread.model_confidence
        if confidence is None:
            spread_data = pred_data.get("spread", {})
            if isinstance(spread_data, dict) and spread_data.get("model_confidence") is not None:
                confidence = spread_data.get("model_confidence")
        
        # 3. Check total.model_confidence
        if confidence is None:
            total_data = pred_data.get("total", {})
            if isinstance(total_data, dict) and total_data.get("model_confidence") is not None:
                confidence = total_data.get("model_confidence")
        
        # 4. Check moneyline.model_confidence
        if confidence is None:
            moneyline_data = pred_data.get("moneyline", {})
            if isinstance(moneyline_data, dict) and moneyline_data.get("model_confidence") is not None:
                confidence = moneyline_data.get("model_confidence")
        
        # 5. CRITICAL: Confidence is required - do not default
        if confidence is None:
            logger.error(
                f"CRITICAL: Confidence not found in any expected location for game_id={game_id}. "
                f"Modeler output must include confidence. Expected locations: "
                f"predictions.confidence, predictions.spread.model_confidence, "
                f"predictions.total.model_confidence, or predictions.moneyline.model_confidence. "
                f"Skipping save for this prediction."
            )
            return  # Skip saving this prediction - confidence is required
        
        # Handle total: it can be a float or a dict with 'projected_total' key
        if total is not None:
            if isinstance(total, dict):
                total = total.get("projected_total")
            elif not isinstance(total, (int, float)):
                total = None
        
        # Calculate margin and total from scores if not directly provided
        if margin is None and away_score is not None and home_score is not None:
            margin = home_score - away_score
        if total is None and away_score is not None and home_score is not None:
            total = away_score + home_score
        
        # Get win probabilities
        win_prob_team1 = win_probs.get("home")
        win_prob_team2 = win_probs.get("away")
        if win_prob_team1 is None or win_prob_team2 is None:
            logger.warning(
                f"Missing win_prob for game_id={game_id} (home={win_prob_team1}, away={win_prob_team2}). "
                "Defaulting to 0.5 may skew EV."
            )
        win_prob_team1 = win_prob_team1 if win_prob_team1 is not None else 0.5
        win_prob_team2 = win_prob_team2 if win_prob_team2 is not None else 0.5
        
        # Extract ev_estimate from modeler output (required field)
        ev_estimate = game_model.get("ev_estimate")
        if ev_estimate is None:
            logger.warning(
                f"Missing ev_estimate for game_id={game_id}. "
                f"Modeler should provide this. Defaulting to 0.0."
            )
            ev_estimate = 0.0
        ev_estimate = float(ev_estimate)
        
        # Validate margin is available (required field)
        if margin is None:
            logger.warning(
                f"Missing predicted margin for game_id={game_id}. "
                f"This may indicate a model failure or data quality issue."
            )
            margin = 0.0  # Default only for data quality issues
        
        # Extract market_total from meta for blending (Strategy 1)
        meta = game_model.get("meta", {})
        market_total = meta.get("market_total_used")
        
        # Apply backtest-validated adjustments (Strategies 1, 2, 3)
        # These were validated on 1,311 games from Jan-Feb 2026
        # Combined improvements: Spread MAE +0.9%, Total MAE +3.5%
        original_margin = margin
        original_total = total
        
        margin, total = apply_all_adjustments(
            predicted_spread=margin,
            predicted_total=total,
            market_total=market_total,
            apply_spread_adjustment=True,  # Strategy 2: Shrinkage + blowout dampening
            apply_total_blend=True,        # Strategy 1: Blend with market
            apply_total_clamp=True         # Strategy 3: Clamp to [135, 170]
        )
        
        # Log significant adjustments for monitoring
        log_adjustments(
            game_id=game_id,
            original_spread=original_margin,
            adjusted_spread=margin,
            original_total=original_total,
            adjusted_total=total,
            market_total=market_total
        )
        
        # Check if prediction already exists (upsert pattern)
        existing = session.query(PredictionModel).filter_by(
            game_id=game_id,
            prediction_date=target_date
        ).first()
        
        if existing:
            # Update existing record
            existing.model_type = "modeler"
            existing.predicted_spread = float(margin)
            existing.predicted_total = float(total) if total is not None else None
            existing.win_probability_team1 = float(win_prob_team1)
            existing.win_probability_team2 = float(win_prob_team2)
            existing.ev_estimate = ev_estimate
            existing.confidence_score = float(confidence)
            existing.mispricing_detected = False
        else:
            # Create new record
            prediction_model = PredictionModel(
                game_id=game_id,
                prediction_date=target_date,
                model_type="modeler",
                predicted_spread=float(margin),
                predicted_total=float(total) if total is not None else None,
                win_probability_team1=float(win_prob_team1),
                win_probability_team2=float(win_prob_team2),
                ev_estimate=ev_estimate,
                confidence_score=float(confidence),
                mispricing_detected=False,
                created_at=datetime.now()
            )
            session.add(prediction_model)
        
        # Predictions are now stored directly in PredictionModel - no need for separate analytics table

