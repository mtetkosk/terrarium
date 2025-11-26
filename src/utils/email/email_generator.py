"""Email generator for daily betting newsletter"""

import json
import os
import re
import smtplib
from datetime import date, datetime, timedelta
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from src.agents.results_processor import ResultsProcessor
from src.data.analytics import AnalyticsService
from src.data.models import BetResult, BetType
from src.data.storage import BetModel, Database, GameModel, PickModel, GameInsightModel, PredictionModel, BettingLineModel, TeamModel
from src.prompts import EMAIL_GENERATOR_RECAP_SYSTEM_PROMPT
from src.utils.config import config
from src.utils.llm import LLMClient
from src.utils.logging import get_logger
from src.utils.team_normalizer import normalize_team_name, are_teams_matching

logger = get_logger("utils.email_generator")


class EmailGenerator:
    """Generate daily email newsletter with betting picks and results"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize email generator"""
        self.db = db
        self.results_processor = ResultsProcessor(db) if db else None
        self.analytics_service = AnalyticsService(db) if db else None
        self.reports_dir = Path("data/reports")
        # Initialize LLM client for recap generation
        try:
            self.llm_client = LLMClient(model="gpt-5.1")
        except Exception as e:
            logger.warning(f"Could not initialize LLM client: {e}. Recap generation will be disabled.")
            self.llm_client = None
    
    def _get_team_name(self, team_id: Optional[int], session) -> str:
        """Get official team name from database using team_id"""
        if not team_id or not session:
            return "Team"
        try:
            team = session.query(TeamModel).filter_by(id=team_id).first()
            if team:
                return team.normalized_team_name
        except Exception as e:
            logger.debug(f"Error getting team name for team_id {team_id}: {e}")
        return "Team"
    
    def generate_email(
        self,
        target_date: Optional[date] = None,
        recipient_name: str = "Friends",
        format_html: bool = True
    ) -> Tuple[str, str]:
        """
        Generate email content for a given date
        
        Args:
            target_date: Date to generate email for (default: today)
            recipient_name: Name to address email to
            format_html: If True, returns HTML formatted email, otherwise plain text
            
        Returns:
            Tuple of (subject, email_content) where email_content is HTML or plain text
        """
        if target_date is None:
            target_date = date.today()
        
        yesterday = target_date - timedelta(days=1)
        
        # Get yesterday's results (for games that happened on yesterday)
        # results_processor.process() takes target_date and processes bets from target_date - 1
        # So to get results for games on yesterday, we pass target_date (today)
        yesterday_results = self._get_yesterday_results(target_date)
        
        # Get today's presidents report
        today_presidents_report = self._get_today_presidents_report(target_date)
        
        # Get game information
        today_games = self._get_today_games(target_date)
        yesterday_games = self._get_yesterday_games(yesterday)
        
        subject = f"Daily Betting Picks - {target_date.strftime('%B %d, %Y')}"
        
        if format_html:
            email_content = self._generate_html_email(
                target_date, yesterday, recipient_name,
                yesterday_results, today_presidents_report, today_games, yesterday_games
            )
        else:
            email_content = self._generate_plain_text_email(
                target_date, yesterday, recipient_name,
                yesterday_results, today_presidents_report, today_games, yesterday_games
            )
        
        return subject, email_content
    
    def _generate_html_email(
        self,
        target_date: date,
        yesterday: date,
        recipient_name: str,
        yesterday_results: Optional[Dict[str, Any]],
        today_presidents_report: Optional[str],
        today_games: List[Dict[str, Any]],
        yesterday_games: List[Dict[str, Any]]
    ) -> str:
        """Generate HTML formatted email"""
        html_parts = []
        
        # HTML header with styles
        html_parts.append("""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 700px;
            margin: 0 auto;
            padding: 20px;
            background-color: #ffffff;
        }
        h2 {
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 5px;
            margin-top: 25px;
            margin-bottom: 15px;
        }
        .section {
            margin-bottom: 25px;
        }
        .bet-item {
            margin-bottom: 15px;
            padding: 12px 15px;
            background-color: #f8f9fa;
            border-left: 4px solid #3498db;
            border-radius: 5px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            transition: box-shadow 0.2s;
        }
        .bet-item:hover {
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
        }
        .bet-selection {
            font-weight: bold;
            color: #2c3e50;
            font-size: 1.05em;
        }
        .bet-rationale {
            font-style: italic;
            color: #555;
            margin-top: 5px;
            padding-left: 10px;
        }
        .performance {
            font-size: 1.1em;
            padding: 15px;
            background-color: #f0f4f8;
            border-radius: 6px;
            border-left: 4px solid #3498db;
            font-weight: 500;
            line-height: 1.8;
        }
        .superlative {
            margin: 8px 0;
            padding-left: 10px;
        }
        .footer {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            color: #777;
            font-size: 0.9em;
        }
        .emoji {
            font-size: 1.2em;
        }
        .recap-content {
            background-color: #f8f9fa;
            padding: 18px 20px;
            border-radius: 6px;
            border-left: 4px solid #3498db;
            line-height: 1.8;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .recap-content p {
            margin: 12px 0;
            color: #444;
        }
        .recap-content ul, .recap-content ol {
            margin: 12px 0;
            padding-left: 30px;
        }
        .recap-content li {
            margin: 8px 0;
            color: #444;
        }
        .recap-content strong {
            color: #2c3e50;
            font-weight: 600;
        }
        .recap-content br {
            line-height: 1.8;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background-color: #ffffff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border-radius: 6px;
            overflow: hidden;
        }
        thead {
            background-color: #3498db;
            color: #ffffff;
        }
        th {
            padding: 12px 15px;
            text-align: left;
            font-weight: 600;
            font-size: 0.95em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        tbody tr {
            border-bottom: 1px solid #e0e0e0;
            transition: background-color 0.2s;
        }
        tbody tr:hover {
            background-color: #f8f9fa;
        }
        tbody tr:last-child {
            border-bottom: none;
        }
        td {
            padding: 12px 15px;
            vertical-align: top;
        }
        .confidence-col {
            text-align: center;
            font-weight: 600;
            color: #2c3e50;
        }
        .best-bet {
            color: #e74c3c;
            font-weight: 600;
        }
        .best-bet-row {
            background-color: #fff9e6 !important;
            border-left: 3px solid #f39c12 !important;
        }
        .best-bet-row:hover {
            background-color: #fff3cd !important;
        }
        .matchup-col {
            font-weight: 500;
            color: #2c3e50;
        }
        .selection-col {
            color: #555;
        }
        .odds-col {
            color: #666;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body>""")
        
        # Greeting
        html_parts.append(f'<p style="font-size: 1.1em;">Hey {recipient_name}! 👋</p>')
        html_parts.append('<p style="color: #555; margin-top: -10px;">Ready for another day of action? Let\'s dive in.</p>')
        
        # Best Games to Watch
        best_games = self._get_best_games_to_watch(target_date)
        if best_games:
            html_parts.append('<div class="section">')
            html_parts.append('<h2><span class="emoji">🎯</span> BEST GAMES TO WATCH</h2>')
            for game in best_games:
                html_parts.append('<div class="bet-item" style="border-left-color: #9b59b6; background-color: #f4f1f8;">')
                html_parts.append(f'<div class="bet-selection" style="font-size: 1.05em; color: #9b59b6;"><strong>{game["matchup"]}</strong></div>')
                if game.get('venue'):
                    html_parts.append(f'<div style="margin-top: 5px; color: #666; font-size: 0.9em;">📍 {game["venue"]}</div>')
                if game.get('description'):
                    html_parts.append(f'<div style="margin-top: 8px; color: #444; line-height: 1.6; font-size: 0.95em;">{game["description"]}</div>')
                html_parts.append('</div>')
            html_parts.append('</div>')
        
        # 2. Yesterday's betting performance
        if yesterday_results:
            performance_text = self._format_yesterday_performance(yesterday_results)
            if performance_text:
                html_parts.append('<div class="section">')
                html_parts.append('<h2><span class="emoji">📊</span> YESTERDAY\'S PERFORMANCE</h2>')
                html_parts.append(f'<div class="performance">{performance_text}</div>')
                html_parts.append('</div>')
                
                # 2.5. Yesterday's Superlatives
                superlatives = self._generate_superlatives(yesterday_games, yesterday_results)
                if superlatives:
                    html_parts.append('<div class="section">')
                    html_parts.append('<h2><span class="emoji">🏆</span> YESTERDAY\'S HIGHLIGHTS</h2>')
                    for key, value in superlatives.items():
                        html_parts.append('<div class="superlative">')
                        html_parts.append(f'<strong>{key}:</strong> {value}')
                        html_parts.append('</div>')
                    html_parts.append('</div>')
        
        # 3. Underdog of the Day (if available)
        underdog = self._extract_underdog_of_the_day(target_date, today_presidents_report)
        if underdog:
            html_parts.append('<div class="section">')
            html_parts.append('<h2><span class="emoji">🐕</span> UNDERDOG OF THE DAY</h2>')
            html_parts.append('<div class="bet-item" style="border-left-color: #e74c3c; background-color: #fdf2f2;">')
            
            matchup = underdog.get('matchup', '')
            selection = underdog.get('selection', '')
            odds = underdog.get('odds', '')
            reasoning = underdog.get('reasoning', '')
            model_projection = underdog.get('model_projection', '')
            
            html_parts.append(f'<div class="bet-selection" style="font-size: 1.1em; color: #e74c3c;">{matchup} - <strong>{selection}</strong>')
            if odds:
                html_parts[-1] += f" ({odds})"
            html_parts[-1] += '</div>'
            
            if model_projection:
                html_parts.append(f'<div style="margin-top: 8px; font-weight: 600;">📊 Model Projection: {model_projection}</div>')
            
            if reasoning:
                html_parts.append(f'<div class="bet-rationale">💡 {reasoning}</div>')
            
            html_parts.append('</div>')
            html_parts.append('</div>')
        
        # 4. All picks ordered by confidence
        all_picks = self._get_today_picks_ordered_by_confidence(target_date)
        if all_picks:
            html_parts.append('<div class="section">')
            html_parts.append('<h2><span class="emoji">⭐</span> TODAY\'S PICKS</h2>')
            html_parts.append('<table>')
            html_parts.append('<thead>')
            html_parts.append('<tr>')
            html_parts.append('<th style="width: 5%;">#</th>')
            html_parts.append('<th style="width: 40%;">Matchup</th>')
            html_parts.append('<th style="width: 35%;">Selection</th>')
            html_parts.append('<th style="width: 10%;">Odds</th>')
            html_parts.append('<th style="width: 10%;">Confidence</th>')
            html_parts.append('</tr>')
            html_parts.append('</thead>')
            html_parts.append('<tbody>')
            
            for i, pick_data in enumerate(all_picks, 1):
                matchup = pick_data.get('matchup', '')
                selection = pick_data.get('selection', '')
                odds = pick_data.get('odds', '')
                rationale = pick_data.get('rationale', '')
                confidence = pick_data.get('confidence_score', 5)
                is_best_bet = pick_data.get('best_bet', False)
                
                # Highlight best bets with special styling
                row_style = 'background-color: #fff9e6; border-left: 3px solid #f39c12;' if is_best_bet else ''
                html_parts.append(f'<tr style="{row_style}">')
                html_parts.append(f'<td>{i}</td>')
                html_parts.append(f'<td class="matchup-col">{matchup}</td>')
                if is_best_bet:
                    selection_display = f'<strong style="color: #e67e22;">{selection} ⭐ BEST BET</strong>'
                else:
                    selection_display = selection
                html_parts.append(f'<td class="selection-col">{selection_display}</td>')
                html_parts.append(f'<td class="odds-col">{odds}</td>')
                html_parts.append(f'<td class="confidence-col">{confidence}/10</td>')
                html_parts.append('</tr>')
                
                # Add rationale row for best bets
                if rationale and is_best_bet:
                    html_parts.append('<tr>')
                    html_parts.append('<td colspan="5" style="padding-left: 30px; padding-top: 0; padding-bottom: 12px; color: #555; font-style: italic; font-size: 0.9em;">')
                    html_parts.append(f'💡 {rationale}')
                    html_parts.append('</td>')
                    html_parts.append('</tr>')
            
            html_parts.append('</tbody>')
            html_parts.append('</table>')
            html_parts.append('</div>')
        
        # Footer - generate sign-off using LLM
        main_message, secondary_message = self._generate_sign_off(target_date)
        html_parts.append('<div class="footer">')
        html_parts.append(f'<p style="font-size: 1.05em; margin-top: 30px;"><strong>{main_message}</strong></p>')
        html_parts.append(f'<p style="font-size: 0.9em; color: #888; margin-top: 10px;">{secondary_message}</p>')
        html_parts.append(f'<p style="font-size: 0.85em; color: #999; margin-top: 15px;">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>')
        html_parts.append('</div>')
        
        html_parts.append('</body></html>')
        
        return '\n'.join(html_parts)
    
    def _generate_plain_text_email(
        self,
        target_date: date,
        yesterday: date,
        recipient_name: str,
        yesterday_results: Optional[Dict[str, Any]],
        today_presidents_report: Optional[str],
        today_games: List[Dict[str, Any]],
        yesterday_games: List[Dict[str, Any]]
    ) -> str:
        """Generate plain text email (for file saving)"""
        email_lines = []
        
        email_lines.append(f"Hey {recipient_name}! 👋")
        email_lines.append("Ready for another day of action? Let's dive in.")
        email_lines.append("")
        
        # Best Games to Watch
        best_games = self._get_best_games_to_watch(target_date)
        if best_games:
            email_lines.append("🎯 BEST GAMES TO WATCH")
            email_lines.append("-" * 60)
            for game in best_games:
                email_lines.append(f"  {game['matchup']}")
                if game.get('venue'):
                    email_lines.append(f"    📍 {game['venue']}")
                if game.get('description'):
                    # Wrap description nicely
                    desc = game['description']
                    if len(desc) > 70:
                        # Try to break at sentence boundaries
                        words = desc.split()
                        lines = []
                        current_line = "    "
                        for word in words:
                            if len(current_line + word) > 70 and current_line.strip() != "":
                                lines.append(current_line)
                                current_line = "    " + word + " "
                            else:
                                current_line += word + " "
                        if current_line.strip():
                            lines.append(current_line)
                        email_lines.extend(lines)
                    else:
                        email_lines.append(f"    {desc}")
                email_lines.append("")
        
        # 2. Yesterday's betting performance
        if yesterday_results:
            performance_text = self._format_yesterday_performance_plain(yesterday_results)
            if performance_text:
                email_lines.append("📊 YESTERDAY'S PERFORMANCE")
                email_lines.append("-" * 60)
                email_lines.append(performance_text)
                email_lines.append("")
                
                # 2.5. Yesterday's Superlatives
                superlatives = self._generate_superlatives(yesterday_games, yesterday_results)
                if superlatives:
                    email_lines.append("🏆 YESTERDAY'S HIGHLIGHTS")
                    email_lines.append("-" * 60)
                    for key, value in superlatives.items():
                        email_lines.append(f"  {key}: {value}")
                    email_lines.append("")
        
        # 3. Underdog of the Day (if available)
        underdog = self._extract_underdog_of_the_day(target_date, today_presidents_report)
        if underdog:
            email_lines.append("🐕 UNDERDOG OF THE DAY")
            email_lines.append("-" * 60)
            
            matchup = underdog.get('matchup', '')
            selection = underdog.get('selection', '')
            odds = underdog.get('odds', '')
            reasoning = underdog.get('reasoning', '')
            model_projection = underdog.get('model_projection', '')
            
            bet_line = f"{matchup} - {selection}"
            if odds:
                bet_line += f" ({odds})"
            email_lines.append(bet_line)
            
            if model_projection:
                email_lines.append(f"   📊 Model Projection: {model_projection}")
            
            if reasoning:
                email_lines.append(f"   💡 {reasoning}")
            
            email_lines.append("")
        
        # 4. All picks ordered by confidence
        all_picks = self._get_today_picks_ordered_by_confidence(target_date)
        if all_picks:
            email_lines.append("⭐ TODAY'S PICKS")
            email_lines.append("=" * 80)
            
            # Table header
            header = f"{'#':<4} {'Matchup':<40} {'Selection':<30} {'Odds':<8} {'Conf':<6}"
            email_lines.append(header)
            email_lines.append("-" * 88)
            
            # Table rows
            for i, pick_data in enumerate(all_picks, 1):
                matchup = pick_data.get('matchup', '')
                selection = pick_data.get('selection', '')
                odds = pick_data.get('odds', '')
                rationale = pick_data.get('rationale', '')
                confidence = pick_data.get('confidence_score', 5)
                is_best_bet = pick_data.get('best_bet', False)
                
                # Truncate long matchups/selections for table format
                matchup_short = (matchup[:38] + '..') if len(matchup) > 40 else matchup
                selection_short = (selection[:28] + '..') if len(selection) > 30 else selection
                if is_best_bet:
                    selection_short = f"{selection_short} ⭐ BEST BET"
                
                row = f"{i:<4} {matchup_short:<40} {selection_short:<30} {odds:<8} {confidence}/10"
                email_lines.append(row)
                
                # Add rationale for best bets below the row
                if rationale and is_best_bet:
                    email_lines.append(f"     💡 {rationale[:70]}{'...' if len(rationale) > 70 else ''}")
            
            email_lines.append("=" * 80)
            email_lines.append("")
        
        # Footer - generate sign-off using LLM
        main_message, secondary_message = self._generate_sign_off(target_date)
        email_lines.append("-" * 60)
        email_lines.append(main_message)
        email_lines.append(secondary_message)
        email_lines.append("")
        email_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return "\n".join(email_lines)
    
    def _get_yesterday_results(self, target_date: date) -> Optional[Dict[str, Any]]:
        """
        Get yesterday's results from database using analytics service.
        
        CRITICAL: Always uses database, never parses text files.
        
        Args:
            target_date: Today's date (gets results for picks made on target_date - 1)
        """
        yesterday = target_date - timedelta(days=1)
        
        if not self.analytics_service:
            logger.warning("Analytics service not available - cannot get yesterday's results")
            return None
        
        try:
            # Get results from analytics service (database-only)
            results = self.analytics_service.get_results_for_date(yesterday)
            
            if not results or not results.get('picks'):
                return None
            
            picks = results['picks']
            bets = results['bets']
            bet_map = results.get('bet_map', {})
            stats = results['stats']
            
            # Calculate profit/loss
            total_wagered_units = sum(p.stake_units for p in picks)
            total_wagered_dollars = sum(p.stake_amount for p in picks)
            
            profit_loss_units = 0.0
            profit_loss_dollars = 0.0
            
            for pick in picks:
                bet = bet_map.get(pick.id)
                if bet:
                    if bet.result == BetResult.WIN:
                        # Calculate payout: stake * (odds/100) for positive, stake * (100/abs(odds)) for negative
                        if pick.odds > 0:
                            payout_ratio = pick.odds / 100.0
                        else:
                            payout_ratio = 100.0 / abs(pick.odds)
                        profit_loss_units += pick.stake_units * payout_ratio
                        profit_loss_dollars += pick.stake_amount * payout_ratio
                    elif bet.result == BetResult.PUSH:
                        # Return stake
                        profit_loss_units += pick.stake_units
                        profit_loss_dollars += pick.stake_amount
                    # Loss: nothing added (already wagered)
            
            # Net profit/loss
            profit_loss_units -= total_wagered_units
            profit_loss_dollars -= total_wagered_dollars
            
            # Only return results if there are actually settled bets
            settled_bets = stats.get('settled_bets', 0)
            wins = stats.get('wins', 0)
            losses = stats.get('losses', 0)
            pushes = stats.get('pushes', 0)
            
            # If no settled bets, return None (don't show performance section)
            if settled_bets == 0:
                return None
            
            return {
                'total_picks': stats['total_picks'],
                'settled_bets': settled_bets,
                'wins': wins,
                'losses': losses,
                'pushes': pushes,
                'profit_loss_units': profit_loss_units,
                'profit_loss_dollars': profit_loss_dollars,
                'total_wagered_units': total_wagered_units,
                'total_wagered_dollars': total_wagered_dollars
            }
        except Exception as e:
            logger.error(f"Error getting yesterday's results from database: {e}", exc_info=True)
            return None
    
    def _get_today_presidents_report(self, target_date: date) -> Optional[str]:
        """Get today's presidents report content (for display only, not analytics)"""
        report_file = self.reports_dir / "president" / f"presidents_report_{target_date.isoformat()}.txt"
        if report_file.exists():
            try:
                with open(report_file, 'r') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Error reading presidents report: {e}")
        return None
    
    def _get_president_response_json(self, target_date: date) -> Optional[Dict[str, Any]]:
        """Get president's raw JSON response if available"""
        # Try to find president JSON file (might be saved separately)
        json_file = self.reports_dir / "president" / f"president_{target_date.isoformat()}.json"
        if json_file.exists():
            try:
                import json
                with open(json_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Could not read president JSON file: {e}")
        
        # Try to extract from text report if it contains JSON
        report_file = self.reports_dir / "president" / f"president_{target_date.isoformat()}.txt"
        if report_file.exists():
            try:
                import json
                with open(report_file, 'r') as f:
                    content = f.read()
                    # Try to find JSON block in the report
                    json_match = re.search(r'\{[^{}]*"daily_portfolio"[^{}]*\{[^{}]*"underdog_of_the_day"[^}]*\}', content, re.DOTALL)
                    if json_match:
                        full_json_match = re.search(r'\{.*\}', content, re.DOTALL)
                        if full_json_match:
                            return json.loads(full_json_match.group(0))
            except Exception as e:
                logger.debug(f"Could not parse JSON from president report: {e}")
        
        return None
    
    def _extract_underdog_of_the_day(self, target_date: date, presidents_report: Optional[str] = None) -> Optional[Dict[str, str]]:
        """Extract underdog of the day from president response or presidents report"""
        # First, try to get from president's JSON response
        president_response = self._get_president_response_json(target_date)
        if president_response:
            daily_portfolio = president_response.get("daily_portfolio", {})
            if not daily_portfolio:
                # Try alternative structure
                daily_portfolio = president_response
            
            underdog = daily_portfolio.get("underdog_of_the_day")
            if underdog:
                # Extract game info to build matchup
                game_id = str(underdog.get("game_id", ""))
                selection = underdog.get("selection", "")
                odds = underdog.get("market_odds", "")
                model_projection = underdog.get("model_projection", "")
                reasoning = underdog.get("reasoning", "")
                
                # Try to get matchup from database
                matchup = "Matchup TBD"
                if self.db and game_id:
                    session = self.db.get_session()
                    try:
                        from src.data.storage import GameModel
                        game = session.query(GameModel).filter(GameModel.id == int(game_id)).first()
                        if game:
                            # Get official team names from database using team_id
                            team1_name = self._get_team_name(game.team1_id, session)
                            team2_name = self._get_team_name(game.team2_id, session)
                            matchup = f"{team2_name} @ {team1_name}"
                            if game.venue:
                                matchup += f" ({game.venue})"
                    except (ValueError, TypeError):
                        pass
                    finally:
                        session.close()
                
                return {
                    'matchup': matchup,
                    'selection': selection,
                    'odds': odds,
                    'model_projection': model_projection,
                    'reasoning': reasoning
                }
        
        # Fallback: try to extract from presidents report if it includes underdog section
        if presidents_report:
            underdog_match = re.search(
                r'UNDERDOG OF THE DAY.*?\n(.*?)(?=\n-{80}|\n⭐|$)', 
                presidents_report, 
                re.DOTALL | re.IGNORECASE
            )
            if underdog_match:
                underdog_text = underdog_match.group(1)
                # Try to parse basic info
                matchup_match = re.search(r'Matchup:\s*(.+)', underdog_text)
                selection_match = re.search(r'Selection:\s*(.+)', underdog_text)
                odds_match = re.search(r'Odds:\s*(.+)', underdog_text)
                
                if matchup_match and selection_match:
                    return {
                        'matchup': matchup_match.group(1).strip(),
                        'selection': selection_match.group(1).strip(),
                        'odds': odds_match.group(1).strip() if odds_match else "",
                        'model_projection': "",
                        'reasoning': ""
                    }
        
        return None
    
    def _get_today_picks_ordered_by_confidence(self, target_date: date) -> List[Dict[str, Any]]:
        """Get all picks for today ordered by confidence_score (descending)"""
        if not self.db:
            return []
        
        session = self.db.get_session()
        try:
            # Get all picks for the target date, ordered by confidence descending (higher confidence = higher confidence_score)
            # Use pick_date with fallback to created_at for legacy records
            from sqlalchemy import or_
            picks = session.query(PickModel).filter(
                or_(
                    PickModel.pick_date == target_date,
                    func.date(PickModel.created_at) == target_date
                )
            ).order_by(PickModel.confidence.desc()).all()
            
            picks_data = []
            for pick in picks:
                # Get game info
                game = session.query(GameModel).filter_by(id=pick.game_id).first()
                if not game:
                    continue
                
                # Format matchup - get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                matchup = f"{team2_name} @ {team1_name}"
                if game.venue:
                    matchup += f" ({game.venue})"
                
                # Format selection
                selection = pick.selection_text or ""
                if not selection:
                    # Construct from bet type and line - use team_id to get official name
                    if pick.bet_type == BetType.SPREAD:
                        # Determine which team based on line and team_id
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} {pick.line:+.1f}"
                        else:
                            # Fallback to team1/team2 logic
                            selection = f"{team2_name if pick.line > 0 else team1_name} {pick.line:+.1f}"
                    elif pick.bet_type == BetType.TOTAL:
                        rationale_lower = (pick.rationale or "").lower()
                        over_under = "Over" if "over" in rationale_lower and "under" not in rationale_lower else "Under"
                        selection = f"{over_under} {pick.line:.1f}"
                    elif pick.bet_type == BetType.MONEYLINE:
                        # Use team_id to get official name
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} ML ({pick.odds:+d})"
                        else:
                            # Fallback
                            selection = f"{team2_name if pick.line > 0 else team1_name} ML ({pick.odds:+d})"
                
                # Format odds
                odds_str = f"{pick.odds:+d}"
                
                # Always derive confidence_score from confidence (0.0-1.0) to confidence_score (1-10)
                # This ensures we use the actual confidence value rather than a potentially stale confidence_score
                confidence_value = pick.confidence or 0.5
                if confidence_value == 0.0:
                    confidence_score = 1
                else:
                    # Convert 0.0-1.0 to 1-10 scale: 0.1->1, 0.3->3, 0.5->5, 0.7->7, 1.0->10
                    confidence_score = max(1, min(10, int(round(confidence_value * 10))))
                
                # For best bets, create a concise summary using LLM
                rationale = pick.rationale or ""
                if pick.best_bet and rationale:
                    rationale = self._summarize_best_bet_rationale(rationale, matchup, selection)
                
                picks_data.append({
                    'matchup': matchup,
                    'selection': selection,
                    'odds': odds_str,
                    'rationale': rationale,
                    'confidence_score': confidence_score,
                    'best_bet': pick.best_bet or False
                })
            
            return picks_data
        except Exception as e:
            logger.error(f"Error getting today's picks: {e}")
            return []
        finally:
            session.close()
    
    def _get_today_games(self, target_date: date) -> List[Dict[str, Any]]:
        """Get games scheduled for today"""
        if not self.db:
            return []
        
        session = self.db.get_session()
        try:
            games = session.query(GameModel).filter(
                func.date(GameModel.date) == target_date
            ).all()
            
            game_list = []
            for game in games:
                # Get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                game_list.append({
                    'id': game.id,
                    'team1': team1_name,
                    'team2': team2_name,
                    'venue': game.venue
                })
            return game_list
        except Exception as e:
            logger.error(f"Error getting today's games: {e}")
            return []
        finally:
            session.close()
    
    def _get_yesterday_games(self, yesterday: date) -> List[Dict[str, Any]]:
        """Get games from yesterday with results"""
        if not self.db:
            return []
        
        session = self.db.get_session()
        try:
            games = session.query(GameModel).filter(
                func.date(GameModel.date) == yesterday
            ).all()
            
            game_list = []
            for game in games:
                # Get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                game_data = {
                    'id': game.id,
                    'team1': team1_name,
                    'team2': team2_name,
                    'venue': game.venue,
                    'result': game.result
                }
                
                # Get picks for this game to determine favorites/underdogs
                picks = session.query(PickModel).filter(
                    PickModel.game_id == game.id,
                    func.date(PickModel.created_at) == yesterday
                ).all()
                
                game_data['picks'] = []
                for pick in picks:
                    bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
                    if bet:
                        game_data['picks'].append({
                            'pick_id': pick.id,
                            'bet_type': pick.bet_type.value,
                            'line': pick.line,
                            'selection_text': pick.selection_text or pick.rationale,
                            'rationale': pick.rationale,
                            'stake_units': pick.stake_units,
                            'stake_amount': pick.stake_amount,
                            'odds': pick.odds,
                            'best_bet': pick.best_bet,
                            'result': bet.result.value if bet.result else None,
                            'profit_loss': bet.profit_loss if bet.result != BetResult.PENDING else None,
                            'payout': bet.payout if bet.result != BetResult.PENDING else None
                        })
                
                game_list.append(game_data)
            
            return game_list
        except Exception as e:
            logger.error(f"Error getting yesterday's games: {e}")
            return []
        finally:
            session.close()
    
    def _generate_best_bets_review(self, yesterday: date) -> str:
        """Generate detailed HTML review of yesterday's best bets"""
        if not self.db:
            return ""
        
        session = self.db.get_session()
        try:
            # Get all best bets from yesterday
            # Get the most recent pick per game_id (database constraint ensures uniqueness)
            all_picks = session.query(PickModel).filter(
                PickModel.best_bet == True,
                func.date(PickModel.created_at) == yesterday
            ).order_by(PickModel.created_at.desc()).all()
            
            # Keep only the most recent pick per game_id
            seen_game_ids = set()
            picks = []
            for pick in all_picks:
                if pick.game_id not in seen_game_ids:
                    picks.append(pick)
                    seen_game_ids.add(pick.game_id)
            
            if not picks:
                return ""
            
            # Get game info and bet results
            best_bets_data = []
            total_wagered = 0.0
            total_profit = 0.0
            wins = 0
            losses = 0
            pushes = 0
            pending = 0
            
            for pick in picks:
                game = session.query(GameModel).filter_by(id=pick.game_id).first()
                bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
                
                if not game:
                    continue
                
                # Get game result
                game_result = game.result or {}
                home_score = game_result.get('home_score', 0)
                away_score = game_result.get('away_score', 0)
                # Get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                home_team = game_result.get('home_team', team1_name)
                away_team = game_result.get('away_team', team2_name)
                
                bet_result = bet.result if bet else BetResult.PENDING
                profit_loss = bet.profit_loss if bet and bet.result != BetResult.PENDING else 0.0
                stake_units = pick.stake_units or 0.0
                stake_amount = pick.stake_amount or 0.0
                
                total_wagered += stake_amount
                total_profit += profit_loss
                
                if bet_result == BetResult.WIN:
                    wins += 1
                elif bet_result == BetResult.LOSS:
                    losses += 1
                elif bet_result == BetResult.PUSH:
                    pushes += 1
                else:
                    pending += 1
                
                # Format selection
                selection = pick.selection_text or ""
                if not selection:
                    # Try to construct from bet type and line - use team_id to get official name
                    if pick.bet_type == BetType.SPREAD:
                        # Determine which team based on line and team_id
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} {pick.line:+.1f}"
                        else:
                            # Fallback to team1/team2 logic
                            selection = f"{team2_name if pick.line > 0 else team1_name} {pick.line:+.1f}"
                    elif pick.bet_type == BetType.TOTAL:
                        rationale_lower = (pick.rationale or "").lower()
                        over_under = "Over" if "over" in rationale_lower and "under" not in rationale_lower else "Under"
                        selection = f"{over_under} {pick.line:.1f}"
                    elif pick.bet_type == BetType.MONEYLINE:
                        # Use team_id to get official name
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} ML ({pick.odds:+d})"
                        else:
                            # Fallback
                            selection = f"{team2_name if pick.line > 0 else team1_name} ML ({pick.odds:+d})"
                
                best_bets_data.append({
                    'matchup': f"{away_team} @ {home_team}",
                    'selection': selection,
                    'odds': pick.odds,
                    'stake_units': stake_units,
                    'stake_amount': stake_amount,
                    'result': bet_result.value if bet_result != BetResult.PENDING else 'pending',
                    'profit_loss': profit_loss,
                    'payout': bet.payout if bet and bet.result != BetResult.PENDING else None,
                    'final_score': f"{away_team} {away_score} - {home_team} {home_score}" if home_score or away_score else None,
                    'rationale': pick.rationale
                })
            
            if not best_bets_data:
                return ""
            
            # Generate HTML
            html_parts = []
            
            # Summary
            settled = wins + losses + pushes
            win_rate = (wins / settled * 100) if settled > 0 else 0
            html_parts.append('<div style="background-color: #f0f4f8; padding: 15px; border-radius: 5px; margin-bottom: 20px;">')
            html_parts.append(f'<p style="margin: 5px 0;"><strong>Best Bets Record:</strong> {wins}-{losses}' + (f'-{pushes}' if pushes > 0 else '') + f' ({win_rate:.1f}% win rate)</p>')
            html_parts.append(f'<p style="margin: 5px 0;"><strong>Total Wagered:</strong> {total_wagered:.2f} units (${total_wagered:.2f})</p>')
            html_parts.append(f'<p style="margin: 5px 0;"><strong>Total Profit/Loss:</strong> <span style="color: {"#2e7d32" if total_profit >= 0 else "#c62828"}; font-weight: bold;">{total_profit:+.2f} units (${total_profit:+.2f})</span></p>')
            if pending > 0:
                html_parts.append(f'<p style="margin: 5px 0; color: #666;"><em>{pending} bet(s) still pending</em></p>')
            html_parts.append('</div>')
            
            # Individual bets
            for i, bet_data in enumerate(best_bets_data, 1):
                result_color = {
                    'win': '#2e7d32',
                    'loss': '#c62828',
                    'push': '#666',
                    'pending': '#ff9800'
                }.get(bet_data['result'], '#666')
                
                result_emoji = {
                    'win': '✅',
                    'loss': '❌',
                    'push': '➖',
                    'pending': '⏳'
                }.get(bet_data['result'], '❓')
                
                html_parts.append('<div class="bet-item" style="margin-bottom: 15px;">')
                html_parts.append(f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">')
                html_parts.append(f'<div class="bet-selection"><strong>#{i}. {bet_data["matchup"]}</strong></div>')
                html_parts.append(f'<span style="color: {result_color}; font-weight: bold;">{result_emoji} {bet_data["result"].upper()}</span>')
                html_parts.append('</div>')
                
                html_parts.append(f'<div style="margin: 5px 0;"><strong>Selection:</strong> {bet_data["selection"]} ({bet_data["odds"]:+d})</div>')
                html_parts.append(f'<div style="margin: 5px 0;"><strong>Stake:</strong> {bet_data["stake_units"]:.2f} units (${bet_data["stake_amount"]:.2f})</div>')
                
                if bet_data['final_score']:
                    html_parts.append(f'<div style="margin: 5px 0;"><strong>Final Score:</strong> {bet_data["final_score"]}</div>')
                
                if bet_data['result'] != 'pending':
                    profit_color = '#2e7d32' if bet_data['profit_loss'] >= 0 else '#c62828'
                    html_parts.append(f'<div style="margin: 5px 0;"><strong>P&L:</strong> <span style="color: {profit_color}; font-weight: bold;">{bet_data["profit_loss"]:+.2f} units (${bet_data["profit_loss"]:+.2f})</span></div>')
                    if bet_data['payout']:
                        html_parts.append(f'<div style="margin: 5px 0; color: #666; font-size: 0.9em;">Payout: ${bet_data["payout"]:.2f}</div>')
                else:
                    html_parts.append('<div style="margin: 5px 0; color: #ff9800;"><em>Result pending</em></div>')
                
                if bet_data['rationale']:
                    # Show full rationale (no truncation)
                    rationale_clean = bet_data['rationale'].strip()
                    html_parts.append(f'<div class="bet-rationale" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #ddd;">💡 {rationale_clean}</div>')
                
                html_parts.append('</div>')
            
            return '\n'.join(html_parts)
            
        except Exception as e:
            logger.error(f"Error generating best bets review: {e}", exc_info=True)
            return ""
        finally:
            session.close()
    
    def _generate_best_bets_review_plain(self, yesterday: date) -> str:
        """Generate plain text review of yesterday's best bets"""
        if not self.db:
            return ""
        
        session = self.db.get_session()
        try:
            # Get all best bets from yesterday
            picks = session.query(PickModel).filter(
                PickModel.best_bet == True,
                func.date(PickModel.created_at) == yesterday
            ).all()
            
            if not picks:
                return ""
            
            # Get game info and bet results
            best_bets_data = []
            total_wagered = 0.0
            total_profit = 0.0
            wins = 0
            losses = 0
            pushes = 0
            pending = 0
            
            for pick in picks:
                game = session.query(GameModel).filter_by(id=pick.game_id).first()
                bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
                
                if not game:
                    continue
                
                # Get game result
                game_result = game.result or {}
                home_score = game_result.get('home_score', 0)
                away_score = game_result.get('away_score', 0)
                # Get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                home_team = game_result.get('home_team', team1_name)
                away_team = game_result.get('away_team', team2_name)
                
                bet_result = bet.result if bet else BetResult.PENDING
                profit_loss = bet.profit_loss if bet and bet.result != BetResult.PENDING else 0.0
                stake_units = pick.stake_units or 0.0
                stake_amount = pick.stake_amount or 0.0
                
                total_wagered += stake_amount
                total_profit += profit_loss
                
                if bet_result == BetResult.WIN:
                    wins += 1
                elif bet_result == BetResult.LOSS:
                    losses += 1
                elif bet_result == BetResult.PUSH:
                    pushes += 1
                else:
                    pending += 1
                
                # Format selection
                selection = pick.selection_text or ""
                if not selection:
                    # Try to construct from bet type and line - use team_id to get official name
                    if pick.bet_type == BetType.SPREAD:
                        # Determine which team based on line and team_id
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} {pick.line:+.1f}"
                        else:
                            # Fallback to team1/team2 logic
                            selection = f"{team2_name if pick.line > 0 else team1_name} {pick.line:+.1f}"
                    elif pick.bet_type == BetType.TOTAL:
                        rationale_lower = (pick.rationale or "").lower()
                        over_under = "Over" if "over" in rationale_lower and "under" not in rationale_lower else "Under"
                        selection = f"{over_under} {pick.line:.1f}"
                    elif pick.bet_type == BetType.MONEYLINE:
                        # Use team_id to get official name
                        if pick.team_id:
                            pick_team_name = self._get_team_name(pick.team_id, session)
                            selection = f"{pick_team_name} ML ({pick.odds:+d})"
                        else:
                            # Fallback
                            selection = f"{team2_name if pick.line > 0 else team1_name} ML ({pick.odds:+d})"
                
                best_bets_data.append({
                    'matchup': f"{away_team} @ {home_team}",
                    'selection': selection,
                    'odds': pick.odds,
                    'stake_units': stake_units,
                    'stake_amount': stake_amount,
                    'result': bet_result.value if bet_result != BetResult.PENDING else 'pending',
                    'profit_loss': profit_loss,
                    'final_score': f"{away_team} {away_score} - {home_team} {home_score}" if home_score or away_score else None,
                    'rationale': pick.rationale
                })
            
            if not best_bets_data:
                return ""
            
            # Generate plain text
            lines = []
            
            # Summary
            settled = wins + losses + pushes
            win_rate = (wins / settled * 100) if settled > 0 else 0
            lines.append(f"Best Bets Record: {wins}-{losses}" + (f"-{pushes}" if pushes > 0 else "") + f" ({win_rate:.1f}% win rate)")
            lines.append(f"Total Wagered: {total_wagered:.2f} units (${total_wagered:.2f})")
            lines.append(f"Total Profit/Loss: {total_profit:+.2f} units (${total_profit:+.2f})")
            if pending > 0:
                lines.append(f"{pending} bet(s) still pending")
            lines.append("")
            
            # Individual bets
            for i, bet_data in enumerate(best_bets_data, 1):
                result_symbol = {
                    'win': '✅',
                    'loss': '❌',
                    'push': '➖',
                    'pending': '⏳'
                }.get(bet_data['result'], '❓')
                
                lines.append(f"#{i}. {bet_data['matchup']} - {result_symbol} {bet_data['result'].upper()}")
                lines.append(f"   Selection: {bet_data['selection']} ({bet_data['odds']:+d})")
                lines.append(f"   Stake: {bet_data['stake_units']:.2f} units (${bet_data['stake_amount']:.2f})")
                
                if bet_data['final_score']:
                    lines.append(f"   Final Score: {bet_data['final_score']}")
                
                if bet_data['result'] != 'pending':
                    lines.append(f"   P&L: {bet_data['profit_loss']:+.2f} units (${bet_data['profit_loss']:+.2f})")
                else:
                    lines.append("   Result pending")
                
                if bet_data['rationale']:
                    # Show full rationale (no truncation)
                    rationale_clean = bet_data['rationale'].strip()
                    lines.append(f"   💡 {rationale_clean}")
                
                lines.append("")
            
            return '\n'.join(lines)
            
        except Exception as e:
            logger.error(f"Error generating best bets review: {e}", exc_info=True)
            return ""
        finally:
            session.close()
    
    def _generate_slate_description(self, games: List[Dict[str, Any]], target_date: date) -> str:
        """Generate 1-2 sentence description of today's slate with ranking"""
        num_games = len(games)
        
        if num_games == 0:
            return f"No games scheduled for {target_date.strftime('%B %d, %Y')}."
        
        # Count notable matchups (can enhance this with rankings, etc.)
        notable_count = min(3, num_games)
        
        # Rank the slate 1-10 based on number of games and quality
        # Simple heuristic: more games = higher rank, but cap at 10
        if num_games >= 50:
            rank = 10
        elif num_games >= 30:
            rank = 8
        elif num_games >= 20:
            rank = 7
        elif num_games >= 10:
            rank = 6
        elif num_games >= 5:
            rank = 5
        else:
            rank = 4
        
        description = f"We have {num_games} games on tap for {target_date.strftime('%B %d')}, "
        
        if num_games >= 20:
            description += "a loaded slate with plenty of opportunities across the board."
        elif num_games >= 10:
            description += "a solid slate with good variety."
        else:
            description += "a lighter slate but still some quality matchups."
        
        description += f" Slate quality: {rank}/10."
        
        return description
    
    def _get_best_games_to_watch(self, target_date: date) -> List[Dict[str, Any]]:
        """Get 2-3 best games to watch - let LLM select and describe them"""
        if not self.db:
            return []
        
        if not self.llm_client:
            logger.warning("LLM client not available - cannot generate best games to watch")
            return []
        
        session = self.db.get_session()
        try:
            games = session.query(GameModel).filter(
                func.date(GameModel.date) == target_date
            ).all()
            
            if not games:
                return []
            
            # Collect game data for LLM
            games_data = []
            for game in games:
                # Get official team names from database using team_id
                team1_name = self._get_team_name(game.team1_id, session)
                team2_name = self._get_team_name(game.team2_id, session)
                
                # Get insights for rankings
                insight = session.query(GameInsightModel).filter_by(game_id=game.id).first()
                team1_stats = insight.team1_stats if insight and insight.team1_stats else {}
                team2_stats = insight.team2_stats if insight and insight.team2_stats else {}
                
                # Get rankings
                team1_kp = team1_stats.get('kp_rank') or team1_stats.get('rank')
                team2_kp = team2_stats.get('kp_rank') or team2_stats.get('rank')
                
                # Get prediction
                prediction = session.query(PredictionModel).filter_by(
                    game_id=game.id,
                    prediction_date=target_date
                ).first()
                
                # Build game info
                game_info = {
                    'matchup': f"{team2_name} @ {team1_name}",
                    'venue': game.venue,
                    'team1_rank': int(team1_kp) if team1_kp else None,
                    'team2_rank': int(team2_kp) if team2_kp else None,
                    'rivalry': insight.rivalry if insight else False,
                    'projected_total': prediction.predicted_total if prediction else None,
                    'projected_spread': prediction.predicted_spread if prediction else None,
                    'matchup_notes': insight.matchup_notes if insight and insight.matchup_notes else None
                }
                
                games_data.append(game_info)
            
            # Let LLM select and describe the best games
            selected_games = self._llm_select_best_games(games_data)
            
            return selected_games
            
        except Exception as e:
            logger.error(f"Error getting best games to watch: {e}")
            return []
        finally:
            session.close()
    
    def _llm_select_best_games(self, games_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Use LLM to select the 3 most exciting games and generate descriptions"""
        if not games_data:
            return []
        
        # Format games data for LLM
        games_text = []
        for i, game in enumerate(games_data, 1):
            game_str = f"{i}. {game['matchup']}"
            if game.get('venue'):
                game_str += f" at {game['venue']}"
            
            details = []
            if game.get('team1_rank') and game.get('team2_rank'):
                details.append(f"Rankings: #{game['team1_rank']} vs #{game['team2_rank']}")
            elif game.get('team1_rank'):
                details.append(f"Home team ranked #{game['team1_rank']}")
            elif game.get('team2_rank'):
                details.append(f"Away team ranked #{game['team2_rank']}")
            
            if game.get('rivalry'):
                details.append("Rivalry game")
            
            if game.get('projected_total'):
                details.append(f"Projected total: {game['projected_total']:.1f} points")
            
            if game.get('projected_spread') is not None:
                spread = game['projected_spread']
                details.append(f"Projected margin: {abs(spread):.1f} points ({'home' if spread > 0 else 'away'} favored)")
            
            if game.get('matchup_notes'):
                notes = game['matchup_notes'][:200]  # Limit length
                details.append(f"Context: {notes}")
            
            if details:
                game_str += " | " + " | ".join(details)
            
            games_text.append(game_str)
        
        games_list = "\n".join(games_text)
        
        system_prompt = """You are a sports writer selecting the most exciting games to watch for a daily betting email newsletter.

Your task: Select the 3 most exciting/compelling games from the list and write a short, engaging blurb (1-2 sentences, max 120 characters) for each explaining why it's worth watching.

Selection criteria (prioritize in this order):
1. Top-ranked matchups (both teams in top 50)
2. Rivalry games
3. Historic/notable venues
4. Extremely close projected games (margin ≤ 2 points)
5. High-scoring shootouts (projected total ≥ 170) or defensive battles (≤ 115)
6. Games with interesting context/storylines

For each selected game, write a unique, specific blurb that:
- Explains what makes THIS game special
- Avoids generic phrases unless truly exceptional
- Is conversational and exciting
- Varies language across games (don't repeat the same phrases)

Output format (JSON):
{
  "selected_games": [
    {
      "matchup": "Team A @ Team B",
      "description": "Your engaging 1-2 sentence blurb here"
    },
    ...
  ]
}"""
        
        user_prompt = f"""Select the 3 most exciting games from today's slate and write engaging blurbs:

{games_list}

Return exactly 3 games in JSON format with matchup and description."""
        
        try:
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.8,
                max_tokens=400,
                parse_json=True
            )
            
            # Extract selected games
            if isinstance(response, dict):
                selected = response.get('selected_games', [])
            else:
                selected = []
            
            # Match back to original game data to get venue and other info
            result = []
            for selected_game in selected[:3]:  # Limit to 3
                matchup = selected_game.get('matchup', '')
                description = selected_game.get('description', '')
                
                # Find matching game data
                matching_game = None
                for game in games_data:
                    if game['matchup'] == matchup:
                        matching_game = game
                        break
                
                if matching_game:
                    result.append({
                        'matchup': matchup,
                        'venue': matching_game.get('venue'),
                        'description': description
                    })
            
            return result[:3]
            
        except Exception as e:
            logger.warning(f"Error in LLM game selection: {e}. Using fallback.")
            # Fallback: return top 3 by ranking if available
            ranked_games = []
            for game in games_data:
                if game.get('team1_rank') and game.get('team2_rank'):
                    avg_rank = (game['team1_rank'] + game['team2_rank']) / 2
                    ranked_games.append((avg_rank, game))
            
            ranked_games.sort(key=lambda x: x[0])
            return [
                {
                    'matchup': game['matchup'],
                    'venue': game.get('venue'),
                    'description': f"{game['matchup']} - Quality matchup worth watching."
                }
                for _, game in ranked_games[:3]
            ]
    
    def _generate_game_description(
        self,
        team1_name: str,
        team2_name: str,
        team1_kp: Optional[int],
        team2_kp: Optional[int],
        venue: Optional[str],
        insight: Optional[GameInsightModel],
        prediction: Optional[PredictionModel]
    ) -> str:
        """Generate an engaging description for a game using LLM"""
        if not self.llm_client:
            # Fallback to simple description if LLM not available
            if team1_kp and team2_kp:
                return f"Quality matchup: #{int(team1_kp)} {team1_name} hosts #{int(team2_kp)} {team2_name}."
            return f"{team2_name} visits {team1_name} in an intriguing matchup."
        
        try:
            # Build context for LLM
            context_parts = []
            
            if team1_kp and team2_kp:
                context_parts.append(f"Rankings: {team1_name} is #{int(team1_kp)}, {team2_name} is #{int(team2_kp)}")
            
            if venue:
                context_parts.append(f"Venue: {venue}")
            
            if insight and insight.rivalry:
                context_parts.append("This is a rivalry game")
            
            if prediction:
                if prediction.predicted_total:
                    context_parts.append(f"Projected total: {prediction.predicted_total:.1f} points")
                if prediction.predicted_spread:
                    context_parts.append(f"Projected margin: {team1_name} by {prediction.predicted_spread:.1f} points")
            
            if insight and insight.matchup_notes:
                notes = insight.matchup_notes[:300]  # Limit length
                context_parts.append(f"Matchup context: {notes}")
            
            context = "\n".join(context_parts) if context_parts else "Standard college basketball matchup"
            
            system_prompt = """You are a sports writer creating engaging, concise descriptions for "Best Games to Watch" in a daily betting email newsletter.

Your task: Write a 1-2 sentence description (max 150 characters) that explains why this game is worth watching. Be specific, engaging, and avoid generic phrases like "high-scoring affair" or "nail-biter" unless truly exceptional.

Focus on:
- What makes THIS game unique (rankings, venue, rivalry, etc.)
- Why viewers should tune in
- Be conversational and exciting

Avoid:
- Repetitive phrases across different games
- Generic statements that could apply to any game
- Overusing "projected" or "expected"
"""
            
            user_prompt = f"""Write a compelling 1-2 sentence description for why viewers should watch this game:

Matchup: {team2_name} @ {team1_name}

Context:
{context}

Make it unique, specific, and exciting. Focus on what makes THIS game special."""
            
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.8,
                max_tokens=100,
                parse_json=False
            )
            
            # Extract description from response
            if isinstance(response, dict):
                description = response.get('raw_response', response.get('content', ''))
            elif isinstance(response, str):
                description = response
            else:
                description = ""
            
            # Clean up the description
            description = description.strip()
            # Remove quotes if wrapped
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]
            if description.startswith("'") and description.endswith("'"):
                description = description[1:-1]
            
            # Ensure it ends with punctuation
            if description and not description[-1] in '.!?':
                description += "."
            
            # Fallback if description is too short or empty
            if len(description) < 20:
                if team1_kp and team2_kp:
                    description = f"#{int(team1_kp)} {team1_name} hosts #{int(team2_kp)} {team2_name} in a compelling matchup."
                else:
                    description = f"{team2_name} visits {team1_name} in an intriguing contest."
            
            return description
            
        except Exception as e:
            logger.warning(f"Error generating LLM description: {e}. Using fallback.")
            # Fallback description
            if team1_kp and team2_kp:
                return f"#{int(team1_kp)} {team1_name} hosts #{int(team2_kp)} {team2_name} in a quality matchup."
            return f"{team2_name} visits {team1_name} in an intriguing matchup."
    
    def _summarize_best_bet_rationale(self, full_rationale: str, matchup: str, selection: str) -> str:
        """Use LLM to create a concise 1-2 bullet point summary of best bet rationale"""
        if not self.llm_client:
            # Fallback: remove historical adjustment and president's analysis
            return self._clean_rationale_fallback(full_rationale)
        
        try:
            # Remove historical adjustment and president's analysis sections
            cleaned = self._remove_unwanted_sections(full_rationale)
            
            system_prompt = """You are a sports betting analyst creating concise summaries for best bets in an email newsletter.

Your task: Take a detailed betting rationale and create a concise 1-2 bullet point summary (max 150 characters total) that explains:
- The key value proposition (why this bet has edge)
- The main reasoning (model edge, matchup advantage, etc.)

Format as 1-2 short bullet points. Be specific and avoid generic language."""
            
            user_prompt = f"""Summarize this betting rationale into 1-2 concise bullet points:

Matchup: {matchup}
Selection: {selection}

Full Rationale:
{cleaned}

Create a brief, engaging summary that captures the key value proposition."""
            
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=100,
                parse_json=False
            )
            
            # Extract summary
            if isinstance(response, dict):
                summary = response.get('raw_response', response.get('content', ''))
            elif isinstance(response, str):
                summary = response
            else:
                summary = ""
            
            summary = summary.strip()
            
            # Ensure it's formatted as bullets if not already
            if summary and not summary.startswith('•') and not summary.startswith('-'):
                # Split into sentences and format as bullets
                sentences = [s.strip() for s in summary.split('.') if s.strip()]
                if len(sentences) <= 2:
                    summary = ' • '.join(sentences)
                else:
                    summary = ' • '.join(sentences[:2])
            
            # Fallback if summary is too short or empty
            if len(summary) < 20:
                return self._clean_rationale_fallback(full_rationale)
            
            return summary
            
        except Exception as e:
            logger.warning(f"Error summarizing best bet rationale: {e}. Using fallback.")
            return self._clean_rationale_fallback(full_rationale)
    
    def _remove_unwanted_sections(self, rationale: str) -> str:
        """Remove 'Historical adjustment' and 'President's Analysis' sections"""
        # Remove Historical adjustment section
        rationale = re.sub(
            r'Historical adjustment:.*?(?=\| |$|President\'s Analysis:)',
            '',
            rationale,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Remove President's Analysis section
        rationale = re.sub(
            r'President\'s Analysis:.*$',
            '',
            rationale,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Clean up extra pipes and whitespace
        rationale = re.sub(r'\|\s*\|', '|', rationale)  # Remove double pipes
        rationale = re.sub(r'\s+', ' ', rationale)  # Normalize whitespace
        rationale = rationale.strip()
        
        # Remove trailing pipes
        rationale = rationale.rstrip('|').strip()
        
        return rationale
    
    def _clean_rationale_fallback(self, rationale: str) -> str:
        """Fallback: clean rationale without LLM"""
        cleaned = self._remove_unwanted_sections(rationale)
        
        # If still too long, take first 2-3 key points
        if len(cleaned) > 200:
            # Split by | and take first 2-3 parts
            parts = [p.strip() for p in cleaned.split('|') if p.strip()]
            if len(parts) > 2:
                cleaned = ' | '.join(parts[:2])
            else:
                cleaned = ' | '.join(parts)
        
        return cleaned
    
    def _generate_simple_game_description(
        self,
        team1_name: str,
        team2_name: str,
        team1_kp: Optional[int],
        team2_kp: Optional[int],
        venue: Optional[str],
        insight: Optional[GameInsightModel],
        prediction: Optional[PredictionModel]
    ) -> str:
        """Generate a simple programmatic description for a game"""
        parts = []
        
        # Rankings
        if team1_kp and team2_kp:
            if team1_kp <= 25 and team2_kp <= 25:
                parts.append(f"Top-25 clash: #{int(team1_kp)} {team1_name} hosts #{int(team2_kp)} {team2_name}")
            elif team1_kp <= 50 or team2_kp <= 50:
                better_rank = min(team1_kp, team2_kp)
                better_team = team1_name if team1_kp == better_rank else team2_name
                parts.append(f"#{int(better_rank)} {better_team} looks to defend home court")
            else:
                parts.append(f"{team2_name} visits {team1_name}")
        else:
            parts.append(f"{team2_name} visits {team1_name}")
        
        # Add one interesting detail
        if insight and insight.rivalry:
            parts.append("in a rivalry matchup")
        elif prediction and prediction.predicted_total:
            total = prediction.predicted_total
            if total >= 170:
                parts.append(f"with a projected {total:.0f}-point shootout")
            elif total <= 115:
                parts.append(f"in a defensive battle (projected {total:.0f} points)")
        elif prediction and prediction.predicted_spread:
            spread = abs(prediction.predicted_spread)
            if spread <= 3:
                parts.append("in a tight contest")
        
        description = " ".join(parts) + "."
        return description
    
    def _generate_superlatives(
        self,
        games: List[Dict[str, Any]],
        results: Dict[str, Any]
    ) -> Dict[str, str]:
        """Generate superlatives for yesterday's games using LLM"""
        # Filter games with results
        games_with_results = [
            g for g in games
            if g.get('result') and isinstance(g.get('result'), dict)
        ]
        
        if not games_with_results:
            return {}
        
        if not self.llm_client:
            # Fallback to empty if LLM not available
            return {}
        
        # Build game results data for LLM
        games_data = []
        for game in games_with_results:
            result = game.get('result', {})
            home_score = result.get('home_score', 0)
            away_score = result.get('away_score', 0)
            
            # Convert scores to int
            try:
                home_score = int(home_score) if home_score else 0
            except (ValueError, TypeError):
                home_score = 0
            try:
                away_score = int(away_score) if away_score else 0
            except (ValueError, TypeError):
                away_score = 0
            
            home_team = result.get('home_team', game.get('team1', 'Home'))
            away_team = result.get('away_team', game.get('team2', 'Away'))
            
            # Get betting lines for this game to determine favorites/underdogs
            spread_info = None
            if self.db:
                session = self.db.get_session()
                try:
                    from src.data.storage import BettingLineModel
                    spread_lines = session.query(BettingLineModel).filter(
                        BettingLineModel.game_id == game['id'],
                        BettingLineModel.bet_type == BetType.SPREAD
                    ).order_by(BettingLineModel.timestamp.desc()).all()
                    
                    if spread_lines:
                        # Format spread info
                        spreads = []
                        for line in spread_lines[:2]:  # Get up to 2 lines (home and away)
                            if line.team:
                                spread_val = line.line
                                if spread_val > 0:
                                    spreads.append(f"{line.team} +{spread_val:.1f} (underdog)")
                                else:
                                    spreads.append(f"{line.team} {spread_val:.1f} (favorite)")
                        if spreads:
                            spread_info = " | ".join(spreads)
                except Exception as e:
                    logger.debug(f"Error getting spread info for game {game.get('id')}: {e}")
                finally:
                    session.close()
            
            games_data.append({
                'matchup': f"{away_team} @ {home_team}",
                'score': f"{away_team} {away_score} - {home_team} {home_score}",
                'home_team': home_team,
                'away_team': away_team,
                'home_score': home_score,
                'away_score': away_score,
                'total': home_score + away_score,
                'margin': abs(home_score - away_score),
                'spread_info': spread_info
            })
        
        # Format games for LLM
        games_text = "\n".join([
            f"{i+1}. {g['matchup']}: {g['score']} (Total: {g['total']} pts, Margin: {g['margin']} pts)"
            + (f" | Spreads: {g['spread_info']}" if g.get('spread_info') else "")
            for i, g in enumerate(games_data)
        ])
        
        system_prompt = """You are a sports analyst identifying the most interesting highlights from yesterday's college basketball games.

Your task: Analyze the game results and identify 2-4 of the most interesting highlights. Focus on:

1. Biggest Underdog Win: The team that was the biggest underdog (largest positive spread) that won
2. Biggest Blowout: The game with the largest margin of victory (only if margin ≥ 15 points)
3. Highest or Lowest Scoring Game: Pick whichever is more interesting/notable (very high ≥170 or very low ≤100)
4. Most Exciting Game: A close game with margin ≤ 5 points (only if there is one)

For each highlight, write a concise, engaging description (max 80 characters) in the format:
"Team Name (spread if underdog) description - Final Score"

Output format (JSON):
{
  "highlights": [
    {
      "category": "Biggest Underdog Win" | "Biggest Blowout" | "Highest Scoring Game" | "Lowest Scoring Game" | "Most Exciting Game",
      "description": "Your engaging description here"
    },
    ...
  ]
}"""
        
        user_prompt = f"""Analyze these game results from yesterday and identify 2-4 interesting highlights:

{games_text}

Return the highlights in JSON format."""
        
        try:
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=300,
                parse_json=True
            )
            
            # Extract highlights
            if isinstance(response, dict):
                highlights = response.get('highlights', [])
            else:
                highlights = []
            
            # Convert to dictionary format
            superlatives = {}
            for highlight in highlights[:4]:  # Limit to 4
                category = highlight.get('category', '')
                description = highlight.get('description', '')
                if category and description:
                    superlatives[category] = description
            
            return superlatives
            
        except Exception as e:
            logger.warning(f"Error generating LLM superlatives: {e}. Returning empty.")
            return {}
    
    def _format_yesterday_performance(self, results: Dict[str, Any]) -> str:
        """Format yesterday's performance summary with engaging language"""
        wins = results.get('wins', 0)
        losses = results.get('losses', 0)
        pushes = results.get('pushes', 0)
        settled_bets = results.get('settled_bets', results.get('total_picks', 0))
        profit_units = results.get('profit_loss_units', 0.0)
        profit_dollars = results.get('profit_loss_dollars', 0.0)
        
        record = f"{wins}-{losses}"
        if pushes > 0:
            record += f"-{pushes}"
        
        # Calculate win rate
        settled = wins + losses + pushes
        win_rate = (wins / settled * 100) if settled > 0 else 0.0
        
        # Format profit/loss with engaging language
        if profit_units > 0:
            profit_emoji = "💰"
            profit_desc = "in the green"
        elif profit_units < 0:
            profit_emoji = "📉"
            profit_desc = "in the red"
        else:
            profit_emoji = "➖"
            profit_desc = "break even"
        
        profit_str = f"{profit_units:+.2f} units"
        if profit_dollars != 0:
            profit_str += f" (${profit_dollars:+.2f})"
        
        # Build performance summary with engaging language
        if settled > 0:
            if win_rate >= 60:
                performance_desc = "🔥 Hot streak!"
            elif win_rate >= 50:
                performance_desc = "✅ Solid day"
            else:
                performance_desc = "📊 Learning day"
        else:
            performance_desc = "⏳ Pending"
        
        parts = [f"<strong>{performance_desc}</strong>"]
        parts.append(f"Record: {record}")
        if settled > 0:
            parts.append(f"Win Rate: {win_rate:.1f}%")
        if settled_bets > 0:
            parts.append(f"📋 {settled_bets} bet{'s' if settled_bets != 1 else ''} settled")
        
        return " | ".join(parts)
    
    def _format_yesterday_performance_plain(self, results: Dict[str, Any]) -> str:
        """Format yesterday's performance summary for plain text (no HTML)"""
        wins = results.get('wins', 0)
        losses = results.get('losses', 0)
        pushes = results.get('pushes', 0)
        settled_bets = results.get('settled_bets', results.get('total_picks', 0))
        profit_units = results.get('profit_loss_units', 0.0)
        profit_dollars = results.get('profit_loss_dollars', 0.0)
        
        record = f"{wins}-{losses}"
        if pushes > 0:
            record += f"-{pushes}"
        
        # Calculate win rate
        settled = wins + losses + pushes
        win_rate = (wins / settled * 100) if settled > 0 else 0.0
        
        # Format profit/loss with engaging language
        if profit_units > 0:
            profit_emoji = "💰"
            profit_desc = "in the green"
        elif profit_units < 0:
            profit_emoji = "📉"
            profit_desc = "in the red"
        else:
            profit_emoji = "➖"
            profit_desc = "break even"
        
        profit_str = f"{profit_units:+.2f} units"
        if profit_dollars != 0:
            profit_str += f" (${profit_dollars:+.2f})"
        
        # Build performance summary with engaging language
        if settled > 0:
            if win_rate >= 60:
                performance_desc = "🔥 Hot streak!"
            elif win_rate >= 50:
                performance_desc = "✅ Solid day"
            else:
                performance_desc = "📊 Learning day"
        else:
            performance_desc = "⏳ Pending"
        
        parts = [performance_desc]
        parts.append(f"Record: {record}")
        if settled > 0:
            parts.append(f"Win Rate: {win_rate:.1f}%")
        if settled_bets > 0:
            parts.append(f"📋 {settled_bets} bet{'s' if settled_bets != 1 else ''} settled")
        
        return " | ".join(parts)
    
    def _extract_best_bets(self, report_text: str) -> List[Dict[str, str]]:
        """Extract best bets from presidents report with rationale"""
        best_bets = []
        
        # Find the ALL PICKS section (can be "ALL PICKS WITH RATIONALE" or "APPROVED PICKS WITH RATIONALE")
        picks_match = re.search(r'🎯 (?:ALL|APPROVED) PICKS WITH RATIONALE.*?\n(.*?)(?=\n❌|$)', report_text, re.DOTALL)
        if not picks_match:
            return best_bets
        
        picks_section = picks_match.group(1)
        
        # Extract each pick that has "⭐ BEST BET" marker
        # Pattern: PICK #N ⭐ BEST BET followed by pick details
        pick_pattern = r'PICK #\d+\s+⭐ BEST BET\s*\n(.*?)(?=\nPICK #|\n-{80}|\n❌|$)'
        picks = re.findall(pick_pattern, picks_section, re.DOTALL)
        
        for pick_text in picks:
            # Extract the selection/matchup line (first non-empty line after PICK header)
            # Format examples:
            # "North Carolina Central Eagles +28.5 vs Dayton Flyers" (spread)
            # "Under 154.5 - North Alabama Lions @ Chattanooga Mocs" (total)
            selection_line_match = re.search(r'^  (.+?)(?:\n|$)', pick_text, re.MULTILINE)
            if not selection_line_match:
                continue
                
            selection_line = selection_line_match.group(1).strip()
            
            # Extract odds
            odds_match = re.search(r'  Odds: (.+)', pick_text)
            odds = odds_match.group(1).strip() if odds_match else ""
            
            # Extract rationale from PRESIDENT'S ANALYSIS section (more concise than PICKER'S RATIONALE)
            rationale_match = re.search(r'💼 PRESIDENT\'S ANALYSIS:\s*(.+?)(?=\n  🔍|\n-{80}|$)', pick_text, re.DOTALL)
            if not rationale_match:
                # Fallback to PICKER'S RATIONALE
                rationale_match = re.search(r'📋 PICKER\'S RATIONALE:\s*(.+?)(?=\nPresident\'s Analysis|\n  💼|\n  🔍|\n-{80}|$)', pick_text, re.DOTALL)
            
            rationale = ""
            if rationale_match:
                rationale = rationale_match.group(1).strip()
                # Clean up formatting - preserve bullet structure but normalize whitespace
                # Replace multiple spaces/newlines with single space, but keep bullet points
                rationale = re.sub(r'\n\s*', ' ', rationale)  # Replace newlines with space
                rationale = re.sub(r'\s+', ' ', rationale)  # Normalize multiple spaces
                # Take first 2-3 bullet points or first 250 chars
                if '•' in rationale:
                    bullets = [b.strip() for b in rationale.split('•') if b.strip()]
                    if bullets:
                        # Take first 2-3 bullets, join with separator
                        selected_bullets = bullets[:3]
                        rationale = ' • '.join(selected_bullets)
                        if len(bullets) > 3:
                            rationale += '...'
                elif len(rationale) > 250:
                    # Try to break at sentence boundary
                    sentences = re.split(r'[.!?]\s+', rationale)
                    if sentences and len(sentences[0]) < 250:
                        rationale = sentences[0] + '.'
                    else:
                        rationale = rationale[:250] + '...'
            
            # Parse selection and matchup from selection_line
            matchup = ""
            selection = ""
            
            # Check if it's a total format (contains " - " and "@")
            if ' - ' in selection_line and '@' in selection_line:
                # Total format: "Under 154.5 - North Alabama Lions @ Chattanooga Mocs"
                parts = selection_line.split(' - ', 1)
                selection = parts[0].strip()
                matchup = parts[1].strip()
            elif ' vs ' in selection_line:
                # Spread format: "North Carolina Central Eagles +28.5 vs Dayton Flyers"
                # Extract the line value and teams
                vs_match = re.search(r'^(.+?)\s+([+-]?\d+\.?\d*)\s+vs\s+(.+)$', selection_line)
                if vs_match:
                    team1 = vs_match.group(1).strip()
                    line = vs_match.group(2).strip()
                    team2 = vs_match.group(3).strip()
                    selection = f"{team1} {line}"
                    matchup = f"{team1} vs {team2}"
                else:
                    # Fallback: just use the whole line
                    selection = selection_line
                    # Try to get matchup from projected score
                    projected_match = re.search(r'Projected score: (.+)', pick_text)
                    if projected_match:
                        matchup = projected_match.group(1).strip()
                    else:
                        matchup = "Matchup TBD"
            else:
                # Fallback: use the whole line as selection
                selection = selection_line
                # Try to get matchup from projected score
                projected_match = re.search(r'Projected score: (.+)', pick_text)
                if projected_match:
                    matchup = projected_match.group(1).strip()
                else:
                    matchup = "Matchup TBD"
            
            best_bets.append({
                'matchup': matchup,
                'selection': selection,
                'odds': odds,
                'rationale': rationale
            })
        
        return best_bets
    
    def _extract_other_picks(self, report_text: str) -> List[str]:
        """Extract other picks from presidents report (non-best-bet picks)"""
        other_picks = []
        
        # Find the ALL PICKS section
        picks_match = re.search(r'🎯 (?:ALL|APPROVED) PICKS WITH RATIONALE.*?\n(.*?)(?=\n❌|$)', report_text, re.DOTALL)
        if not picks_match:
            return other_picks
        
        picks_section = picks_match.group(1)
        
        # Extract all picks with their full text including header
        # Pattern matches: PICK #N or PICK #N ⭐ BEST BET, followed by content
        pick_pattern = r'(PICK #\d+(?:\s+⭐ BEST BET)?)\s*\n(.*?)(?=\nPICK #|\n-{80}|\n❌|$)'
        all_picks = re.findall(pick_pattern, picks_section, re.DOTALL)
        
        # Extract picks that are NOT best bets
        for pick_header, pick_text in all_picks:
            # Check if this pick is a best bet
            if '⭐ BEST BET' in pick_header:
                continue  # Skip best bets
            
            # Extract the selection/matchup line (first non-empty line after PICK header)
            selection_line_match = re.search(r'^  (.+?)(?:\n|$)', pick_text, re.MULTILINE)
            if not selection_line_match:
                continue
                
            selection_line = selection_line_match.group(1).strip()
            
            # Extract odds
            odds_match = re.search(r'  Odds: (.+)', pick_text)
            odds = odds_match.group(1).strip() if odds_match else ""
            
            # Format the pick line
            pick_line = selection_line
            if odds:
                pick_line += f" ({odds})"
            
            other_picks.append(pick_line)
        
        return other_picks
    
    def _extract_best_bets_from_card(self, card_text: str) -> List[Dict[str, str]]:
        """Extract best bets from betting card format"""
        best_bets = []
        
        # Find the BEST BETS section
        best_bets_match = re.search(r'⭐ BEST BETS.*?\n(.*?)(?=\n-{80}|\n📋|$)', card_text, re.DOTALL)
        if not best_bets_match:
            return best_bets
        
        best_bets_section = best_bets_match.group(1)
        
        # Extract each best bet
        bet_pattern = r'BEST BET #\d+\s*\n(.*?)(?=\nBEST BET #|\n-{80}|$)'
        bets = re.findall(bet_pattern, best_bets_section, re.DOTALL)
        
        for bet_text in bets:
            # Extract key info from betting card format
            matchup_match = re.search(r'  Matchup: (.+)', bet_text)
            selection_match = re.search(r'  Selection: (.+)', bet_text)
            odds_match = re.search(r'  Odds: (.+)', bet_text)
            rationale_match = re.search(r'  Rationale: (.+?)(?=\n  President|$)', bet_text, re.DOTALL)
            
            if matchup_match and selection_match:
                matchup = matchup_match.group(1).strip()
                selection = selection_match.group(1).strip()
                odds = odds_match.group(1).strip() if odds_match else ""
                
                # Extract and condense rationale to 2 bullet points max
                rationale = ""
                if rationale_match:
                    full_rationale = rationale_match.group(1).strip()
                    # Clean up rationale
                    full_rationale = re.sub(r'\s+', ' ', full_rationale)
                    
                    # Split by | (pipe separator) which is the main separator in the rationale
                    if '|' in full_rationale:
                        points = [p.strip() for p in full_rationale.split('|') if p.strip()]
                    else:
                        # Fallback: try splitting by sentences (period followed by space and capital)
                        points = [p.strip() for p in re.split(r'\.\s+(?=[A-Z])', full_rationale) if p.strip()]
                    
                    # Take first 2 points and format as bullets
                    if points:
                        selected_points = points[:2]
                        # Format as bullet points, each on its own line with proper indentation
                        if len(selected_points) == 1:
                            rationale = f"• {selected_points[0]}"
                        else:
                            rationale = f"• {selected_points[0]}\n   • {selected_points[1]}"
                    else:
                        # Fallback: just take first 200 chars
                        rationale = f"• {full_rationale[:200]}"
                
                best_bets.append({
                    'matchup': matchup,
                    'selection': selection,
                    'odds': odds,
                    'rationale': rationale
                })
        
        return best_bets
    
    def _extract_other_picks_from_card(self, card_text: str) -> List[str]:
        """Extract other picks from betting card format (without rationale)"""
        other_picks = []
        
        # Find the OTHER PICKS section
        other_picks_match = re.search(r'📋 ALL OTHER PICKS.*?\n(.*?)(?=\n={80}|$)', card_text, re.DOTALL)
        if not other_picks_match:
            return other_picks
        
        other_picks_section = other_picks_match.group(1)
        
        # Extract each pick
        pick_pattern = r'PICK #\d+\s*\n(.*?)(?=\nPICK #|\n-{80}|$)'
        picks = re.findall(pick_pattern, other_picks_section, re.DOTALL)
        
        for pick_text in picks:
            # Extract key info from betting card format
            matchup_match = re.search(r'  Matchup: (.+)', pick_text)
            selection_match = re.search(r'  Selection: (.+)', pick_text)
            odds_match = re.search(r'  Odds: (.+)', pick_text)
            
            if matchup_match and selection_match:
                matchup = matchup_match.group(1).strip()
                selection = selection_match.group(1).strip()
                odds = odds_match.group(1).strip() if odds_match else ""
                
                # Format: "Matchup - Selection (Odds)"
                pick_line = f"{matchup} - {selection}"
                if odds:
                    pick_line += f" ({odds})"
                
                other_picks.append(pick_line)
        
        return other_picks
    
    def _get_modeler_stash(self, target_date: date) -> Optional[str]:
        """Get modeler stash for a given date"""
        stash_file = self.reports_dir / "modeler" / f"modeler_{target_date.isoformat()}.txt"
        if stash_file.exists():
            try:
                with open(stash_file, 'r') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Error reading modeler stash: {e}")
        return None
    
    def _generate_yesterday_recap(
        self,
        yesterday: date,
        yesterday_results: Dict[str, Any],
        yesterday_games: List[Dict[str, Any]]
    ) -> Optional[str]:
        """Generate yesterday's recap using LLM"""
        if not self.llm_client:
            return None
        
        try:
            # Get modeler stash for yesterday
            modeler_stash = self._get_modeler_stash(yesterday)
            
            # Prepare data for LLM - use settled_bets instead of total_picks
            # total_picks includes all picks, but settled_bets is what actually got settled
            settled_bets = yesterday_results.get('settled_bets', yesterday_results.get('total_picks', 0))
            results_summary = {
                'total_picks': settled_bets,  # Use settled bets count
                'wins': yesterday_results.get('wins', 0),
                'losses': yesterday_results.get('losses', 0),
                'pushes': yesterday_results.get('pushes', 0),
                'profit_loss_units': yesterday_results.get('profit_loss_units', 0.0),
                'profit_loss_dollars': yesterday_results.get('profit_loss_dollars', 0.0),
                'accuracy': yesterday_results.get('accuracy', 0.0)
            }
            
            # Get game results summary with prediction details
            games_summary = []
            for game in yesterday_games[:10]:  # Limit to top 10 games
                result = game.get('result', {})
                picks = game.get('picks', [])
                
                if result and picks:
                    home_score = result.get('home_score')
                    away_score = result.get('away_score')
                    team1 = game.get('team1', 'Home')
                    team2 = game.get('team2', 'Away')
                    
                    # Extract prediction details from picks
                    predictions = []
                    for pick in picks:
                        bet_type = pick.get('bet_type', '').upper()
                        selection = pick.get('selection_text', '')
                        line = pick.get('line')
                        
                        if bet_type == 'SPREAD' and selection:
                            # Extract team name and line from selection (e.g., "Marshall -14.5")
                            predictions.append(f"{selection}")
                        elif bet_type == 'TOTAL' and selection:
                            predictions.append(f"{selection}")
                        elif bet_type == 'MONEYLINE' and selection:
                            predictions.append(f"{selection}")
                    
                    # Format actual result
                    actual_result = f"{away_score} - {home_score}"
                    if home_score and away_score:
                        margin = home_score - away_score
                        if margin > 0:
                            actual_result += f" ({team1} won by {margin})"
                        elif margin < 0:
                            actual_result += f" ({team2} won by {abs(margin)})"
                        else:
                            actual_result += " (Tie)"
                    
                    games_summary.append({
                        'matchup': f"{team2} @ {team1}",
                        'team1': team1,
                        'team2': team2,
                        'home_score': home_score,
                        'away_score': away_score,
                        'actual_result': actual_result,
                        'picks': len(picks),
                        'predictions': predictions  # What we predicted
                    })
            
            # Use system prompt from prompts.py
            system_prompt = EMAIL_GENERATOR_RECAP_SYSTEM_PROMPT
            
            # Format games with predictions - explicitly include both team names
            games_text = []
            for g in games_summary:
                team1 = g.get('team1', 'Home')
                team2 = g.get('team2', 'Away')
                matchup = f"{team2} vs. {team1}"  # Use "vs." format for clarity
                actual = g.get('actual_result', f"{g.get('away_score', '?')} - {g.get('home_score', '?')}")
                predictions = g.get('predictions', [])
                
                if predictions:
                    pred_text = ", ".join(predictions[:2])  # Limit to first 2 predictions
                    games_text.append(f"- {matchup}: We predicted {pred_text}. Actual: {actual}")
                else:
                    games_text.append(f"- {matchup}: Actual: {actual}")
            
            user_prompt = f"""Generate a daily betting recap for {yesterday.isoformat()}.

Results Summary:
- Total Picks: {results_summary['total_picks']}
- Wins: {results_summary['wins']}
- Losses: {results_summary['losses']}
- Pushes: {results_summary['pushes']}
- Accuracy: {results_summary['accuracy']:.1f}%

Notable Games (with predictions):
{chr(10).join(games_text)}

{f'Modeler Predictions Context:{chr(10)}{modeler_stash[:2000]}' if modeler_stash else ''}

IMPORTANT: Each game listed above shows the full matchup (both team names). When writing about notable games in your recap, you MUST use the complete matchup format "Team A vs. Team B" - never use "[opponent]" or omit team names. Always include both team names when discussing any game.

Write a compelling recap that highlights the key moments and performance. When discussing notable games, ALWAYS mention the full matchup (both teams), what we predicted, and what actually happened. Do NOT mention units, profit, loss, P&L, or dollar amounts."""
            
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=1000,
                parse_json=False
            )
            
            # LLM client returns {"raw_response": content} when parse_json=False
            recap_text = None
            if isinstance(response, dict):
                if 'raw_response' in response:
                    recap_text = response['raw_response']
                elif 'content' in response:
                    recap_text = response['content']
            elif isinstance(response, str):
                recap_text = response
            
            if recap_text:
                # Remove any signature/closing that might have been added
                # Remove lines like "Best,", "[Your Name]", "Sports Betting Analyst", etc.
                lines = recap_text.split('\n')
                cleaned_lines = []
                skip_remaining = False
                for line in lines:
                    line_lower = line.strip().lower()
                    # Stop if we hit a signature pattern
                    if any(pattern in line_lower for pattern in ['best,', '[your name]', 'sports betting analyst', 'sincerely', 'regards']):
                        skip_remaining = True
                    if not skip_remaining:
                        cleaned_lines.append(line)
                
                return '\n'.join(cleaned_lines).strip()
            
            logger.warning(f"Unexpected LLM response format: {type(response)}")
            return None
                
        except Exception as e:
            logger.error(f"Error generating LLM recap: {e}")
            return None
    
    def _generate_sign_off(self, target_date: date) -> Tuple[str, str]:
        """
        Generate a motivational sign-off message for degenerate gamblers using LLM.
        Returns a tuple of (main_message, secondary_message).
        """
        if not self.llm_client:
            # Fallback to default message if LLM not available
            return ("Time to print money! 💰🔥", "All gas, no brakes. Let's ride this wave to the bank! 🚀⚡")
        
        try:
            system_prompt = """You are writing a daily motivational sign-off for a sports betting email newsletter targeting degenerate gamblers.
            
Your job: Generate TWO short, energetic lines that will motivate and pump up degenerate gamblers:
1. First line: A main punchy statement (max 50 chars, bold and exciting)
2. Second line: A follow-up motivational statement (max 80 chars)

Requirements:
- NO responsible gambling messages - these are degenerate gamblers who don't care about responsibility
- Use gambling/casino/money emojis liberally (💰🔥🚀⚡🎰💸💎)
- Make it exciting, confident, and action-oriented
- Vary the message each time - don't repeat the same phrases
- Keep it short and punchy
- Examples of tone: "Let's get this bread!", "Time to print money!", "All gas no brakes!", "Let's ride!", "YOLO mode activated!"

Output format (JSON):
{
  "main_message": "Your exciting main message here",
  "secondary_message": "Your follow-up motivational message here"
}"""

            user_prompt = f"""Generate a unique motivational sign-off for today ({target_date.strftime('%B %d, %Y')}). 
Make it exciting and different from previous days. These are degenerate gamblers ready to bet - pump them up!"""
            
            response = self.llm_client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.9,
                max_tokens=200,
                parse_json=True
            )
            
            # Extract messages
            if isinstance(response, dict):
                main = response.get('main_message', 'Time to print money! 💰🔥')
                secondary = response.get('secondary_message', 'All gas, no brakes. Let\'s ride this wave to the bank! 🚀⚡')
                return (main, secondary)
            else:
                logger.warning(f"Unexpected LLM response format for sign-off: {type(response)}")
                return ("Time to print money! 💰🔥", "All gas, no brakes. Let's ride this wave to the bank! 🚀⚡")
                
        except Exception as e:
            logger.error(f"Error generating LLM sign-off: {e}. Using fallback.")
            return ("Time to print money! 💰🔥", "All gas, no brakes. Let's ride this wave to the bank! 🚀⚡")
    
    def _format_recap_as_html(self, recap_text: str) -> str:
        """Convert plain text recap to nicely formatted HTML"""
        import re
        
        # Split into paragraphs (double newlines)
        paragraphs = recap_text.split('\n\n')
        html_parts = []
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Check if it's a bullet list (starts with - or *)
            if para.startswith('-') or para.startswith('*'):
                # Convert to HTML list
                lines = para.split('\n')
                html_parts.append('<ul>')
                for line in lines:
                    line = line.strip()
                    if line.startswith('-') or line.startswith('*'):
                        # Remove bullet and clean up
                        content = line[1:].strip()
                        # Make bold text (text between **)
                        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
                        html_parts.append(f'<li>{content}</li>')
                html_parts.append('</ul>')
            else:
                # Regular paragraph
                # Convert markdown-style bold (**text**) to HTML
                para = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', para)
                # Convert single newlines to <br> for line breaks within paragraph
                para = para.replace('\n', '<br>')
                html_parts.append(f'<p>{para}</p>')
        
        return '\n'.join(html_parts)
    
    def save_email(self, email_content: str, target_date: Optional[date] = None) -> Path:
        """Save email to file"""
        if target_date is None:
            target_date = date.today()
        
        email_dir = self.reports_dir / "emails"
        email_dir.mkdir(parents=True, exist_ok=True)
        
        filename = email_dir / f"daily_email_{target_date.isoformat()}.txt"
        
        with open(filename, 'w') as f:
            f.write(email_content)
        
        logger.info(f"Email saved to {filename}")
        return filename
    
    def send_email(
        self,
        subject: str,
        email_content: str,
        target_date: Optional[date] = None,
        recipients: Optional[List[str]] = None,
        send_html: bool = True
    ) -> bool:
        """
        Send email to recipients
        
        Args:
            subject: Email subject line
            email_content: Email content (HTML or plain text)
            target_date: Date for email subject
            recipients: List of recipient emails (if None, uses config)
            send_html: If True, send as HTML email, otherwise plain text
            
        Returns:
            True if sent successfully, False otherwise
        """
        # Get email configuration
        email_config = config.get('email', {})
        smtp_server = email_config.get('smtp_server', 'smtp.gmail.com')
        smtp_port = email_config.get('smtp_port', 587)
        use_tls = email_config.get('use_tls', True)
        sender_name = email_config.get('sender_name', 'Terrarium Betting')
        
        # Get sender credentials from environment
        sender_email = os.getenv('EMAIL_SENDER')
        sender_password = os.getenv('EMAIL_PASSWORD')
        
        if not sender_email or not sender_password:
            logger.error("EMAIL_SENDER and EMAIL_PASSWORD environment variables must be set")
            return False
        
        # Get recipients
        if recipients is None:
            # Try environment variable first
            env_recipients = os.getenv('EMAIL_RECIPIENTS', '')
            if env_recipients:
                recipients = [r.strip() for r in env_recipients.split(',') if r.strip()]
            else:
                # Fall back to config
                recipients = email_config.get('recipients', [])
        
        if not recipients:
            logger.error("No recipients specified. Set EMAIL_RECIPIENTS env var or configure in config.yaml")
            return False
        
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{sender_name} <{sender_email}>"
            # Use sender's email as the "To" field (required by some SMTP servers)
            # All recipients will be in BCC so they can't see each other
            msg['To'] = sender_email
            msg['Bcc'] = ', '.join(recipients)
            msg['Subject'] = subject
            
            # Create plain text version (for email clients that don't support HTML)
            plain_text = self._html_to_plain_text(email_content) if send_html else email_content
            msg.attach(MIMEText(plain_text, 'plain'))
            
            # Add HTML version if sending HTML
            if send_html:
                msg.attach(MIMEText(email_content, 'html'))
            
            # Send email
            logger.info(f"Sending email to {len(recipients)} recipient(s) via BCC")
            
            if use_tls:
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            
            server.login(sender_email, sender_password)
            # Send to all recipients (including BCC)
            server.send_message(msg, from_addr=sender_email, to_addrs=[sender_email] + recipients)
            server.quit()
            
            logger.info(f"Email sent successfully to {len(recipients)} recipient(s) via BCC")
            return True
            
        except Exception as e:
            logger.error(f"Error sending email: {e}", exc_info=True)
            return False
    
    def _html_to_plain_text(self, html_content: str) -> str:
        """Convert HTML to plain text for email clients that don't support HTML"""
        # Simple HTML to text conversion
        import re
        text = html_content
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        return text

