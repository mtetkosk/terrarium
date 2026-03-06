"""Auditor agent for performance tracking"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta

from src.agents.base import BaseAgent
from src.data.models import (
    Bet, Pick, DailyReport, AccuracyMetrics, BetResult, BetType
)
from src.data.storage import Database, BetModel, PickModel, DailyReportModel, GameModel
from src.prompts import AUDITOR_PROMPT, build_auditor_user_prompt
from src.utils.json_schemas import get_auditor_schema
from sqlalchemy import func
from src.utils.logging import get_logger
from collections import defaultdict

logger = get_logger("agents.auditor")


class Auditor(BaseAgent):
    """Auditor agent for performance tracking"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Auditor agent"""
        super().__init__("Auditor", db, llm_client)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Auditor"""
        return AUDITOR_PROMPT
    
    def process(self, target_date: Optional[date] = None) -> DailyReport:
        """Review previous day's results and generate comprehensive report"""
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
        
        self.interaction_logger.log_agent_start(
            "Auditor",
            f"Reviewing results for {target_date}"
        )
        
        # Review previous day (yesterday's bets that settled today)
        review_date = target_date - timedelta(days=1)
        self.log_info(f"Auditing performance for {target_date} (reviewing bets from {review_date})")
        
        report = self.review_daily_results(target_date, review_date)
        self._save_report(report)
        
        self.interaction_logger.log_agent_complete(
            "Auditor",
            f"Generated report with {len(report.recommendations)} recommendations"
        )
        
        return report
    
    def review_daily_results(self, report_date: date, review_date: date) -> DailyReport:
        """
        Review previous day's results and generate comprehensive insights.
        
        CRITICAL: Uses analytics service which ensures only latest pick per game_id.
        No manual duplicate handling needed - database constraint enforces uniqueness.
        """
        if not self.db:
            return DailyReport(
                date=report_date,
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0
            )
        
        try:
            # Get results from database
            results = self.db.get_results_for_date(review_date)
            
            picks = results.get('picks', [])
            bet_map = results.get('bet_map', {})
            
            if not picks:
                self.log_info(f"No picks found for {review_date}")
                return DailyReport(
                    date=report_date,
                    total_picks=0,
                    wins=0,
                    losses=0,
                    pushes=0,
                    insights={"note": f"No bets placed on {review_date}"},
                    recommendations=["No action needed - no bets to review"]
                )
            
            # Calculate basic metrics
            total_picks = len(picks)
            wins = 0
            losses = 0
            pushes = 0
            total_wagered = 0.0
            total_payout = 0.0
            
            # Detailed analysis
            winning_picks = []
            losing_picks = []
            parlay_results = []
            bet_type_performance = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pushes': 0, 'wagered': 0.0, 'payout': 0.0})
            confidence_analysis = {'high': {'wins': 0, 'losses': 0}, 'medium': {'wins': 0, 'losses': 0}, 'low': {'wins': 0, 'losses': 0}}
            ev_analysis = {'winners': [], 'losers': []}
            
            session = self.db.get_session()
            try:
                for pick_model in picks:
                    total_wagered += pick_model.stake_amount
                    bet = bet_map.get(pick_model.id)
                    
                    # Track bet type performance
                    bet_type = pick_model.bet_type.value
                    bet_type_performance[bet_type]['wagered'] += pick_model.stake_amount
                    
                    if bet:
                        if bet.result == BetResult.WIN:
                            wins += 1
                            total_payout += bet.payout
                            bet_type_performance[bet_type]['wins'] += 1
                            bet_type_performance[bet_type]['payout'] += bet.payout
                            winning_picks.append({
                                'pick': pick_model,
                                'bet': bet,
                                'profit': bet.profit_loss
                            })
                            ev_analysis['winners'].append({
                                'expected': pick_model.expected_value,
                                'realized': bet.profit_loss / pick_model.stake_amount if pick_model.stake_amount > 0 else 0
                            })
                        elif bet.result == BetResult.LOSS:
                            losses += 1
                            bet_type_performance[bet_type]['losses'] += 1
                            losing_picks.append({
                                'pick': pick_model,
                                'bet': bet,
                                'loss': abs(bet.profit_loss)
                            })
                            ev_analysis['losers'].append({
                                'expected': pick_model.expected_value,
                                'realized': bet.profit_loss / pick_model.stake_amount if pick_model.stake_amount > 0 else 0
                            })
                        elif bet.result == BetResult.PUSH:
                            pushes += 1
                            total_payout += pick_model.stake_amount
                            bet_type_performance[bet_type]['pushes'] += 1
                            bet_type_performance[bet_type]['payout'] += pick_model.stake_amount
                    
                    # Confidence analysis
                    # HIGH: >= 0.6 (60% or 6/10), MEDIUM: >= 0.4 (40% or 4/10), LOW: < 0.4
                    if pick_model.confidence >= 0.6:
                        conf_level = 'high'
                    elif pick_model.confidence >= 0.4:
                        conf_level = 'medium'
                    else:
                        conf_level = 'low'
                    
                    if bet:
                        if bet.result == BetResult.WIN:
                            confidence_analysis[conf_level]['wins'] += 1
                        elif bet.result == BetResult.LOSS:
                            confidence_analysis[conf_level]['losses'] += 1
                    
                    # Track parlay results
                    if pick_model.bet_type == BetType.PARLAY and bet:
                        parlay_results.append({
                            'legs': len(pick_model.parlay_legs) if pick_model.parlay_legs else 0,
                            'result': bet.result.value,
                            'profit': bet.profit_loss
                        })
                
                # Calculate additional metrics
                accuracy_metrics = self._calculate_accuracy_metrics(picks, session)
            finally:
                session.close()
            
            profit_loss = total_payout - total_wagered
            win_rate = wins / total_picks if total_picks > 0 else 0.0
            roi = (profit_loss / total_wagered * 100) if total_wagered > 0 else 0.0
            
            # Build metrics for LLM (no ORM objects)
            metrics = {
                "report_date": report_date.isoformat(),
                "review_date": review_date.isoformat(),
                "total_picks": total_picks,
                "wins": wins,
                "losses": losses,
                "pushes": pushes,
                "win_rate": round(win_rate, 4),
                "total_wagered": round(total_wagered, 2),
                "total_payout": round(total_payout, 2),
                "profit_loss": round(profit_loss, 2),
                "roi": round(roi, 2),
                "accuracy_metrics": accuracy_metrics,
                "bet_type_performance": dict(bet_type_performance),
                "confidence_analysis": confidence_analysis,
                "winning_picks_count": len(winning_picks),
                "losing_picks_count": len(losing_picks),
                "parlay_results": parlay_results,
                "ev_analysis": {
                    "winners_count": len(ev_analysis["winners"]),
                    "losers_count": len(ev_analysis["losers"]),
                },
            }
            
            insights, recommendations = self._get_insights_and_recommendations(metrics)
            
            report = DailyReport(
                date=report_date,
                total_picks=total_picks,
                wins=wins,
                losses=losses,
                pushes=pushes,
                win_rate=win_rate,
                total_wagered=total_wagered,
                total_payout=total_payout,
                profit_loss=profit_loss,
                roi=roi,
                accuracy_metrics=accuracy_metrics,
                insights=insights,
                recommendations=recommendations
            )
            
            return report
            
        except Exception as e:
            self.log_error(f"Error reviewing daily results: {e}", exc_info=True)
            return DailyReport(
                date=report_date,
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0,
                insights={"error": str(e)},
                recommendations=["Error occurred during review"]
            )
    
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
        
        # Extract pick data upfront to avoid detached instance errors
        # Access all attributes while picks are still bound to session
        picks_data = []
        for pick in picks:
            try:
                pick_data = {
                    'id': pick.id,
                    'confidence': pick.confidence,
                    'expected_value': pick.expected_value,
                    'stake_amount': pick.stake_amount or 0.0
                }
                picks_data.append(pick_data)
            except Exception as e:
                # If pick is detached, log and skip
                self.log_error(f"Error accessing pick attributes in _calculate_accuracy_metrics: {e}")
                continue
        
        if not picks_data:
            return metrics
        
        # Average confidence
        avg_confidence = sum(p['confidence'] for p in picks_data) / len(picks_data)
        metrics['average_confidence'] = avg_confidence
        
        # Average EV
        avg_ev = sum(p['expected_value'] for p in picks_data) / len(picks_data)
        metrics['average_ev'] = avg_ev
        
        # Calculate realized vs expected
        total_expected_ev = sum(p['expected_value'] * p['stake_amount'] for p in picks_data)
        total_realized = 0.0
        
        for pick_data in picks_data:
            bet = session.query(BetModel).filter_by(pick_id=pick_data['id']).first()
            if bet and bet.result == BetResult.WIN:
                total_realized += bet.profit_loss
        
        if total_expected_ev > 0:
            metrics['ev_efficiency'] = total_realized / total_expected_ev
        else:
            metrics['ev_efficiency'] = 0.0
        
        return metrics
    
    def _get_insights_and_recommendations(self, metrics: Dict[str, Any]) -> tuple:
        """Call LLM to generate insights and recommendations; fallback to minimal output on failure."""
        fallback_insights = {
            "what_went_well": [],
            "what_needs_improvement": ["LLM analysis unavailable. Review metrics manually."],
            "key_findings": {},
        }
        fallback_recommendations = ["LLM analysis unavailable. Review metrics manually."]
        
        if not self.system_prompt:
            return fallback_insights, fallback_recommendations
        
        try:
            user_prompt = build_auditor_user_prompt(metrics)
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=None,
                temperature=0.4,
                parse_json=True,
                response_format=get_auditor_schema(),
            )
            insights = response.get("insights") or fallback_insights
            recommendations = response.get("recommendations")
            if not isinstance(recommendations, list):
                recommendations = fallback_recommendations
            if not isinstance(insights, dict):
                insights = fallback_insights
            else:
                insights.setdefault("what_went_well", [])
                insights.setdefault("what_needs_improvement", [])
                insights.setdefault("key_findings", {})
            return insights, recommendations
        except Exception as e:
            self.log_error(f"Auditor LLM call failed: {e}", exc_info=True)
            return fallback_insights, fallback_recommendations
    
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
                existing.insights = report.insights
                existing.recommendations = report.recommendations
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
                    accuracy_metrics=report.accuracy_metrics,
                    insights=report.insights,
                    recommendations=report.recommendations
                )
                session.add(report_model)
            
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving daily report: {e}")
            session.rollback()
        finally:
            session.close()

