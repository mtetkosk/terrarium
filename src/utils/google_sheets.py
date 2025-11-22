"""Google Sheets integration for tracking betting results"""

import os
from datetime import date, datetime
from typing import List, Dict, Any, Optional
import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy import func

from src.data.storage import Database, PickModel, BetModel, GameModel, BetType, BetResult
from src.data.analytics import AnalyticsService
from src.data.analytics import AnalyticsPredictionModel, AnalyticsResultModel, AnalyticsGameModel
from src.utils.logging import get_logger
from src.utils.config import config

logger = get_logger("utils.google_sheets")


class GoogleSheetsService:
    """Service for writing betting results to Google Sheets"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Google Sheets service"""
        self.db = db or Database()
        self.analytics_service = AnalyticsService(self.db)
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize Google Sheets client"""
        try:
            # Get credentials from environment variable or config
            credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
            if not credentials_path:
                logger.warning("GOOGLE_SHEETS_CREDENTIALS_PATH not set. Google Sheets integration disabled.")
                return
            
            if not os.path.exists(credentials_path):
                logger.warning(f"Google Sheets credentials file not found at {credentials_path}")
                return
            
            # Authenticate
            scope = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(credentials_path, scopes=scope)
            self.client = gspread.authorize(creds)
            logger.info("Google Sheets client initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Google Sheets client: {e}")
            self.client = None
    
    def write_picks_to_sheet(
        self,
        target_date: date,
        spreadsheet_id: Optional[str] = None,
        worksheet_name: str = "Betting Results"
    ) -> bool:
        """
        Write picks and results to Google Sheets
        
        Args:
            target_date: Date to write picks for
            spreadsheet_id: Google Sheets spreadsheet ID (from env or config)
            worksheet_name: Name of the worksheet to write to
            
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            logger.warning("Google Sheets client not initialized. Skipping sheet write.")
            return False
        
        if not spreadsheet_id:
            spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
            if not spreadsheet_id:
                logger.warning("GOOGLE_SHEETS_SPREADSHEET_ID not set. Cannot write to sheet.")
                return False
        
        try:
            # Open spreadsheet
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            
            # Get or create worksheet
            try:
                worksheet = spreadsheet.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=10)
                # Write headers
                headers = ["Date", "Game ID", "Bet Type", "Team", "Odds", "Projected", "Actual", "Win/Loss"]
                worksheet.append_row(headers)
                logger.info(f"Created new worksheet: {worksheet_name}")
            
            # Get picks for the date
            session = self.db.get_session()
            try:
                picks = session.query(PickModel).filter(
                    func.date(PickModel.created_at) == target_date
                ).all()
                
                if not picks:
                    logger.info(f"No picks found for {target_date}")
                    return True
                
                # Prepare rows
                rows = []
                for pick in picks:
                    row = self._pick_to_row(pick, target_date, session)
                    if row:
                        rows.append(row)
                
                # Write rows to sheet
                if rows:
                    worksheet.append_rows(rows)
                    logger.info(f"Wrote {len(rows)} picks to Google Sheets for {target_date}")
                    return True
                else:
                    logger.warning(f"No valid rows to write for {target_date}")
                    return False
                    
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error writing to Google Sheets: {e}", exc_info=True)
            return False
    
    def _pick_to_row(
        self,
        pick: PickModel,
        pick_date: date,
        session
    ) -> Optional[List[Any]]:
        """
        Convert a pick to a row for Google Sheets
        
        Args:
            pick: Pick model
            pick_date: Date of the pick
            session: Database session
            
        Returns:
            List representing a row, or None if invalid
        """
        try:
            # Get game info
            game = session.query(GameModel).filter_by(id=pick.game_id).first()
            if not game:
                return None
            
            # Get analytics data
            analytics_game = session.query(AnalyticsGameModel).filter_by(
                game_id=pick.game_id,
                date=pick_date
            ).first()
            
            analytics_prediction = session.query(AnalyticsPredictionModel).filter_by(
                game_id=pick.game_id,
                date=pick_date
            ).first()
            
            analytics_result = session.query(AnalyticsResultModel).filter_by(
                game_id=pick.game_id,
                date=pick_date
            ).first()
            
            # Get bet result
            bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
            win_loss = None
            if bet and bet.result:
                if bet.result == BetResult.WIN:
                    win_loss = "Win"
                elif bet.result == BetResult.LOSS:
                    win_loss = "Loss"
                elif bet.result == BetResult.PUSH:
                    win_loss = "Push"
                else:
                    win_loss = "Pending"
            
            # Determine team name
            team = None
            if pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                # Extract team from selection_text or rationale
                if pick.selection_text:
                    # Try to extract team name from selection (e.g., "Team A +3.5")
                    selection = pick.selection_text
                    # Remove spread/odds info
                    selection = selection.replace(f"{pick.line:+.1f}", "").strip()
                    # Remove common betting terms
                    selection = selection.replace("over", "").replace("under", "").strip()
                    team = selection
                elif analytics_game:
                    # Try to determine from line sign and home/away
                    if pick.line < 0:
                        # Negative line typically means home team
                        team = analytics_game.home_team
                    elif pick.line > 0:
                        # Positive line typically means away team
                        team = analytics_game.away_team
                    else:
                        # Line is 0, try to infer from rationale
                        if pick.rationale:
                            rationale_lower = pick.rationale.lower()
                            if analytics_game.home_team.lower() in rationale_lower:
                                team = analytics_game.home_team
                            elif analytics_game.away_team.lower() in rationale_lower:
                                team = analytics_game.away_team
            elif pick.bet_type == BetType.TOTAL:
                # For totals, show "Over" or "Under" in team column
                if pick.selection_text:
                    selection_lower = pick.selection_text.lower()
                    if "over" in selection_lower:
                        team = "Over"
                    elif "under" in selection_lower:
                        team = "Under"
                elif pick.rationale:
                    rationale_lower = pick.rationale.lower()
                    if "over" in rationale_lower and "under" not in rationale_lower:
                        team = "Over"
                    elif "under" in rationale_lower:
                        team = "Under"
            
            # Calculate projected value
            projected = None
            if analytics_prediction:
                if pick.bet_type == BetType.SPREAD:
                    # Projected spread for the team
                    if team and analytics_game:
                        if team == analytics_game.home_team:
                            projected = analytics_prediction.projected_spread
                        elif team == analytics_game.away_team:
                            projected = -analytics_prediction.projected_spread
                elif pick.bet_type == BetType.TOTAL:
                    projected = analytics_prediction.projected_total
                elif pick.bet_type == BetType.MONEYLINE:
                    # For moneyline, we could show win probability or projected score
                    if team and analytics_game:
                        if team == analytics_game.home_team:
                            projected = analytics_prediction.home_projected_score
                        elif team == analytics_game.away_team:
                            projected = analytics_prediction.away_projected_score
            
            # Calculate actual value
            actual = None
            if analytics_result:
                if pick.bet_type == BetType.SPREAD:
                    # Actual spread
                    if analytics_result.home_actual_score is not None and analytics_result.away_actual_score is not None:
                        if team and analytics_game:
                            if team == analytics_game.home_team:
                                actual = float(analytics_result.home_actual_score - analytics_result.away_actual_score)
                            elif team == analytics_game.away_team:
                                actual = float(analytics_result.away_actual_score - analytics_result.home_actual_score)
                elif pick.bet_type == BetType.TOTAL:
                    if analytics_result.actual_total is not None:
                        actual = float(analytics_result.actual_total)
                elif pick.bet_type == BetType.MONEYLINE:
                    # For moneyline, show actual score
                    if team and analytics_game:
                        if team == analytics_game.home_team and analytics_result.home_actual_score is not None:
                            actual = float(analytics_result.home_actual_score)
                        elif team == analytics_game.away_team and analytics_result.away_actual_score is not None:
                            actual = float(analytics_result.away_actual_score)
            
            # Format bet type
            bet_type_str = pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)
            
            # Format odds
            odds_str = f"{pick.odds:+d}"
            
            # Build row
            row = [
                pick_date.isoformat(),  # Date
                pick.game_id,  # Game ID
                bet_type_str,  # Bet Type
                team or "",  # Team (empty for totals)
                odds_str,  # Odds
                f"{projected:.1f}" if projected is not None else "",  # Projected
                f"{actual:.1f}" if actual is not None else "",  # Actual
                win_loss or "Pending"  # Win/Loss
            ]
            
            return row
            
        except Exception as e:
            logger.error(f"Error converting pick to row: {e}", exc_info=True)
            return None

