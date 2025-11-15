"""Reporting and visualization utilities"""

from typing import List, Optional
from datetime import date, timedelta
from pathlib import Path

from src.data.models import DailyReport, Pick, Bet, Bankroll
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
        """Generate daily report as text"""
        if target_date is None:
            target_date = date.today()
        
        if not self.auditor:
            return f"No report available for {target_date}"
        
        report = self.auditor.process(target_date)
        
        # Format report
        lines = [
            "=" * 60,
            f"DAILY REPORT - {target_date}",
            "=" * 60,
            "",
            f"Total Picks: {report.total_picks}",
            f"Wins: {report.wins}",
            f"Losses: {report.losses}",
            f"Pushes: {report.pushes}",
            f"Win Rate: {report.win_rate:.1%}",
            "",
            f"Total Wagered: ${report.total_wagered:.2f}",
            f"Total Payout: ${report.total_payout:.2f}",
            f"Profit/Loss: ${report.profit_loss:.2f}",
            f"ROI: {report.roi:.2f}%",
            "",
        ]
        
        if report.accuracy_metrics:
            lines.append("Additional Metrics:")
            for key, value in report.accuracy_metrics.items():
                lines.append(f"  {key}: {value:.3f}")
        
        lines.append("")
        lines.append("=" * 60)
        
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

