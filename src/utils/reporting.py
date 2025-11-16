"""Reporting and visualization utilities"""

from typing import List, Optional, Dict, Any
from datetime import date, timedelta, datetime
from pathlib import Path

from src.data.models import DailyReport, Pick, Bet, Bankroll, CardReview
from src.data.storage import Database, DailyReportModel, PickModel, BetModel, BankrollModel
from src.agents.auditor import Auditor
from src.utils.logging import get_logger

logger = get_logger("utils.reporting")


class ReportGenerator:
    """Generate daily and summary reports"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize report generator"""
        self.db = db
        self.auditor = Auditor(db) if db else None
    
    def generate_daily_report(self, target_date: Optional[date] = None) -> str:
        """Generate comprehensive daily report with insights"""
        if target_date is None:
            target_date = date.today()
        
        if not self.auditor:
            return f"No report available for {target_date}"
        
        report = self.auditor.process(target_date)
        
        # Format comprehensive report
        lines = [
            "=" * 80,
            f"DAILY PERFORMANCE REPORT - {target_date}",
            "=" * 80,
            "",
            "📊 PERFORMANCE SUMMARY",
            "-" * 80,
            f"Total Picks: {report.total_picks}",
            f"Wins: {report.wins}  |  Losses: {report.losses}  |  Pushes: {report.pushes}",
            f"Win Rate: {report.win_rate:.1%}",
            "",
            f"Total Wagered: ${report.total_wagered:.2f}",
            f"Total Payout: ${report.total_payout:.2f}",
            f"Profit/Loss: ${report.profit_loss:+.2f}",
            f"ROI: {report.roi:+.2f}%",
            "",
        ]
        
        if report.accuracy_metrics:
            lines.append("📈 ACCURACY METRICS")
            lines.append("-" * 80)
            for key, value in report.accuracy_metrics.items():
                lines.append(f"  {key.replace('_', ' ').title()}: {value:.3f}")
            lines.append("")
        
        # Insights section
        if report.insights:
            insights = report.insights
            lines.append("✅ WHAT WENT WELL")
            lines.append("-" * 80)
            if insights.get('what_went_well'):
                for item in insights['what_went_well']:
                    lines.append(f"  • {item}")
            else:
                lines.append("  • No significant wins to report")
            lines.append("")
            
            lines.append("⚠️  WHAT NEEDS IMPROVEMENT")
            lines.append("-" * 80)
            if insights.get('what_needs_improvement'):
                for item in insights['what_needs_improvement']:
                    lines.append(f"  • {item}")
            else:
                lines.append("  • No major issues identified")
            lines.append("")
            
            # Key findings
            if insights.get('key_findings'):
                lines.append("🔍 KEY FINDINGS")
                lines.append("-" * 80)
                key_findings = insights['key_findings']
                if key_findings.get('best_bet_type'):
                    lines.append(f"  Best Performing Bet Type: {key_findings['best_bet_type'].upper()}")
                if key_findings.get('worst_bet_type'):
                    lines.append(f"  Worst Performing Bet Type: {key_findings['worst_bet_type'].upper()}")
                if key_findings.get('parlay_performance') and key_findings['parlay_performance'] != "N/A":
                    lines.append(f"  Parlay Performance: {key_findings['parlay_performance']}")
                if key_findings.get('confidence_accuracy'):
                    conf_acc = key_findings['confidence_accuracy']
                    lines.append("  Confidence Accuracy:")
                    for level, rate in conf_acc.items():
                        if rate > 0:
                            lines.append(f"    {level.title()}: {rate:.1%}")
                lines.append("")
        
        # Recommendations section
        if report.recommendations:
            lines.append("💡 RECOMMENDATIONS")
            lines.append("-" * 80)
            for i, rec in enumerate(report.recommendations, 1):
                lines.append(f"  {i}. {rec}")
            lines.append("")
        
        lines.append("=" * 80)
        lines.append(f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def generate_summary_report(
        self,
        start_date: date,
        end_date: date
    ) -> str:
        """Generate summary report for date range"""
        if not self.db:
            return "No database available for summary report"
        
        session = self.db.get_session()
        try:
            # Get all reports in range
            reports = session.query(DailyReportModel).filter(
                DailyReportModel.date >= start_date,
                DailyReportModel.date <= end_date
            ).all()
            
            if not reports:
                return f"No reports available for {start_date} to {end_date}"
            
            # Aggregate statistics
            total_picks = sum(r.total_picks for r in reports)
            total_wins = sum(r.wins for r in reports)
            total_losses = sum(r.losses for r in reports)
            total_pushes = sum(r.pushes for r in reports)
            total_wagered = sum(r.total_wagered for r in reports)
            total_payout = sum(r.total_payout for r in reports)
            total_profit = sum(r.profit_loss for r in reports)
            
            win_rate = total_wins / total_picks if total_picks > 0 else 0.0
            roi = (total_profit / total_wagered * 100) if total_wagered > 0 else 0.0
            
            lines = [
                "=" * 60,
                f"SUMMARY REPORT - {start_date} to {end_date}",
                "=" * 60,
                "",
                f"Days: {len(reports)}",
                f"Total Picks: {total_picks}",
                f"Wins: {total_wins}",
                f"Losses: {total_losses}",
                f"Pushes: {total_pushes}",
                f"Win Rate: {win_rate:.1%}",
                "",
                f"Total Wagered: ${total_wagered:.2f}",
                f"Total Payout: ${total_payout:.2f}",
                f"Total Profit/Loss: ${total_profit:.2f}",
                f"ROI: {roi:.2f}%",
                "",
                "=" * 60,
            ]
            
            return "\n".join(lines)
            
        finally:
            session.close()
    
    def generate_bankroll_report(self) -> str:
        """Generate bankroll status report"""
        if not self.db:
            return "No database available for bankroll report"
        
        session = self.db.get_session()
        try:
            # Get current bankroll
            bankroll = session.query(BankrollModel).order_by(
                BankrollModel.date.desc()
            ).first()
            
            if not bankroll:
                return "No bankroll data available"
            
            # Get initial bankroll
            initial = session.query(BankrollModel).order_by(
                BankrollModel.date.asc()
            ).first()
            
            initial_balance = initial.balance if initial else bankroll.balance
            
            lines = [
                "=" * 60,
                "BANKROLL REPORT",
                "=" * 60,
                "",
                f"Current Balance: ${bankroll.balance:.2f}",
                f"Initial Balance: ${initial_balance:.2f}",
                f"Total Profit/Loss: ${bankroll.total_profit:.2f}",
                f"Total Wagered: ${bankroll.total_wagered:.2f}",
                f"Active Bets: {bankroll.active_bets}",
                "",
                f"Return: {((bankroll.balance - initial_balance) / initial_balance * 100):.2f}%",
                "",
                "=" * 60,
            ]
            
            return "\n".join(lines)
            
        finally:
            session.close()
    
    def generate_betting_card(self, approved_picks: List[Pick], card_date: date) -> str:
        """Generate betting card with approved picks for manual review"""
        from src.data.storage import GameModel
        
        lines = [
            "=" * 80,
            f"BETTING CARD - {card_date}",
            "=" * 80,
            "",
            f"Total Picks: {len(approved_picks)}",
            "",
        ]
        
        # Separate favorites from other picks
        favorite_picks = [p for p in approved_picks if p.favorite]
        other_picks = [p for p in approved_picks if not p.favorite]
        
        total_units = sum(p.stake_units for p in favorite_picks)
        total_amount = sum(p.stake_amount for p in favorite_picks)
        
        lines.append(f"Total Picks: {len(approved_picks)} ({len(favorite_picks)} favorites, {len(other_picks)} others)")
        lines.append(f"Total Units: {total_units:.2f}")
        lines.append(f"Total Amount: ${total_amount:.2f}")
        lines.append("")
        lines.append("-" * 80)
        lines.append("")
        
        # Get game information and betting lines from database
        game_info_map = {}
        betting_lines_map = {}  # Map pick to original betting line for context
        if self.db:
            from src.data.storage import BettingLineModel
            session = self.db.get_session()
            try:
                game_ids = [p.game_id for p in approved_picks if p.game_id]
                games = session.query(GameModel).filter(GameModel.id.in_(game_ids)).all()
                for game in games:
                    game_info_map[game.id] = {
                        'team1': game.team1,
                        'team2': game.team2,
                        'venue': game.venue
                    }
                
                # Get betting lines to help determine over/under and moneyline team
                for pick in approved_picks:
                    if pick.game_id:
                        # Try to find matching betting line
                        line_match = session.query(BettingLineModel).filter(
                            BettingLineModel.game_id == pick.game_id,
                            BettingLineModel.bet_type == pick.bet_type.value,
                            BettingLineModel.book == pick.book
                        ).first()
                        if line_match:
                            betting_lines_map[pick.id or 0] = line_match
            finally:
                session.close()
        
        # Show favorites first
        if favorite_picks:
            lines.append("⭐ FAVORITE PICKS (Place These)")
            lines.append("=" * 80)
            lines.append("")
            
            for i, pick in enumerate(favorite_picks, 1):
                lines.append(f"FAVORITE #{i}")
                self._format_pick_details(lines, pick, game_info_map, betting_lines_map, i)
            
            lines.append("")
            lines.append("-" * 80)
            lines.append("")
        
        # Then show other picks
        if other_picks:
            lines.append("📋 ALL OTHER PICKS (For Reference)")
            lines.append("=" * 80)
            lines.append("")
            
            for i, pick in enumerate(other_picks, 1):
                lines.append(f"PICK #{i}")
                self._format_pick_details(lines, pick, game_info_map, betting_lines_map, len(favorite_picks) + i)
            
            lines.append("")
        
        lines.append("=" * 80)
        lines.append(f"Card generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def _format_pick_details(
        self,
        lines: List[str],
        pick: 'Pick',
        game_info_map: Dict[int, Dict[str, str]],
        betting_lines_map: Dict[int, Any],
        pick_number: int
    ) -> None:
        """Helper method to format pick details for betting card"""
        # Get game info
        game_info = game_info_map.get(pick.game_id, {})
        team1 = game_info.get('team1', 'Team 1')
        team2 = game_info.get('team2', 'Team 2')
        venue = game_info.get('venue', '')
        
        # Format matchup
        if team1 and team2:
            matchup = f"{team2} @ {team1}"
            if venue:
                matchup += f" ({venue})"
            lines.append(f"  Matchup: {matchup}")
        else:
            lines.append(f"  Game ID: {pick.game_id}")
        
        lines.append(f"  Bet Type: {pick.bet_type.value.upper()}")
        
        # Determine which team is being bet on
        # First, try to use the original selection text from Picker if available
        if pick.selection_text:
            # Use the original selection text which should include team name
            lines.append(f"  Selection: {pick.selection_text}")
        else:
            # Fallback: determine from line and bet type
            selection_text = ""
            if pick.bet_type.value == "spread":
                if pick.line > 0:
                    # Positive line = betting on away team (team2) to cover
                    selection_text = f"{team2} +{pick.line:.1f}"
                elif pick.line < 0:
                    # Negative line = betting on home team (team1) to cover
                    selection_text = f"{team1} {pick.line:.1f}"
                else:
                    selection_text = "Pick'em"
                lines.append(f"  Selection: {selection_text}")
            elif pick.bet_type.value == "total":
                # For totals, try to extract Over/Under from rationale if available
                over_under = ""
                if pick.rationale:
                    rationale_lower = pick.rationale.lower()
                    if "over" in rationale_lower:
                        over_under = "Over"
                    elif "under" in rationale_lower:
                        over_under = "Under"
                
                if over_under:
                    lines.append(f"  Selection: {over_under} {pick.line:.1f}")
                else:
                    lines.append(f"  Total: {pick.line:.1f}")
                    lines.append(f"  Note: Check Over/Under on {pick.book} for this total")
            elif pick.bet_type.value == "moneyline":
                # For moneyline, try to extract team name from rationale
                team_from_rationale = None
                if pick.rationale:
                    # Look for team names in rationale
                    for team in [team1, team2]:
                        if team and team.lower() in pick.rationale.lower():
                            team_from_rationale = team
                            break
                
                if team_from_rationale:
                    selection_text = f"{team_from_rationale} (ML {pick.odds:+d})"
                else:
                    # Infer from odds
                    if pick.odds < 0:
                        # Favorite - more likely home team
                        selection_text = f"{team1} (ML {pick.odds:+d}) - Favorite"
                    else:
                        # Underdog - more likely away team
                        selection_text = f"{team2} (ML {pick.odds:+d}) - Underdog"
                lines.append(f"  Selection: {selection_text}")
        
        if pick.line and pick.bet_type.value not in ["total", "moneyline"]:
            lines.append(f"  Line: {pick.line:+.1f}")
        lines.append(f"  Odds: {pick.odds:+d}")
        
        # Show stake info only for favorites (they're the ones being bet)
        if pick.favorite:
            lines.append(f"  Units: {pick.stake_units:.2f}")
            lines.append(f"  Amount: ${pick.stake_amount:.2f}")
        
        lines.append(f"  Book: {pick.book}")
        lines.append(f"  Confidence Score: {pick.confidence_score}/10")
        lines.append(f"  Confidence: {pick.confidence:.1%}")
        lines.append(f"  Expected Value: {pick.expected_value:.3f}")
        if pick.rationale:
            lines.append(f"  Rationale: {pick.rationale}")
        lines.append("")
    
    def generate_presidents_report(
        self, 
        approved_picks: List[Pick], 
        card_review: CardReview,
        card_date: date,
        president_response: Optional[Dict[str, Any]] = None,
        researcher_output: Optional[Dict[str, Any]] = None,
        modeler_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate President's daily report with picks and detailed rationale"""
        lines = [
            "=" * 80,
            f"PRESIDENT'S DAILY REPORT - {card_date}",
            "=" * 80,
            "",
            f"Report Date: {card_date}",
            f"Card Status: {'APPROVED' if card_review.approved else 'REJECTED'}",
            f"Total Picks: {len(approved_picks)}",
            "",
        ]
        
        # Summary statistics
        total_units = sum(p.stake_units for p in approved_picks)
        total_amount = sum(p.stake_amount for p in approved_picks)
        avg_confidence = sum(p.confidence for p in approved_picks) / len(approved_picks) if approved_picks else 0.0
        avg_ev = sum(p.expected_value for p in approved_picks) / len(approved_picks) if approved_picks else 0.0
        
        lines.append("📊 CARD SUMMARY")
        lines.append("-" * 80)
        lines.append(f"Total Units: {total_units:.2f}")
        lines.append(f"Total Amount: ${total_amount:.2f}")
        lines.append(f"Average Confidence: {avg_confidence:.1%}")
        lines.append(f"Average Expected Value: {avg_ev:.3f}")
        lines.append("")
        
        # Strategic notes from President
        if card_review.review_notes:
            lines.append("📝 STRATEGIC NOTES")
            lines.append("-" * 80)
            lines.append(card_review.review_notes)
            lines.append("")
        
        # Detailed picks with rationale
        lines.append("🎯 APPROVED PICKS WITH RATIONALE")
        lines.append("-" * 80)
        lines.append("")
        
        for i, pick in enumerate(approved_picks, 1):
            lines.append(f"PICK #{i}")
            lines.append(f"  Game ID: {pick.game_id}")
            lines.append(f"  Bet Type: {pick.bet_type.value.upper()}")
            if pick.line:
                lines.append(f"  Line: {pick.line:+.1f}")
            lines.append(f"  Odds: {pick.odds:+d}")
            lines.append(f"  Stake: {pick.stake_units:.2f} units (${pick.stake_amount:.2f})")
            lines.append(f"  Book: {pick.book}")
            lines.append(f"  Confidence: {pick.confidence:.1%}")
            lines.append(f"  Expected Value: {pick.expected_value:.3f}")
            lines.append("")
            
            # Rationale from Picker
            if pick.rationale:
                lines.append("  📋 PICKER'S RATIONALE:")
                rationale_lines = pick.rationale.split('|') if '|' in pick.rationale else [pick.rationale]
                for rationale_line in rationale_lines:
                    lines.append(f"    • {rationale_line.strip()}")
                lines.append("")
            
            # Get President's final decision reasoning if available
            president_reasoning = None
            if president_response:
                from src.data.models import BetType
                import re
                approved_picks_data = president_response.get("approved_picks", [])
                for approved_data in approved_picks_data:
                    try:
                        game_id_str = str(approved_data.get("game_id", ""))
                        bet_type_str = approved_data.get("bet_type", "").lower()
                        selection = approved_data.get("selection", "")
                        reasoning = approved_data.get("final_decision_reasoning", "")
                        
                        # Extract line from selection
                        line = 0.0
                        if selection:
                            match = re.search(r'([+-]?\d+\.?\d*)', selection)
                            if match:
                                line = float(match.group(1))
                        
                        # Match with current pick
                        try:
                            game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                            bet_type = BetType(bet_type_str)
                            if (game_id == pick.game_id and 
                                bet_type.value == pick.bet_type.value and 
                                abs(line - pick.line) < 0.1):  # Allow small floating point differences
                                president_reasoning = reasoning
                                break
                        except (ValueError, KeyError):
                            pass
                    except Exception:
                        pass
            
            if president_reasoning:
                lines.append("  ✅ PRESIDENT'S DECISION REASONING:")
                lines.append(f"    {president_reasoning}")
                lines.append("")
            
            # Try to find additional context from researcher insights
            if researcher_output:
                game_insights = None
                for game in researcher_output.get('games', []):
                    if str(game.get('game_id')) == str(pick.game_id):
                        game_insights = game
                        break
                
                if game_insights:
                    lines.append("  🔍 RESEARCHER'S INSIGHTS:")
                    if game_insights.get('key_injuries'):
                        injuries = game_insights['key_injuries']
                        if injuries:
                            lines.append("    Injuries:")
                            for injury in injuries[:3]:  # Limit to 3 most important
                                lines.append(f"      - {injury}")
                    if game_insights.get('recent_form_summary'):
                        lines.append(f"    Recent Form: {game_insights['recent_form_summary']}")
                    if game_insights.get('notable_context'):
                        context = game_insights['notable_context']
                        if context:
                            lines.append("    Context:")
                            for ctx in context[:3]:  # Limit to 3
                                lines.append(f"      - {ctx}")
                    lines.append("")
            
            # Try to find model predictions
            if modeler_output:
                game_prediction = None
                for pred in modeler_output.get('game_models', []):
                    if str(pred.get('game_id')) == str(pick.game_id):
                        game_prediction = pred
                        break
                
                if game_prediction:
                    lines.append("  📊 MODELER'S PREDICTIONS:")
                    predictions = game_prediction.get('predictions', {})
                    if pick.bet_type.value == 'spread' and 'spread' in predictions:
                        spread_pred = predictions['spread']
                        lines.append(f"    Projected Margin: {spread_pred.get('projected_margin', 'N/A')}")
                        lines.append(f"    Model Confidence: {spread_pred.get('model_confidence', 0):.1%}")
                    elif pick.bet_type.value == 'total' and 'total' in predictions:
                        total_pred = predictions['total']
                        lines.append(f"    Projected Total: {total_pred.get('projected_total', 'N/A')}")
                        lines.append(f"    Model Confidence: {total_pred.get('model_confidence', 0):.1%}")
                    elif pick.bet_type.value == 'moneyline' and 'moneyline' in predictions:
                        ml_pred = predictions['moneyline']
                        probs = ml_pred.get('team_probabilities', {})
                        lines.append(f"    Win Probabilities: {probs}")
                        lines.append(f"    Model Confidence: {ml_pred.get('model_confidence', 0):.1%}")
                    
                    # Market edges
                    edges = game_prediction.get('market_edges', [])
                    for edge in edges:
                        if edge.get('market_type') == pick.bet_type.value:
                            lines.append(f"    Edge: {edge.get('edge', 0):.3f}")
                            lines.append(f"    Edge Confidence: {edge.get('edge_confidence', 0):.1%}")
                            break
                    lines.append("")
            
            lines.append("-" * 80)
            lines.append("")
        
        # Rejected picks with detailed reasoning
        if card_review.picks_rejected:
            lines.append("❌ REJECTED PICKS WITH REASONING")
            lines.append("-" * 80)
            lines.append(f"Total Rejected: {len(card_review.picks_rejected)}")
            lines.append("")
            
            # Get rejection reasons from president_response
            rejected_picks_data = []
            if president_response:
                rejected_picks_data = president_response.get("rejected_picks", [])
            
            # Create a map of rejected picks by game_id, bet_type, and selection
            rejected_map = {}
            for rejected_data in rejected_picks_data:
                try:
                    game_id_str = str(rejected_data.get("game_id", ""))
                    bet_type_str = rejected_data.get("bet_type", "").lower()
                    selection = rejected_data.get("selection", "")
                    reason = rejected_data.get("reason_rejected", "")
                    
                    # Extract line from selection if available
                    line = 0.0
                    if selection:
                        import re
                        match = re.search(r'([+-]?\d+\.?\d*)', selection)
                        if match:
                            line = float(match.group(1))
                    
                    # Create key for matching
                    try:
                        game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                        from src.data.models import BetType
                        bet_type = BetType(bet_type_str)
                        key = (game_id, bet_type.value, line)
                        rejected_map[key] = reason
                    except (ValueError, KeyError):
                        pass
                except Exception:
                    pass
            
            # Get game information for context
            from src.data.storage import GameModel
            game_info_map = {}
            if self.db:
                session = self.db.get_session()
                try:
                    rejected_game_ids = [pick_id for pick_id in card_review.picks_rejected]
                    # Get picks to find game_ids
                    from src.data.storage import PickModel
                    rejected_picks_models = session.query(PickModel).filter(
                        PickModel.id.in_(rejected_game_ids)
                    ).all()
                    game_ids = [p.game_id for p in rejected_picks_models if p.game_id]
                    
                    if game_ids:
                        games = session.query(GameModel).filter(GameModel.id.in_(game_ids)).all()
                        for game in games:
                            game_info_map[game.id] = {
                                'team1': game.team1,
                                'team2': game.team2,
                                'venue': game.venue
                            }
                finally:
                    session.close()
            
            # Display rejected picks with reasoning
            for i, pick_id in enumerate(card_review.picks_rejected, 1):
                lines.append(f"REJECTED PICK #{i}")
                
                # Try to get pick details from database
                pick_details = None
                if self.db:
                    session = self.db.get_session()
                    try:
                        from src.data.storage import PickModel
                        pick_model = session.query(PickModel).filter(PickModel.id == pick_id).first()
                        if pick_model:
                            pick_details = {
                                'game_id': pick_model.game_id,
                                'bet_type': pick_model.bet_type.value,
                                'line': pick_model.line,
                                'odds': pick_model.odds,
                                'selection_text': None,  # Not stored in DB
                                'rationale': pick_model.rationale
                            }
                            
                            # Get game info
                            game_info = game_info_map.get(pick_model.game_id, {})
                            team1 = game_info.get('team1', 'Team 1')
                            team2 = game_info.get('team2', 'Team 2')
                            
                            lines.append(f"  Game ID: {pick_model.game_id}")
                            if team1 and team2:
                                lines.append(f"  Matchup: {team2} @ {team1}")
                            lines.append(f"  Bet Type: {pick_model.bet_type.value.upper()}")
                            if pick_model.line:
                                lines.append(f"  Line: {pick_model.line:+.1f}")
                            lines.append(f"  Odds: {pick_model.odds:+d}")
                            if pick_model.rationale:
                                lines.append(f"  Original Rationale: {pick_model.rationale}")
                    finally:
                        session.close()
                else:
                    lines.append(f"  Pick ID: {pick_id}")
                
                # Get rejection reason from president_response
                rejection_reason = None
                if pick_details:
                    key = (pick_details['game_id'], pick_details['bet_type'], pick_details.get('line', 0.0))
                    rejection_reason = rejected_map.get(key)
                
                if rejection_reason:
                    lines.append(f"  ❌ REJECTION REASON:")
                    lines.append(f"    {rejection_reason}")
                else:
                    lines.append(f"  ❌ REJECTION REASON: Not specified in President's response")
                
                lines.append("")
        
        lines.append("=" * 80)
        lines.append(f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def generate_agent_report(
        self,
        agent_name: str,
        agent_output: Dict[str, Any],
        target_date: date,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate a debug report for a specific agent"""
        lines = [
            "=" * 80,
            f"{agent_name.upper()} AGENT REPORT - {target_date}",
            "=" * 80,
            "",
            f"Report Date: {target_date}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        
        # Add metadata if provided
        if metadata:
            lines.append("📋 METADATA")
            lines.append("-" * 80)
            for key, value in metadata.items():
                lines.append(f"{key}: {value}")
            lines.append("")
        
        # Format agent output based on agent type
        lines.append("📊 AGENT OUTPUT")
        lines.append("-" * 80)
        lines.append("")
        
        if agent_name.lower() == "researcher":
            games = agent_output.get("games", [])
            lines.append(f"Total Games Researched: {len(games)}")
            lines.append("")
            for i, game in enumerate(games, 1):
                lines.append(f"GAME #{i}")
                game_id = game.get("game_id", "N/A")
                teams = game.get("teams", {})
                away = teams.get("away", "N/A")
                home = teams.get("home", "N/A")
                lines.append(f"  Game ID: {game_id}")
                lines.append(f"  Matchup: {away} @ {home}")
                lines.append(f"  Start Time: {game.get('start_time', 'N/A')}")
                
                market = game.get("market", {})
                if market:
                    lines.append("  Market Data:")
                    if market.get("spread"):
                        lines.append(f"    Spread: {market['spread']}")
                    if market.get("total"):
                        lines.append(f"    Total: {market['total']}")
                    if market.get("moneyline"):
                        ml = market["moneyline"]
                        lines.append(f"    Moneyline: Away {ml.get('away', 'N/A')} / Home {ml.get('home', 'N/A')}")
                
                injuries = game.get("key_injuries", [])
                if injuries:
                    lines.append(f"  Key Injuries ({len(injuries)}):")
                    for injury in injuries[:5]:  # Limit to 5
                        lines.append(f"    - {injury}")
                
                if game.get("recent_form_summary"):
                    lines.append(f"  Recent Form: {game['recent_form_summary']}")
                
                context = game.get("notable_context", [])
                if context:
                    lines.append(f"  Notable Context ({len(context)}):")
                    for ctx in context[:5]:  # Limit to 5
                        lines.append(f"    - {ctx}")
                
                if game.get("data_quality_notes"):
                    lines.append(f"  Data Quality Notes: {game['data_quality_notes']}")
                
                lines.append("")
        
        elif agent_name.lower() == "modeler":
            game_models = agent_output.get("game_models", [])
            lines.append(f"Total Games Modeled: {len(game_models)}")
            lines.append("")
            for i, model in enumerate(game_models, 1):
                lines.append(f"GAME MODEL #{i}")
                game_id = model.get("game_id", "N/A")
                lines.append(f"  Game ID: {game_id}")
                
                predictions = model.get("predictions", {})
                if predictions:
                    lines.append("  Predictions:")
                    if "spread" in predictions:
                        spread = predictions["spread"]
                        lines.append(f"    Spread: {spread.get('projected_line', 'N/A')} (Margin: {spread.get('projected_margin', 'N/A')})")
                        lines.append(f"    Confidence: {spread.get('model_confidence', 0):.1%}")
                    if "total" in predictions:
                        total = predictions["total"]
                        lines.append(f"    Total: {total.get('projected_total', 'N/A')}")
                        lines.append(f"    Confidence: {total.get('model_confidence', 0):.1%}")
                    if "moneyline" in predictions:
                        ml = predictions["moneyline"]
                        probs = ml.get("team_probabilities", {})
                        lines.append(f"    Moneyline Probabilities: Away {probs.get('away', 0):.1%} / Home {probs.get('home', 0):.1%}")
                        lines.append(f"    Confidence: {ml.get('model_confidence', 0):.1%}")
                
                edges = model.get("market_edges", [])
                if edges:
                    lines.append(f"  Market Edges ({len(edges)}):")
                    for edge in edges:
                        lines.append(f"    {edge.get('market_type', 'N/A').upper()}: Edge {edge.get('edge', 0):.3f} "
                                   f"(Confidence: {edge.get('edge_confidence', 0):.1%})")
                        lines.append(f"      Market Line: {edge.get('market_line', 'N/A')}")
                        lines.append(f"      Model Prob: {edge.get('model_estimated_probability', 0):.1%} "
                                   f"vs Implied: {edge.get('implied_probability', 0):.1%}")
                
                if model.get("model_notes"):
                    lines.append(f"  Model Notes: {model['model_notes']}")
                
                lines.append("")
        
        elif agent_name.lower() == "picker":
            candidate_picks = agent_output.get("candidate_picks", [])
            lines.append(f"Total Candidate Picks: {len(candidate_picks)}")
            
            strategy = agent_output.get("overall_strategy_summary", [])
            if strategy:
                lines.append("")
                lines.append("Strategy Summary:")
                for item in strategy:
                    lines.append(f"  - {item}")
            
            lines.append("")
            for i, pick in enumerate(candidate_picks, 1):
                lines.append(f"PICK #{i}")
                lines.append(f"  Game ID: {pick.get('game_id', 'N/A')}")
                lines.append(f"  Bet Type: {pick.get('bet_type', 'N/A').upper()}")
                lines.append(f"  Selection: {pick.get('selection', 'N/A')}")
                lines.append(f"  Odds: {pick.get('odds', 'N/A')}")
                lines.append(f"  Edge Estimate: {pick.get('edge_estimate', 0):.3f}")
                lines.append(f"  Confidence: {pick.get('confidence', 0):.1%}")
                
                justification = pick.get("justification", [])
                if justification:
                    lines.append("  Justification:")
                    for j in justification:
                        lines.append(f"    - {j}")
                
                if pick.get("notes"):
                    lines.append(f"  Notes: {pick['notes']}")
                
                lines.append("")
        
        elif agent_name.lower() == "banker":
            sized_picks = agent_output.get("sized_picks", [])
            bankroll_status = agent_output.get("bankroll_status", {})
            
            lines.append("Bankroll Status:")
            lines.append(f"  Current Bankroll: ${bankroll_status.get('current_bankroll', 0):.2f}")
            lines.append(f"  Base Unit Size: {bankroll_status.get('base_unit_size', 0):.2f}")
            lines.append(f"  Risk Mode: {bankroll_status.get('risk_mode', 'N/A')}")
            if bankroll_status.get("notes"):
                lines.append(f"  Notes: {bankroll_status['notes']}")
            
            lines.append("")
            lines.append(f"Total Sized Picks: {len(sized_picks)}")
            
            exposure = agent_output.get("total_daily_exposure_summary", {})
            if exposure:
                lines.append("")
                lines.append("Daily Exposure Summary:")
                lines.append(f"  Number of Bets: {exposure.get('num_bets', 0)}")
                lines.append(f"  Total Units Risked: {exposure.get('total_units_risked', 0):.2f}")
                if exposure.get("concentration_notes"):
                    lines.append(f"  Concentration: {exposure['concentration_notes']}")
            
            lines.append("")
            for i, pick in enumerate(sized_picks, 1):
                lines.append(f"SIZED PICK #{i}")
                lines.append(f"  Game ID: {pick.get('game_id', 'N/A')}")
                lines.append(f"  Bet Type: {pick.get('bet_type', 'N/A').upper()}")
                lines.append(f"  Selection: {pick.get('selection', 'N/A')}")
                lines.append(f"  Odds: {pick.get('odds', 'N/A')}")
                lines.append(f"  Units: {pick.get('units', 0):.2f}")
                lines.append(f"  Edge: {pick.get('edge_estimate', 0):.3f}")
                lines.append(f"  Confidence: {pick.get('confidence', 0):.1%}")
                
                rationale = pick.get("stake_rationale", [])
                if rationale:
                    lines.append("  Stake Rationale:")
                    for r in rationale:
                        lines.append(f"    - {r}")
                
                flags = pick.get("risk_flags", [])
                if flags:
                    lines.append("  Risk Flags:")
                    for flag in flags:
                        lines.append(f"    ⚠️  {flag}")
                
                lines.append("")
        
        elif agent_name.lower() == "compliance":
            bet_reviews = agent_output.get("bet_reviews", [])
            global_assessment = agent_output.get("global_risk_assessment", [])
            
            lines.append(f"Total Bet Reviews: {len(bet_reviews)}")
            
            approved = sum(1 for r in bet_reviews if r.get("compliance_status") == "approved")
            approved_warn = sum(1 for r in bet_reviews if r.get("compliance_status") == "approved_with_warning")
            rejected = sum(1 for r in bet_reviews if r.get("compliance_status") == "rejected")
            
            lines.append(f"  Approved: {approved}")
            lines.append(f"  Approved with Warning: {approved_warn}")
            lines.append(f"  Rejected: {rejected}")
            
            if global_assessment:
                lines.append("")
                lines.append("Global Risk Assessment:")
                for item in global_assessment:
                    lines.append(f"  - {item}")
            
            lines.append("")
            for i, review in enumerate(bet_reviews, 1):
                lines.append(f"REVIEW #{i}")
                lines.append(f"  Game ID: {review.get('game_id', 'N/A')}")
                lines.append(f"  Selection: {review.get('selection', 'N/A')}")
                lines.append(f"  Units: {review.get('units', 0):.2f}")
                lines.append(f"  Status: {review.get('compliance_status', 'N/A').upper()}")
                
                issues = review.get("issues", [])
                if issues:
                    lines.append("  Issues:")
                    for issue in issues:
                        lines.append(f"    ⚠️  {issue}")
                
                recommendations = review.get("recommendations", [])
                if recommendations:
                    lines.append("  Recommendations:")
                    for rec in recommendations:
                        lines.append(f"    ✓ {rec}")
                
                lines.append("")
        
        elif agent_name.lower() == "president":
            approved_picks = agent_output.get("approved_picks", [])
            rejected_picks = agent_output.get("rejected_picks", [])
            strategy_notes = agent_output.get("high_level_strategy_notes", [])
            
            lines.append(f"Total Approved Picks: {len(approved_picks)}")
            lines.append(f"Total Rejected Picks: {len(rejected_picks)}")
            
            if strategy_notes:
                lines.append("")
                lines.append("High-Level Strategy Notes:")
                for note in strategy_notes:
                    lines.append(f"  - {note}")
            
            if approved_picks:
                lines.append("")
                lines.append("APPROVED PICKS:")
                for i, pick in enumerate(approved_picks, 1):
                    lines.append(f"  #{i}: Game {pick.get('game_id', 'N/A')} - {pick.get('bet_type', 'N/A').upper()} "
                               f"({pick.get('selection', 'N/A')}) - {pick.get('units', 0):.2f} units")
                    if pick.get("final_decision_reasoning"):
                        lines.append(f"    Reasoning: {pick['final_decision_reasoning']}")
            
            if rejected_picks:
                lines.append("")
                lines.append("REJECTED PICKS:")
                for i, pick in enumerate(rejected_picks, 1):
                    lines.append(f"  #{i}: Game {pick.get('game_id', 'N/A')} - {pick.get('bet_type', 'N/A').upper()} "
                               f"({pick.get('selection', 'N/A')})")
                    lines.append(f"    Reason: {pick.get('reason_rejected', 'N/A')}")
        
        elif agent_name.lower() == "auditor":
            period_summary = agent_output.get("period_summary", {})
            bet_analysis = agent_output.get("bet_level_analysis", [])
            diagnostics = agent_output.get("diagnostics_and_recommendations", {})
            
            if period_summary:
                lines.append("Period Summary:")
                lines.append(f"  Period: {period_summary.get('start_date', 'N/A')} to {period_summary.get('end_date', 'N/A')}")
                lines.append(f"  Number of Bets: {period_summary.get('num_bets', 0)}")
                lines.append(f"  Units Won/Lost: {period_summary.get('units_won_or_lost', 0):+.2f}")
                lines.append(f"  ROI: {period_summary.get('roi', 0):.2%}")
                lines.append(f"  Hit Rate: {period_summary.get('hit_rate', 0):.1%}")
                lines.append(f"  Max Drawdown: {period_summary.get('max_drawdown_units', 0):.2f} units")
                if period_summary.get("notes"):
                    lines.append(f"  Notes: {period_summary['notes']}")
                lines.append("")
            
            if bet_analysis:
                lines.append(f"Bet-Level Analysis ({len(bet_analysis)} bets):")
                for i, bet in enumerate(bet_analysis, 1):
                    lines.append(f"  BET #{i}: {bet.get('selection', 'N/A')}")
                    lines.append(f"    Result: {bet.get('result', 'N/A').upper()}")
                    lines.append(f"    Units Result: {bet.get('units_result', 0):+.2f}")
                    lines.append(f"    Edge Estimate: {bet.get('edge_estimate', 0):.3f}")
                    lines.append(f"    Confidence: {bet.get('confidence', 0):.1%}")
                    lines.append(f"    Consistent with Model: {bet.get('was_result_consistent_with_model', 'N/A')}")
                    if bet.get("post_hoc_notes"):
                        lines.append(f"    Notes: {bet['post_hoc_notes']}")
                lines.append("")
            
            if diagnostics:
                lines.append("Diagnostics and Recommendations:")
                for agent, recs in diagnostics.items():
                    if recs:
                        lines.append(f"  {agent.upper()}:")
                        for rec in recs:
                            lines.append(f"    - {rec}")
        
        else:
            # Generic output for other agents
            lines.append("Raw Output:")
            import json
            lines.append(json.dumps(agent_output, indent=2, default=str))
        
        lines.append("")
        lines.append("=" * 80)
        lines.append(f"Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def save_agent_report(
        self,
        agent_name: str,
        agent_output: Dict[str, Any],
        target_date: date,
        metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "data/reports"
    ) -> Path:
        """Generate and save an agent report in agent-specific subdirectory"""
        report_text = self.generate_agent_report(agent_name, agent_output, target_date, metadata)
        filename = f"{agent_name.lower()}_{target_date.isoformat()}.txt"
        # Create agent-specific subdirectory
        agent_subdir = Path(output_dir) / agent_name.lower()
        return self.save_report_to_file(report_text, filename, str(agent_subdir))
    
    def save_report_to_file(
        self,
        report_text: str,
        filename: Optional[str] = None,
        output_dir: str = "data/reports"
    ) -> Path:
        """Save report to file"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        if filename is None:
            filename = f"report_{date.today().isoformat()}.txt"
        
        file_path = output_path / filename
        
        with open(file_path, 'w') as f:
            f.write(report_text)
        
        logger.info(f"Report saved to {file_path}")
        return file_path
    
    def print_report(self, report_text: str):
        """Print report to console"""
        print(report_text)


def generate_and_save_daily_report(
    target_date: Optional[date] = None,
    db: Optional[Database] = None,
    save_file: bool = True
) -> str:
    """Convenience function to generate and save daily report"""
    generator = ReportGenerator(db)
    report = generator.generate_daily_report(target_date)
    
    if save_file:
        generator.save_report_to_file(report)
    
    return report

