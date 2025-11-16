"""Auditor agent for performance tracking"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta

from src.agents.base import BaseAgent
from src.data.models import (
    Bet, Pick, DailyReport, AccuracyMetrics, BetResult, BetType
)
from src.data.storage import Database, BetModel, PickModel, DailyReportModel, GameModel
from sqlalchemy import func
from src.utils.logging import get_logger
from collections import defaultdict

logger = get_logger("agents.auditor")


class Auditor(BaseAgent):
    """Auditor agent for performance tracking"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Auditor agent"""
        super().__init__("Auditor", db)
    
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
        """Review previous day's results and generate comprehensive insights"""
        if not self.db:
            return DailyReport(
                date=report_date,
                total_picks=0,
                wins=0,
                losses=0,
                pushes=0
            )
        
        session = self.db.get_session()
        try:
            # Get picks from the review date (yesterday's bets)
            picks = session.query(PickModel).filter(
                func.date(PickModel.created_at) == review_date
            ).all()
            
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
            
            for pick_model in picks:
                total_wagered += pick_model.stake_amount
                bet = session.query(BetModel).filter_by(pick_id=pick_model.id).first()
                
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
                if pick_model.confidence >= 0.7:
                    conf_level = 'high'
                elif pick_model.confidence >= 0.5:
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
            
            profit_loss = total_payout - total_wagered
            win_rate = wins / total_picks if total_picks > 0 else 0.0
            roi = (profit_loss / total_wagered * 100) if total_wagered > 0 else 0.0
            
            # Calculate additional metrics
            accuracy_metrics = self._calculate_accuracy_metrics(picks, session)
            
            # Generate insights
            insights = self._generate_insights(
                winning_picks, losing_picks, parlay_results,
                bet_type_performance, confidence_analysis, ev_analysis,
                win_rate, roi, profit_loss
            )
            
            # Generate recommendations
            recommendations = self._generate_recommendations(
                insights, win_rate, roi, profit_loss, accuracy_metrics,
                bet_type_performance, confidence_analysis
            )
            
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
        finally:
            session.close()
    
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
    
    def _generate_insights(
        self,
        winning_picks: List[Dict],
        losing_picks: List[Dict],
        parlay_results: List[Dict],
        bet_type_performance: Dict,
        confidence_analysis: Dict,
        ev_analysis: Dict,
        win_rate: float,
        roi: float,
        profit_loss: float
    ) -> Dict[str, Any]:
        """Generate insights about what went well and what needs improvement"""
        insights = {
            'what_went_well': [],
            'what_needs_improvement': [],
            'key_findings': {}
        }
        
        # What went well
        if profit_loss > 0:
            insights['what_went_well'].append(f"Profitable day: +${profit_loss:.2f} ({roi:.1f}% ROI)")
        
        if win_rate >= 0.55:
            insights['what_went_well'].append(f"Strong win rate: {win_rate:.1%}")
        
        # Analyze winning picks
        if winning_picks:
            avg_win_profit = sum(w['profit'] for w in winning_picks) / len(winning_picks)
            best_win = max(winning_picks, key=lambda x: x['profit'])
            insights['what_went_well'].append(
                f"Average win profit: ${avg_win_profit:.2f}. Best win: ${best_win['profit']:.2f}"
            )
        
        # Bet type performance
        for bet_type, perf in bet_type_performance.items():
            if perf['wins'] + perf['losses'] > 0:
                bt_win_rate = perf['wins'] / (perf['wins'] + perf['losses'])
                if bt_win_rate >= 0.6:
                    insights['what_went_well'].append(
                        f"{bet_type.upper()} bets performing well: {bt_win_rate:.1%} win rate"
                    )
        
        # Confidence analysis
        high_conf_total = confidence_analysis['high']['wins'] + confidence_analysis['high']['losses']
        if high_conf_total > 0:
            high_conf_win_rate = confidence_analysis['high']['wins'] / high_conf_total
            if high_conf_win_rate >= 0.6:
                insights['what_went_well'].append(
                    f"High-confidence picks delivering: {high_conf_win_rate:.1%} win rate"
                )
        
        # Parlay results
        if parlay_results:
            parlay_wins = sum(1 for p in parlay_results if p['result'] == 'win')
            if parlay_wins > 0:
                insights['what_went_well'].append(
                    f"Parlays hit! {parlay_wins}/{len(parlay_results)} parlays won"
                )
        
        # What needs improvement
        if profit_loss < 0:
            insights['what_needs_improvement'].append(
                f"Lost ${abs(profit_loss):.2f} ({roi:.1f}% ROI). Need to review strategy."
            )
        
        if win_rate < 0.45:
            insights['what_needs_improvement'].append(
                f"Low win rate: {win_rate:.1%}. Consider raising EV threshold or improving model."
            )
        
        # Analyze losing picks
        if losing_picks:
            avg_loss = sum(l['loss'] for l in losing_picks) / len(losing_picks)
            worst_loss = max(losing_picks, key=lambda x: x['loss'])
            insights['what_needs_improvement'].append(
                f"Average loss: ${avg_loss:.2f}. Largest loss: ${worst_loss['loss']:.2f}"
            )
        
        # EV efficiency
        if ev_analysis['winners'] and ev_analysis['losers']:
            avg_expected_ev = sum(e['expected'] for e in ev_analysis['winners'] + ev_analysis['losers']) / len(ev_analysis['winners'] + ev_analysis['losers'])
            if avg_expected_ev > 0.1 and roi < 0:
                insights['what_needs_improvement'].append(
                    "High expected EV but negative ROI - model may be overestimating win probability"
                )
        
        # Bet type weaknesses
        for bet_type, perf in bet_type_performance.items():
            if perf['wins'] + perf['losses'] > 0:
                bt_win_rate = perf['wins'] / (perf['wins'] + perf['losses'])
                if bt_win_rate < 0.4:
                    insights['what_needs_improvement'].append(
                        f"{bet_type.upper()} bets struggling: {bt_win_rate:.1%} win rate - consider avoiding"
                    )
        
        # Confidence issues
        high_conf_total = confidence_analysis['high']['wins'] + confidence_analysis['high']['losses']
        if high_conf_total > 0:
            high_conf_win_rate = confidence_analysis['high']['wins'] / high_conf_total
            if high_conf_win_rate < 0.5:
                insights['what_needs_improvement'].append(
                    "High-confidence picks underperforming - may be overconfident"
                )
        
        # Key findings
        insights['key_findings'] = {
            'best_bet_type': max(bet_type_performance.items(), 
                               key=lambda x: x[1]['wins'] / (x[1]['wins'] + x[1]['losses']) if (x[1]['wins'] + x[1]['losses']) > 0 else 0)[0] if bet_type_performance else None,
            'worst_bet_type': min(bet_type_performance.items(),
                                key=lambda x: x[1]['wins'] / (x[1]['wins'] + x[1]['losses']) if (x[1]['wins'] + x[1]['losses']) > 0 else 1)[0] if bet_type_performance else None,
            'parlay_performance': f"{sum(1 for p in parlay_results if p['result'] == 'win')}/{len(parlay_results)}" if parlay_results else "N/A",
            'confidence_accuracy': {
                'high': confidence_analysis['high']['wins'] / (confidence_analysis['high']['wins'] + confidence_analysis['high']['losses']) if (confidence_analysis['high']['wins'] + confidence_analysis['high']['losses']) > 0 else 0,
                'medium': confidence_analysis['medium']['wins'] / (confidence_analysis['medium']['wins'] + confidence_analysis['medium']['losses']) if (confidence_analysis['medium']['wins'] + confidence_analysis['medium']['losses']) > 0 else 0,
                'low': confidence_analysis['low']['wins'] / (confidence_analysis['low']['wins'] + confidence_analysis['low']['losses']) if (confidence_analysis['low']['wins'] + confidence_analysis['low']['losses']) > 0 else 0,
            }
        }
        
        return insights
    
    def _generate_recommendations(
        self,
        insights: Dict[str, Any],
        win_rate: float,
        roi: float,
        profit_loss: float,
        accuracy_metrics: Dict[str, float],
        bet_type_performance: Dict,
        confidence_analysis: Dict
    ) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        # ROI-based recommendations
        if roi < -10:
            recommendations.append("URGENT: ROI below -10%. Consider pausing betting until model is reviewed.")
        elif roi < -5:
            recommendations.append("ROI below -5%. Review model accuracy and consider raising EV threshold.")
        elif roi > 10:
            recommendations.append("Strong ROI! Consider slightly increasing exposure if bankroll allows.")
        
        # Win rate recommendations
        if win_rate < 0.40:
            recommendations.append("Win rate below 40%. Strongly consider raising minimum EV threshold to 0.08+.")
        elif win_rate < 0.45:
            recommendations.append("Win rate below 45%. Consider raising minimum EV threshold to 0.06+.")
        elif win_rate > 0.60:
            recommendations.append("Excellent win rate! Current strategy is working well.")
        
        # Bet type recommendations
        for bet_type, perf in bet_type_performance.items():
            if perf['wins'] + perf['losses'] > 2:  # Need at least 3 bets for meaningful analysis
                bt_win_rate = perf['wins'] / (perf['wins'] + perf['losses'])
                if bt_win_rate < 0.35:
                    recommendations.append(
                        f"Consider reducing {bet_type.upper()} bets - only {bt_win_rate:.1%} win rate"
                    )
                elif bt_win_rate > 0.65:
                    recommendations.append(
                        f"{bet_type.upper()} bets performing well ({bt_win_rate:.1%} win rate) - consider prioritizing"
                    )
        
        # Confidence recommendations
        high_conf_total = confidence_analysis['high']['wins'] + confidence_analysis['high']['losses']
        if high_conf_total > 0:
            high_conf_win_rate = confidence_analysis['high']['wins'] / high_conf_total
            if high_conf_win_rate < 0.5:
                recommendations.append(
                    "High-confidence picks underperforming. Review confidence calculation or model calibration."
                )
        
        # EV efficiency recommendations
        if 'ev_efficiency' in accuracy_metrics:
            ev_eff = accuracy_metrics['ev_efficiency']
            if ev_eff < 0.5:
                recommendations.append(
                    f"EV efficiency low ({ev_eff:.2f}). Model may be overestimating win probabilities."
                )
            elif ev_eff > 1.2:
                recommendations.append(
                    f"EV efficiency high ({ev_eff:.2f}). Model is performing well - consider being more aggressive."
                )
        
        # Bankroll recommendations
        if profit_loss < -20:
            recommendations.append("Significant losses. Ensure bankroll management is conservative.")
        elif profit_loss > 20:
            recommendations.append("Strong profits. Bankroll is growing - maintain current strategy.")
        
        # Default if no specific issues
        if not recommendations:
            recommendations.append("Performance is within acceptable ranges. Continue current strategy.")
        
        return recommendations
    
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

