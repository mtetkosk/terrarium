"""Auditor agent for performance tracking"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta

from src.agents.base import BaseAgent
from src.data.models import (
    Bet, Pick, DailyReport, AccuracyMetrics, BetResult
)
from src.data.storage import Database, BetModel, PickModel, DailyReportModel
from sqlalchemy import func
from src.utils.logging import get_logger

logger = get_logger("agents.auditor")


class Auditor(BaseAgent):
    """Auditor agent for performance tracking"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Auditor agent"""
        super().__init__("Auditor", db)
    
    def process(self, target_date: Optional[date] = None) -> DailyReport:
        """Calculate daily P&L and metrics"""
        if not self.is_enabled():
            self.log_warning("Auditor agent is disabled")
            return DailyReport(
                date=target_date or date.today(),
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0
            )
        
        if target_date is None:
            target_date = date.today()
        
        self.log_info(f"Auditing performance for {target_date}")
        
        report = self.calculate_daily_pl(target_date)
        self._save_report(report)
        
        return report
    
    def calculate_daily_pl(self, target_date: date) -> DailyReport:
        """Calculate daily P&L"""
        if not self.db:
            return DailyReport(
                date=target_date,
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0
            )
        
        session = self.db.get_session()
        try:
            # Get picks for the day
            picks = session.query(PickModel).filter(
                func.date(PickModel.created_at) == target_date
            ).all()
            
            total_picks = len(picks)
            wins = 0
            losses = 0
            pushes = 0
            total_wagered = 0.0
            total_payout = 0.0
            
            for pick_model in picks:
                total_wagered += pick_model.stake_amount
                
                # Get bet result if exists
                bet = session.query(BetModel).filter_by(pick_id=pick_model.id).first()
                if bet:
                    if bet.result == BetResult.WIN:
                        wins += 1
                        total_payout += bet.payout
                    elif bet.result == BetResult.LOSS:
                        losses += 1
                    elif bet.result == BetResult.PUSH:
                        pushes += 1
                        total_payout += pick_model.stake_amount  # Return stake
            
            profit_loss = total_payout - total_wagered
            win_rate = wins / total_picks if total_picks > 0 else 0.0
            roi = (profit_loss / total_wagered * 100) if total_wagered > 0 else 0.0
            
            # Calculate additional metrics
            accuracy_metrics = self._calculate_accuracy_metrics(picks, session)
            
            report = DailyReport(
                date=target_date,
                total_picks=total_picks,
                wins=wins,
                losses=losses,
                pushes=pushes,
                win_rate=win_rate,
                total_wagered=total_wagered,
                total_payout=total_payout,
                profit_loss=profit_loss,
                roi=roi,
                accuracy_metrics=accuracy_metrics
            )
            
            return report
            
        except Exception as e:
            self.log_error(f"Error calculating daily P&L: {e}")
            return DailyReport(
                date=target_date,
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0
            )
        finally:
            session.close()
    
    def track_accuracy(
        self,
        picks: List[Pick],
        results: List[Bet],
        period_start: date,
        period_end: date
    ) -> AccuracyMetrics:
        """Track accuracy metrics over a period"""
        if not picks or not results:
            return AccuracyMetrics(
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0,
                win_rate=0.0,
                roi=0.0,
                average_confidence=0.0,
                ev_realized=0.0,
                period_start=period_start,
                period_end=period_end
            )
        
        # Match picks with results
        results_by_pick = {bet.pick_id: bet for bet in results}
        
        wins = 0
        losses = 0
        pushes = 0
        total_wagered = 0.0
        total_payout = 0.0
        total_confidence = 0.0
        total_ev = 0.0
        
        for pick in picks:
            total_wagered += pick.stake_amount
            total_confidence += pick.confidence
            total_ev += pick.expected_value
            
            bet = results_by_pick.get(pick.id)
            if bet:
                if bet.result == BetResult.WIN:
                    wins += 1
                    total_payout += bet.payout
                elif bet.result == BetResult.LOSS:
                    losses += 1
                elif bet.result == BetResult.PUSH:
                    pushes += 1
                    total_payout += pick.stake_amount
        
        win_rate = wins / len(picks) if picks else 0.0
        profit_loss = total_payout - total_wagered
        roi = (profit_loss / total_wagered * 100) if total_wagered > 0 else 0.0
        avg_confidence = total_confidence / len(picks) if picks else 0.0
        ev_realized = profit_loss / len(picks) if picks else 0.0
        
        return AccuracyMetrics(
            total_picks=len(picks),
            wins=wins,
            losses=losses,
            pushes=pushes,
            win_rate=win_rate,
            roi=roi,
            average_confidence=avg_confidence,
            ev_realized=ev_realized,
            period_start=period_start,
            period_end=period_end
        )
    
    def detect_model_drift(
        self,
        predictions: List,
        results: List[Bet]
    ) -> Dict[str, Any]:
        """Detect model drift"""
        # Placeholder for model drift detection
        # Would compare predicted vs actual outcomes
        return {
            "drift_detected": False,
            "drift_magnitude": 0.0,
            "notes": "Model drift detection not yet implemented"
        }
    
    def generate_feedback(self, metrics: AccuracyMetrics) -> Dict[str, Any]:
        """Generate feedback for other agents"""
        feedback = {
            "overall_performance": "good" if metrics.roi > 0 else "poor",
            "recommendations": []
        }
        
        if metrics.win_rate < 0.5:
            feedback["recommendations"].append(
                "Win rate below 50%. Consider raising EV threshold."
            )
        
        if metrics.roi < -5.0:
            feedback["recommendations"].append(
                "Negative ROI detected. Review model accuracy."
            )
        
        if metrics.average_confidence > 0.8 and metrics.win_rate < 0.55:
            feedback["recommendations"].append(
                "Overconfidence detected. Model may be overfitting."
            )
        
        if not feedback["recommendations"]:
            feedback["recommendations"].append("No immediate concerns.")
        
        return feedback
    
    def _calculate_accuracy_metrics(
        self,
        picks: List[PickModel],
        session
    ) -> Dict[str, float]:
        """Calculate additional accuracy metrics"""
        metrics = {}
        
        if not picks:
            return metrics
        
        # Average confidence
        avg_confidence = sum(p.confidence for p in picks) / len(picks)
        metrics['average_confidence'] = avg_confidence
        
        # Average EV
        avg_ev = sum(p.expected_value for p in picks) / len(picks)
        metrics['average_ev'] = avg_ev
        
        # Calculate realized vs expected
        total_expected_ev = sum(p.expected_value * p.stake_amount for p in picks)
        total_realized = 0.0
        
        for pick in picks:
            bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
            if bet and bet.result == BetResult.WIN:
                total_realized += bet.profit_loss
        
        if total_expected_ev > 0:
            metrics['ev_efficiency'] = total_realized / total_expected_ev
        else:
            metrics['ev_efficiency'] = 0.0
        
        return metrics
    
    def _save_report(self, report: DailyReport) -> None:
        """Save daily report to database"""
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            # Check if report exists
            existing = session.query(DailyReportModel).filter_by(
                date=report.date
            ).first()
            
            if existing:
                # Update existing
                existing.total_picks = report.total_picks
                existing.wins = report.wins
                existing.losses = report.losses
                existing.pushes = report.pushes
                existing.win_rate = report.win_rate
                existing.total_wagered = report.total_wagered
                existing.total_payout = report.total_payout
                existing.profit_loss = report.profit_loss
                existing.roi = report.roi
                existing.accuracy_metrics = report.accuracy_metrics
            else:
                # Create new
                report_model = DailyReportModel(
                    date=report.date,
                    total_picks=report.total_picks,
                    wins=report.wins,
                    losses=report.losses,
                    pushes=report.pushes,
                    win_rate=report.win_rate,
                    total_wagered=report.total_wagered,
                    total_payout=report.total_payout,
                    profit_loss=report.profit_loss,
                    roi=report.roi,
                    accuracy_metrics=report.accuracy_metrics
                )
                session.add(report_model)
            
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving daily report: {e}")
            session.rollback()
        finally:
            session.close()

