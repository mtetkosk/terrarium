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
    
    def generate_betting_card(
        self, 
        approved_picks: List[Pick], 
        card_date: date, 
        approved: bool = True,
        modeler_output: Optional[Dict[str, Any]] = None,
        president_response: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate betting card with picks for manual review
        
        Generated from president's report structure:
        - Approved picks become best bets
        - Rejected picks go in "other picks" section (without rejection reasons)
        
        Args:
            approved_picks: List of picks to include in the card (includes both approved and rejected)
            card_date: Date of the betting card
            approved: Whether the card was approved (default: True)
            modeler_output: Optional modeler output containing predicted scores
            president_response: Optional president response (not currently used, for future extensibility)
        """
        from src.data.storage import GameModel
        
        status = "APPROVED" if approved else "REJECTED"
        lines = [
            "=" * 80,
            f"BETTING CARD - {card_date}",
            f"STATUS: {status}",
            "=" * 80,
            "",
        ]
        
        if not approved:
            lines.append("⚠️  CARD REJECTED - NO BETS PLACED")
            lines.append("")
            lines.append("The President has rejected this betting card. All picks below were analyzed")
            lines.append("but no bets will be placed. Review the picks and President's report for details.")
            lines.append("")
            lines.append("-" * 80)
            lines.append("")
        
        # Separate best bets from other picks (best_bet only)
        favorite_picks = [p for p in approved_picks if p.best_bet]
        other_picks = [p for p in approved_picks if not p.best_bet]
        
        total_units = sum(p.stake_units for p in favorite_picks)
        total_amount = sum(p.stake_amount for p in favorite_picks)
        
        lines.append(f"Total Picks: {len(approved_picks)} ({len(favorite_picks)} best bets, {len(other_picks)} others)")
        if approved:
            lines.append(f"Total Units: {total_units:.2f}")
            lines.append(f"Total Amount: ${total_amount:.2f}")
        else:
            lines.append(f"Total Units (not placed): {total_units:.2f}")
            lines.append(f"Total Amount (not placed): ${total_amount:.2f}")
        lines.append("")
        lines.append("-" * 80)
        lines.append("")
        
        # Get game information and betting lines from database first (needed for underdog matchup)
        underdog_game_id = None
        if president_response:
            daily_portfolio = president_response.get("daily_portfolio", {})
            if not daily_portfolio:
                daily_portfolio = president_response
            underdog_data = daily_portfolio.get("underdog_of_the_day")
            if underdog_data:
                underdog_game_id = underdog_data.get("game_id")
        
        # Get game information and betting lines from database
        game_info_map = {}
        betting_lines_map = {}  # Map pick to original betting line for context
        predicted_scores_map = {}  # Map game_id to predicted score
        
        # Extract predicted scores from modeler output
        if modeler_output:
            game_models = modeler_output.get("game_models", [])
            for model in game_models:
                game_id = model.get("game_id")
                predicted_score = model.get("predicted_score")
                if game_id and predicted_score:
                    # Store as dict with away_score and home_score
                    predicted_scores_map[str(game_id)] = predicted_score
        
        if self.db:
            from src.data.storage import BettingLineModel
            session = self.db.get_session()
            try:
                game_ids = [p.game_id for p in approved_picks if p.game_id]
                # Also include underdog game_id if available
                if president_response:
                    daily_portfolio = president_response.get("daily_portfolio", {})
                    if not daily_portfolio:
                        daily_portfolio = president_response
                    underdog = daily_portfolio.get("underdog_of_the_day")
                    if underdog:
                        underdog_game_id = underdog.get("game_id")
                        if underdog_game_id and int(underdog_game_id) not in game_ids:
                            game_ids.append(int(underdog_game_id))
                
                from src.data.storage import TeamModel
                from sqlalchemy.orm import aliased
                team1_alias = aliased(TeamModel)
                team2_alias = aliased(TeamModel)
                games = session.query(
                    GameModel,
                    team1_alias.normalized_team_name.label('team1_name'),
                    team2_alias.normalized_team_name.label('team2_name')
                ).join(
                    team1_alias, GameModel.team1_id == team1_alias.id
                ).join(
                    team2_alias, GameModel.team2_id == team2_alias.id
                ).filter(GameModel.id.in_(game_ids)).all()
                
                for game, team1_name, team2_name in games:
                    game_info_map[game.id] = {
                        'team1': team1_name,
                        'team2': team2_name,
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
        
        # Extract and display Underdog of the Day if available (after we have game_info_map)
        if president_response and underdog_game_id:
            daily_portfolio = president_response.get("daily_portfolio", {})
            if not daily_portfolio:
                daily_portfolio = president_response
            
            underdog = daily_portfolio.get("underdog_of_the_day")
            if underdog:
                lines.append("🐕 UNDERDOG OF THE DAY")
                lines.append("=" * 80)
                lines.append("")
                
                game_id_int = int(underdog_game_id) if isinstance(underdog_game_id, str) else underdog_game_id
                game_info = game_info_map.get(game_id_int, {})
                team1 = game_info.get('team1', 'Home')
                team2 = game_info.get('team2', 'Away')
                venue = game_info.get('venue', '')
                
                matchup = f"{team2} @ {team1}"
                if venue:
                    matchup += f" ({venue})"
                
                selection = underdog.get("selection", "")
                odds = underdog.get("market_odds", "")
                model_projection = underdog.get("model_projection", "")
                reasoning = underdog.get("reasoning", "")
                
                lines.append(f"  Matchup: {matchup}")
                lines.append(f"  Selection: {selection}")
                if odds:
                    lines.append(f"  Odds: {odds}")
                if model_projection:
                    lines.append(f"  Model Projection: {model_projection}")
                if reasoning:
                    lines.append(f"  Reasoning: {reasoning}")
                
                lines.append("")
                lines.append("-" * 80)
                lines.append("")
        
        # Show best bets first
        if favorite_picks:
            if approved:
                lines.append("⭐ BEST BETS (Place These)")
            else:
                lines.append("⭐ BEST BETS (Not Placed - Card Rejected)")
            lines.append("=" * 80)
            lines.append("")
            
            for i, pick in enumerate(favorite_picks, 1):
                lines.append(f"BEST BET #{i}")
                self._format_pick_details(lines, pick, game_info_map, betting_lines_map, predicted_scores_map, i)
            
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
                self._format_pick_details(lines, pick, game_info_map, betting_lines_map, predicted_scores_map, len(favorite_picks) + i)
            
            lines.append("")
        
        lines.append("=" * 80)
        lines.append(f"Card generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        
        return "\n".join(lines)
    
    def _get_game_info_map(self, game_ids: List[int]) -> Dict[int, Dict[str, str]]:
        """Helper to get game information map from database"""
        game_info_map = {}
        if self.db:
            from src.data.storage import GameModel
            session = self.db.get_session()
            try:
                from src.data.storage import TeamModel
                from sqlalchemy.orm import aliased
                team1_alias = aliased(TeamModel)
                team2_alias = aliased(TeamModel)
                games = session.query(
                    GameModel,
                    team1_alias.normalized_team_name.label('team1_name'),
                    team2_alias.normalized_team_name.label('team2_name')
                ).join(
                    team1_alias, GameModel.team1_id == team1_alias.id
                ).join(
                    team2_alias, GameModel.team2_id == team2_alias.id
                ).filter(GameModel.id.in_(game_ids)).all()
                
                for game, team1_name, team2_name in games:
                    game_info_map[game.id] = {
                        'team1': team1_name,
                        'team2': team2_name,
                        'venue': game.venue
                    }
            finally:
                session.close()
        return game_info_map
    
    def _format_pick_with_teams(
        self,
        pick_data: Dict[str, Any],
        game_info_map: Dict[int, Dict[str, str]],
        modeler_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Format a pick with team names and matchup instead of game number"""
        game_id = pick_data.get('game_id')
        if not game_id:
            return f"Game {game_id} - {pick_data.get('bet_type', 'N/A').upper()} ({pick_data.get('selection', 'N/A')})"
        
        # Normalize game_id to int
        try:
            game_id_int = int(game_id) if isinstance(game_id, str) and game_id.isdigit() else game_id
        except (ValueError, TypeError):
            game_id_int = game_id
        
        # Get game info
        game_info = game_info_map.get(game_id_int, {})
        team1 = game_info.get('team1', 'Team 1')
        team2 = game_info.get('team2', 'Team 2')
        
        # Get bet type and selection
        bet_type = pick_data.get('bet_type', '').upper()
        selection = pick_data.get('selection', '')
        
        # Get line - handle both float and string
        line = pick_data.get('line', 0.0)
        if isinstance(line, str):
            try:
                line = float(line)
            except (ValueError, TypeError):
                line = 0.0
        
        # Format the pick display
        if bet_type == 'SPREAD':
            # Extract team name from selection or determine from line
            if selection:
                # Try to find team name in selection
                if team1 and team1.lower() in selection.lower():
                    team_name = team1
                    opponent = team2
                elif team2 and team2.lower() in selection.lower():
                    team_name = team2
                    opponent = team1
                else:
                    # Infer from line: positive = away team (team2), negative = home team (team1)
                    if line > 0:
                        team_name = team2
                        opponent = team1
                    else:
                        team_name = team1
                        opponent = team2
            else:
                # Infer from line
                if line > 0:
                    team_name = team2
                    opponent = team1
                else:
                    team_name = team1
                    opponent = team2
            
            # Format line: show as +1.5 or -1.5, removing unnecessary decimals
            if line == 0:
                line_str = "0"
            elif line == int(line):
                line_str = f"{int(line):+d}"
            else:
                line_str = f"{line:+.1f}"
            return f"{team_name} {line_str} vs {opponent}"
        
        elif bet_type == 'TOTAL':
            # For totals, show the over/under
            over_under = "Over" if "over" in selection.lower() else "Under" if "under" in selection.lower() else ""
            if over_under:
                return f"{over_under} {line:.1f} - {team2} @ {team1}"
            else:
                return f"Total {line:.1f} - {team2} @ {team1}"
        
        elif bet_type == 'MONEYLINE':
            # Convert odds to int once for all uses
            odds = pick_data.get('odds', 0)
            try:
                odds_int = int(odds) if odds else 0
            except (ValueError, TypeError):
                odds_int = 0
            
            # Try to extract team name from selection
            if selection:
                if team1 and team1.lower() in selection.lower():
                    team_name = team1
                    opponent = team2
                elif team2 and team2.lower() in selection.lower():
                    team_name = team2
                    opponent = team1
                else:
                    # Infer from odds: negative = favorite (likely home), positive = underdog (likely away)
                    if odds_int < 0:
                        team_name = team1
                        opponent = team2
                    else:
                        team_name = team2
                        opponent = team1
            else:
                # Infer from odds
                if odds_int < 0:
                    team_name = team1
                    opponent = team2
                else:
                    team_name = team2
                    opponent = team1
            
            return f"{team_name} (ML {odds_int:+d}) vs {opponent}"
        
        else:
            return f"{team2} @ {team1} - {bet_type} ({selection})"
    
    def _get_predicted_score(
        self,
        game_id: Any,
        modeler_output: Optional[Dict[str, Any]] = None,
        game_info_map: Optional[Dict[int, Dict[str, str]]] = None
    ) -> Optional[str]:
        """Get predicted score for a game from modeler output"""
        if not modeler_output:
            return None
        
        game_models = modeler_output.get("game_models", [])
        for model in game_models:
            if str(model.get("game_id")) == str(game_id):
                predicted_score = model.get("predicted_score")
                if predicted_score:
                    away_score = predicted_score.get('away_score')
                    home_score = predicted_score.get('home_score')
                    if away_score is not None and home_score is not None:
                        # If game_info_map provided, include team names
                        if game_info_map:
                            game_info = game_info_map.get(int(game_id) if isinstance(game_id, str) and game_id.isdigit() else game_id, {})
                            team1 = game_info.get('team1', 'Home')
                            team2 = game_info.get('team2', 'Away')
                            return f"{team2} {away_score:.0f} - {team1} {home_score:.0f}"
                        else:
                            return f"{away_score:.0f}-{home_score:.0f}"
        return None
    
    def _format_pick_details(
        self,
        lines: List[str],
        pick: 'Pick',
        game_info_map: Dict[int, Dict[str, str]],
        betting_lines_map: Dict[int, Any],
        predicted_scores_map: Dict[str, Dict[str, float]],
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
        
        # Add predicted score if available
        predicted_score = predicted_scores_map.get(str(pick.game_id))
        if predicted_score:
            away_score = predicted_score.get('away_score')
            home_score = predicted_score.get('home_score')
            if away_score is not None and home_score is not None:
                lines.append(f"  Predicted Score: {team2} {away_score:.1f} - {team1} {home_score:.1f}")
        
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
        
        # Show stake info if stake was allocated (stake_units > 0)
        # This should always be true for best bets that are being placed
        if pick.stake_units > 0:
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
        
        # Strategic notes from President (from daily_report_summary)
        daily_report_summary = president_response.get("daily_report_summary", {}) if president_response else {}
        strategic_notes = daily_report_summary.get("strategic_notes", [])
        if strategic_notes:
            lines.append("📝 STRATEGIC NOTES")
            lines.append("-" * 80)
            for note in strategic_notes:
                lines.append(f"  • {note}")
            lines.append("")
        elif card_review.review_notes:
            lines.append("📝 STRATEGIC NOTES")
            lines.append("-" * 80)
            lines.append(card_review.review_notes)
            lines.append("")
        
        # Best bets summary
        best_bet_picks = [p for p in approved_picks if p.best_bet]
        if best_bet_picks:
            lines.append("⭐ BEST BETS (Top Picks)")
            lines.append("-" * 80)
            lines.append(f"Total Best Bets: {len(best_bet_picks)}")
            lines.append("")
        
        # Get game info for all picks
        game_ids = [p.game_id for p in approved_picks if p.game_id]
        game_info_map = self._get_game_info_map(game_ids) if game_ids else {}
        
        # Detailed picks with rationale - show all picks
        lines.append("🎯 ALL PICKS WITH RATIONALE")
        lines.append("-" * 80)
        lines.append("")
        
        # Sort picks: best bets first, then by units
        sorted_picks = sorted(approved_picks, key=lambda p: (not p.best_bet, -p.stake_units))
        
        for i, pick in enumerate(sorted_picks, 1):
            # Get game info
            game_info = game_info_map.get(pick.game_id, {})
            team1 = game_info.get('team1', 'Team 1')
            team2 = game_info.get('team2', 'Team 2')
            
            # Format pick display with team names
            pick_data = {
                'game_id': pick.game_id,
                'bet_type': pick.bet_type.value,
                'selection': pick.selection_text or '',
                'line': pick.line,
                'odds': pick.odds
            }
            pick_display = self._format_pick_with_teams(pick_data, game_info_map, modeler_output)
            
            # Mark best bets
            best_bet_marker = " ⭐ BEST BET" if pick.best_bet else ""
            lines.append(f"PICK #{i}{best_bet_marker}")
            lines.append(f"  {pick_display}")
            
            # Get predicted score
            predicted_score = None
            if modeler_output:
                game_prediction = None
                for pred in modeler_output.get('game_models', []):
                    if str(pred.get('game_id')) == str(pick.game_id):
                        game_prediction = pred
                        break
                
                if game_prediction:
                    predicted_score_dict = game_prediction.get("predicted_score")
                    if predicted_score_dict:
                        away_score = predicted_score_dict.get('away_score')
                        home_score = predicted_score_dict.get('home_score')
                        if away_score is not None and home_score is not None:
                            # Format as "Away 84 - Home 78" for clarity
                            predicted_score = f"{team2} {away_score:.0f} - {team1} {home_score:.0f}"
            
            if predicted_score:
                lines.append(f"  Projected score: {predicted_score}")
            
            lines.append("")
            
            # Additional details (keep for reference but less prominent)
            lines.append(f"  Details: {pick.bet_type.value.upper()}")
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
                lines.append("  💼 PRESIDENT'S ANALYSIS:")
                # Split reasoning into bullet points if it contains multiple sentences
                reasoning_lines = president_reasoning.split('. ')
                for reasoning_line in reasoning_lines:
                    if reasoning_line.strip():
                        lines.append(f"    • {reasoning_line.strip()}")
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
                    # Check new schema first, fallback to old for compatibility
                    injuries = game_insights.get('injuries') or game_insights.get('key_injuries', [])
                    if injuries:
                        lines.append("    Injuries:")
                        for injury in injuries[:3]:  # Limit to 3 most important
                            if isinstance(injury, dict):
                                player = injury.get('player', 'Unknown')
                                status = injury.get('status', 'Unknown')
                                notes = injury.get('notes', '')
                                lines.append(f"      - {player} ({status}): {notes}")
                            else:
                                lines.append(f"      - {injury}")
                    recent = game_insights.get('recent') or {}
                    if recent:
                        away_rec = recent.get('away', {}).get('rec', '')
                        home_rec = recent.get('home', {}).get('rec', '')
                        if away_rec or home_rec:
                            lines.append(f"    Recent Form: Away {away_rec}, Home {home_rec}")
                    elif game_insights.get('recent_form_summary'):
                        lines.append(f"    Recent Form: {game_insights['recent_form_summary']}")
                    context = game_insights.get('context') or game_insights.get('notable_context', [])
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
                    elif pick.bet_type.value == 'total' and ('total' in predictions or 'total_details' in predictions):
                        # Handle both formats: total as float or total_details as dict
                        total_pred = predictions.get('total_details') or predictions.get('total')
                        if isinstance(total_pred, dict):
                            lines.append(f"    Projected Total: {total_pred.get('projected_total', 'N/A')}")
                            lines.append(f"    Model Confidence: {total_pred.get('model_confidence', 0):.1%}")
                        elif isinstance(total_pred, (int, float)):
                            lines.append(f"    Projected Total: {total_pred}")
                            lines.append(f"    Model Confidence: {predictions.get('confidence', 0):.1%}")
                    elif pick.bet_type.value == 'moneyline' and 'moneyline' in predictions:
                        ml_pred = predictions['moneyline']
                        # Handle both formats: team_probabilities dict or direct away_win_probability/home_win_probability
                        probs = ml_pred.get('team_probabilities', {})
                        if not probs:
                            # Try direct keys format
                            away_prob = ml_pred.get('away_win_probability')
                            home_prob = ml_pred.get('home_win_probability')
                            if away_prob is not None or home_prob is not None:
                                probs = {
                                    'away': away_prob if away_prob is not None else 0,
                                    'home': home_prob if home_prob is not None else 0
                                }
                        lines.append(f"    Win Probabilities: {probs}")
                        lines.append(f"    Model Confidence: {ml_pred.get('model_confidence', 0):.1%}")
                    
                    # Market edges
                    edges = game_prediction.get('market_edges', [])
                    for edge in edges:
                        if edge.get('market_type') == pick.bet_type.value:
                            edge_val = edge.get('edge', 0) or 0
                            edge_conf = edge.get('edge_confidence', 0) or 0
                            lines.append(f"    Edge: {edge_val:.3f}")
                            lines.append(f"    Edge Confidence: {edge_conf:.1%}")
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
                        from src.data.storage import TeamModel
                        from sqlalchemy.orm import aliased
                        team1_alias = aliased(TeamModel)
                        team2_alias = aliased(TeamModel)
                        games = session.query(
                            GameModel,
                            team1_alias.normalized_team_name.label('team1_name'),
                            team2_alias.normalized_team_name.label('team2_name')
                        ).join(
                            team1_alias, GameModel.team1_id == team1_alias.id
                        ).join(
                            team2_alias, GameModel.team2_id == team2_alias.id
                        ).filter(GameModel.id.in_(game_ids)).all()
                        
                        for game, team1_name, team2_name in games:
                            game_info_map[game.id] = {
                                'team1': team1_name,
                                'team2': team2_name,
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
                lines.append("=" * 70)
                game_id = game.get("game_id", "N/A")
                teams = game.get("teams", {})
                away = teams.get("away", "N/A")
                home = teams.get("home", "N/A")
                lines.append(f"  Game ID: {game_id}")
                lines.append(f"  Matchup: {away} @ {home}")
                lines.append(f"  League: {game.get('league', 'N/A')}")
                lines.append(f"  Start Time: {game.get('start_time', 'N/A')}")
                
                # Market Data
                market = game.get("market", {})
                if market:
                    lines.append("")
                    lines.append("  📈 MARKET DATA:")
                    if market.get("spread"):
                        lines.append(f"    Spread: {market['spread']}")
                    if market.get("total"):
                        lines.append(f"    Total: {market['total']}")
                    if market.get("moneyline"):
                        ml = market["moneyline"]
                        lines.append(f"    Moneyline: Away {ml.get('away', 'N/A')} / Home {ml.get('home', 'N/A')}")
                
                # Advanced Stats (KenPom/Torvik) - THE KEY DATA
                adv = game.get("adv", {})
                if adv and not adv.get("data_unavailable"):
                    lines.append("")
                    lines.append("  📊 ADVANCED STATS (KenPom/Torvik):")
                    
                    # Away team stats
                    away_stats = adv.get("away", {})
                    if away_stats:
                        lines.append(f"    {away} (Away):")
                        if away_stats.get("kp_rank"):
                            lines.append(f"      KenPom Rank: #{away_stats['kp_rank']}")
                        if away_stats.get("torvik_rank"):
                            lines.append(f"      Torvik Rank: #{away_stats['torvik_rank']}")
                        if away_stats.get("adjo") is not None:
                            lines.append(f"      AdjO (Offense): {away_stats['adjo']:.1f}")
                        if away_stats.get("adjd") is not None:
                            lines.append(f"      AdjD (Defense): {away_stats['adjd']:.1f}")
                        if away_stats.get("adjt") is not None:
                            lines.append(f"      AdjT (Tempo): {away_stats['adjt']:.1f}")
                        if away_stats.get("net") is not None:
                            lines.append(f"      Net Rating: {away_stats['net']:+.1f}")
                        if away_stats.get("conference"):
                            lines.append(f"      Conference: {away_stats['conference']}")
                        if away_stats.get("w_l"):
                            lines.append(f"      Record: {away_stats['w_l']}")
                        elif away_stats.get("wins") is not None and away_stats.get("losses") is not None:
                            lines.append(f"      Record: {away_stats['wins']}-{away_stats['losses']}")
                        if away_stats.get("sos") is not None:
                            lines.append(f"      SOS (Strength of Schedule): {away_stats['sos']:.1f}")
                        if away_stats.get("ncsos") is not None:
                            lines.append(f"      NCSOS (Non-Conf SOS): {away_stats['ncsos']:.1f}")
                        if away_stats.get("luck") is not None:
                            lines.append(f"      Luck: {away_stats['luck']:+.1f}")
                    
                    # Home team stats
                    home_stats = adv.get("home", {})
                    if home_stats:
                        lines.append(f"    {home} (Home):")
                        if home_stats.get("kp_rank"):
                            lines.append(f"      KenPom Rank: #{home_stats['kp_rank']}")
                        if home_stats.get("torvik_rank"):
                            lines.append(f"      Torvik Rank: #{home_stats['torvik_rank']}")
                        if home_stats.get("adjo") is not None:
                            lines.append(f"      AdjO (Offense): {home_stats['adjo']:.1f}")
                        if home_stats.get("adjd") is not None:
                            lines.append(f"      AdjD (Defense): {home_stats['adjd']:.1f}")
                        if home_stats.get("adjt") is not None:
                            lines.append(f"      AdjT (Tempo): {home_stats['adjt']:.1f}")
                        if home_stats.get("net") is not None:
                            lines.append(f"      Net Rating: {home_stats['net']:+.1f}")
                        if home_stats.get("conference"):
                            lines.append(f"      Conference: {home_stats['conference']}")
                        if home_stats.get("w_l"):
                            lines.append(f"      Record: {home_stats['w_l']}")
                        elif home_stats.get("wins") is not None and home_stats.get("losses") is not None:
                            lines.append(f"      Record: {home_stats['wins']}-{home_stats['losses']}")
                        if home_stats.get("sos") is not None:
                            lines.append(f"      SOS (Strength of Schedule): {home_stats['sos']:.1f}")
                        if home_stats.get("ncsos") is not None:
                            lines.append(f"      NCSOS (Non-Conf SOS): {home_stats['ncsos']:.1f}")
                        if home_stats.get("luck") is not None:
                            lines.append(f"      Luck: {home_stats['luck']:+.1f}")
                    
                    # Matchup analysis
                    matchup = adv.get("matchup", [])
                    if matchup:
                        lines.append("    Matchup Analysis:")
                        for m in matchup:
                            lines.append(f"      • {m}")
                elif adv.get("data_unavailable"):
                    lines.append("")
                    lines.append("  📊 ADVANCED STATS: Data unavailable")
                
                # Injuries
                injuries = game.get("injuries") or game.get("key_injuries", [])
                if injuries:
                    lines.append("")
                    lines.append(f"  🏥 INJURIES ({len(injuries)}):")
                    for injury in injuries:
                        if isinstance(injury, dict):
                            team = injury.get('team', '')
                            player = injury.get('player', 'Unknown')
                            pos = injury.get('pos', '')
                            status = injury.get('status', 'Unknown')
                            notes = injury.get('notes', '')
                            pos_str = f" ({pos})" if pos else ""
                            team_str = f"[{team}] " if team else ""
                            lines.append(f"    - {team_str}{player}{pos_str}: {status}" + (f" - {notes}" if notes else ""))
                        else:
                            lines.append(f"    - {injury}")
                
                # Recent Form
                recent = game.get("recent") or {}
                if recent:
                    lines.append("")
                    lines.append("  📅 RECENT FORM:")
                    away_recent = recent.get('away', {})
                    home_recent = recent.get('home', {})
                    
                    if away_recent:
                        rec = away_recent.get('rec', 'N/A')
                        notes = away_recent.get('notes', '')
                        pace_trend = away_recent.get('pace_trend', '')
                        last_3_avg = away_recent.get('last_3_avg_score')
                        lines.append(f"    {away} (Away): {rec}")
                        if notes:
                            lines.append(f"      Notes: {notes}")
                        if pace_trend:
                            lines.append(f"      Pace Trend: {pace_trend}")
                        if last_3_avg:
                            lines.append(f"      Last 3 Avg Score: {last_3_avg:.1f}")
                    
                    if home_recent:
                        rec = home_recent.get('rec', 'N/A')
                        notes = home_recent.get('notes', '')
                        pace_trend = home_recent.get('pace_trend', '')
                        last_3_avg = home_recent.get('last_3_avg_score')
                        lines.append(f"    {home} (Home): {rec}")
                        if notes:
                            lines.append(f"      Notes: {notes}")
                        if pace_trend:
                            lines.append(f"      Pace Trend: {pace_trend}")
                        if last_3_avg:
                            lines.append(f"      Last 3 Avg Score: {last_3_avg:.1f}")
                elif game.get("recent_form_summary"):
                    lines.append("")
                    lines.append(f"  📅 RECENT FORM: {game['recent_form_summary']}")
                
                # Expert Predictions
                experts = game.get("experts", {})
                if experts and (experts.get("src") or experts.get("scores") or experts.get("reason") or experts.get("spread_pick") or experts.get("total_pick")):
                    lines.append("")
                    lines.append("  🎯 EXPERT PREDICTIONS:")
                    if experts.get("src"):
                        lines.append(f"    Sources Consulted: {experts['src']}")
                    # New format: spread_pick as string (e.g., "Kentucky -4.5")
                    if experts.get("spread_pick"):
                        lines.append(f"    Consensus Spread Pick: {experts['spread_pick']}")
                    # Legacy format: home_spread as count (deprecated)
                    elif experts.get("home_spread") is not None:
                        try:
                            # If it's a number, show as count
                            home_spread = float(experts['home_spread'])
                            lines.append(f"    Expert Spread Votes: {int(home_spread)} for home")
                        except (ValueError, TypeError):
                            # If it's a string, show as-is
                            lines.append(f"    Consensus Spread Pick: {experts['home_spread']}")
                    # New format: total_pick as string (e.g., "Over 153.5")
                    if experts.get("total_pick"):
                        lines.append(f"    Consensus Total Pick: {experts['total_pick']}")
                    # Legacy format: lean_total
                    elif experts.get("lean_total"):
                        lines.append(f"    Total Lean: {experts['lean_total'].upper()}")
                    if experts.get("scores"):
                        lines.append(f"    Predicted Scores: {', '.join(experts['scores'])}")
                    if experts.get("reason"):
                        lines.append(f"    Key Reasoning: {experts['reason']}")
                
                # Common Opponents
                common_opp = game.get("common_opp", [])
                if common_opp:
                    lines.append("")
                    lines.append("  🔄 COMMON OPPONENTS:")
                    for opp in common_opp:
                        lines.append(f"    • {opp}")
                
                # Notable Context
                context = game.get("context") or game.get("notable_context", [])
                if context:
                    lines.append("")
                    lines.append(f"  📝 CONTEXT ({len(context)}):")
                    for ctx in context:
                        lines.append(f"    • {ctx}")
                
                # Data Quality Notes
                dq = game.get("dq") or []
                if dq:
                    lines.append("")
                    lines.append(f"  ⚠️  DATA QUALITY NOTES ({len(dq)}):")
                    for note in dq:
                        lines.append(f"    • {note}")
                elif game.get("data_quality_notes"):
                    lines.append("")
                    lines.append(f"  ⚠️  DATA QUALITY: {game['data_quality_notes']}")
                
                lines.append("")
                lines.append("-" * 70)
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
                        # Format spread line from away_line, home_line, or projected_margin
                        projected_margin = spread.get('projected_margin')
                        away_line = spread.get('away_line')
                        home_line = spread.get('home_line')
                        projected_line = spread.get('projected_line')  # Check for this key too
                        
                        # Determine spread line format - prioritize projected_line if available
                        # Convert to float if string (API sometimes returns strings)
                        def to_float(val):
                            if val is None:
                                return None
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return None
                        
                        away_line_f = to_float(away_line)
                        home_line_f = to_float(home_line)
                        projected_margin_f = to_float(projected_margin)
                        
                        if projected_line is not None:
                            spread_line = str(projected_line)
                        elif away_line_f is not None:
                            spread_line = f"Away {away_line_f:+.1f}"
                        elif home_line_f is not None:
                            spread_line = f"Home {home_line_f:+.1f}"
                        elif projected_margin_f is not None:
                            spread_line = f"{projected_margin_f:+.1f}"
                        else:
                            spread_line = "N/A"
                        
                        margin_display = f"{projected_margin_f:.1f}" if projected_margin_f is not None else "N/A"
                        # If we have margin but no explicit line, just show margin
                        if spread_line == "N/A" and projected_margin is not None:
                            lines.append(f"    Spread: {margin_display}")
                        else:
                            lines.append(f"    Spread: {spread_line} (Margin: {margin_display})")
                        
                        # Get confidence - check both 'confidence' and 'model_confidence' for backward compatibility
                        spread_confidence = to_float(spread.get('confidence') or spread.get('model_confidence', 0)) or 0
                        lines.append(f"    Confidence: {spread_confidence:.1%}")
                    # Check for total - handle both formats:
                    # 1. predictions.total as dict with projected_total (old format)
                    # 2. predictions.total as float + predictions.total_details as dict (new format)
                    total_data = predictions.get("total_details") or predictions.get("total")
                    if total_data is not None:
                        # Helper to safely convert to float
                        def to_float_total(val):
                            if val is None:
                                return None
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return None
                        
                        if isinstance(total_data, dict):
                            projected_total = total_data.get('projected_total', 'N/A')
                            total_confidence = to_float_total(total_data.get('confidence') or total_data.get('model_confidence', 0)) or 0
                        elif isinstance(total_data, (int, float)):
                            projected_total = total_data
                            total_confidence = to_float_total(predictions.get('confidence', 0)) or 0
                        else:
                            projected_total = 'N/A'
                            total_confidence = 0
                        lines.append(f"    Total: {projected_total}")
                        lines.append(f"    Confidence: {total_confidence:.1%}")
                    if "moneyline" in predictions:
                        ml = predictions["moneyline"]
                        # Handle multiple formats for backward compatibility
                        away_prob = None
                        home_prob = None
                        
                        # Try direct keys first (current format)
                        if 'away_win_prob' in ml:
                            away_prob = ml.get('away_win_prob')
                            home_prob = ml.get('home_win_prob')
                        
                        # Try alternative key names
                        if away_prob is None:
                            away_prob = ml.get('away_win_probability')
                        if home_prob is None:
                            home_prob = ml.get('home_win_probability')
                        
                        # Try team_probabilities dict format (old format)
                        if away_prob is None:
                            probs = ml.get("team_probabilities", {})
                            if isinstance(probs, dict):
                                away_prob = probs.get('away')
                                home_prob = probs.get('home')
                        
                        # Default to 0 if still not found, ensuring float type
                        def to_float_ml(val):
                            if val is None:
                                return 0
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return 0
                        
                        away_prob = to_float_ml(away_prob)
                        home_prob = to_float_ml(home_prob)
                        
                        lines.append(f"    Moneyline Probabilities: Away {away_prob:.1%} / Home {home_prob:.1%}")
                        
                        # Get confidence - check both 'confidence' and 'model_confidence' for backward compatibility
                        ml_confidence = to_float_ml(ml.get('confidence') or ml.get('model_confidence', 0))
                        lines.append(f"    Confidence: {ml_confidence:.1%}")
                
                # Helper to safely convert to float for scores/edges
                def safe_float(val, default=0):
                    if val is None:
                        return None if default is None else default
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        return default
                
                predicted_score = model.get("predicted_score", {})
                if predicted_score:
                    away_score = safe_float(predicted_score.get('away_score'), None)
                    home_score = safe_float(predicted_score.get('home_score'), None)
                    if away_score is not None and home_score is not None:
                        lines.append(f"  Predicted Score: Away {away_score:.1f} - Home {home_score:.1f}")
                
                edges = model.get("market_edges", [])
                if edges:
                    lines.append(f"  Market Edges ({len(edges)}):")
                    for edge in edges:
                        edge_value = safe_float(edge.get('edge'), None)
                        edge_str = f"{edge_value:.3f}" if edge_value is not None else "N/A"
                        edge_confidence = safe_float(edge.get('edge_confidence', 0))
                        model_prob = safe_float(edge.get('model_estimated_probability', 0))
                        implied_prob = safe_float(edge.get('implied_probability', 0))
                        lines.append(f"    {edge.get('market_type', 'N/A').upper()}: Edge {edge_str} "
                                   f"(Confidence: {edge_confidence:.1%})")
                        lines.append(f"      Market Line: {edge.get('market_line', 'N/A')}")
                        lines.append(f"      Model Prob: {model_prob:.1%} "
                                   f"vs Implied: {implied_prob:.1%}")
                
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
                edge_est = pick.get('edge_estimate', 0) or 0
                # Get confidence - handle both confidence_score (1-10) and confidence (0.0-1.0)
                conf_raw = pick.get('confidence', 0) or 0
                confidence_score = pick.get('confidence_score')
                
                # If confidence_score is provided, use it and convert to 0.0-1.0
                if confidence_score is not None:
                    conf = max(0.0, min(1.0, float(confidence_score) / 10.0))
                elif conf_raw > 1.0:
                    # confidence is likely a confidence_score (1-10), convert to 0.0-1.0
                    conf = max(0.0, min(1.0, float(conf_raw) / 10.0))
                else:
                    # confidence is already in 0.0-1.0 range
                    conf = max(0.0, min(1.0, float(conf_raw)))
                
                lines.append(f"  Edge Estimate: {edge_est:.3f}")
                lines.append(f"  Confidence: {conf:.1%}")
                
                justification = pick.get("justification", [])
                if justification:
                    lines.append("  Justification:")
                    for j in justification:
                        lines.append(f"    - {j}")
                
                if pick.get("notes"):
                    lines.append(f"  Notes: {pick['notes']}")
                
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
            
            # Get game info for all picks
            all_game_ids = []
            for pick in approved_picks + rejected_picks:
                game_id = pick.get('game_id')
                if game_id:
                    try:
                        all_game_ids.append(int(game_id) if isinstance(game_id, str) and game_id.isdigit() else game_id)
                    except (ValueError, TypeError):
                        pass
            
            game_info_map = self._get_game_info_map(all_game_ids) if all_game_ids else {}
            
            # Get modeler output if available (for predicted scores)
            modeler_output = None  # Will be passed if available in future
            
            if approved_picks:
                lines.append("")
                lines.append("APPROVED PICKS:")
                for i, pick in enumerate(approved_picks, 1):
                    # Format with team names
                    pick_display = self._format_pick_with_teams(pick, game_info_map, modeler_output)
                    units = pick.get('units', 0)
                    lines.append(f"  #{i}: {pick_display} - {units:.2f} units")
                    
                    # Get predicted score if available
                    game_id = pick.get('game_id')
                    predicted_score = self._get_predicted_score(game_id, modeler_output, game_info_map)
                    if predicted_score:
                        lines.append(f"    Projected score: {predicted_score}")
                    
                    if pick.get("final_decision_reasoning"):
                        lines.append(f"    Reasoning: {pick['final_decision_reasoning']}")
            
            if rejected_picks:
                lines.append("")
                lines.append("REJECTED PICKS:")
                for i, pick in enumerate(rejected_picks, 1):
                    # Format with team names
                    pick_display = self._format_pick_with_teams(pick, game_info_map, modeler_output)
                    lines.append(f"  #{i}: {pick_display}")
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
                    edge_est = bet.get('edge_estimate', 0) or 0
                    conf = bet.get('confidence', 0) or 0
                    lines.append(f"    Edge Estimate: {edge_est:.3f}")
                    lines.append(f"    Confidence: {conf:.1%}")
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

