"""Google Sheets integration for tracking betting results"""

import os
from datetime import date, datetime
from typing import List, Dict, Any, Optional
import requests
import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy import func

from src.data.storage import Database, PickModel, BetModel, GameModel, PredictionModel, BetType, BetResult
from src.utils.logging import get_logger
from src.utils.config import config

logger = get_logger("utils.google_sheets")


class GoogleSheetsService:
    """Service for writing betting results to Google Sheets"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Google Sheets service"""
        self.db = db or Database()
        self.client = None
        self.api_key = None
        self.use_api_key = False
        self._initialize_client()
    
    def _initialize_client(self) -> None:
        """Initialize Google Sheets client"""
        try:
            # Prefer service account credentials (required for private sheets)
            credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
            if credentials_path and os.path.exists(credentials_path):
                # Authenticate with service account
                scope = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/drive'
                ]
                creds = Credentials.from_service_account_file(credentials_path, scopes=scope)
                self.client = gspread.authorize(creds)
                self.use_api_key = False
                logger.info("Google Sheets client initialized with service account credentials")
                return
            
            # Fall back to API key (only works for public sheets)
            api_key = os.getenv('GOOGLE_SHEETS_API_KEY')
            if api_key:
                self.api_key = api_key
                self.use_api_key = True
                logger.warning(
                    "Using API key authentication. Note: API keys can only access public sheets. "
                    "For private sheets, use service account credentials (GOOGLE_SHEETS_CREDENTIALS_PATH)."
                )
                return
            
            logger.warning(
                "Neither GOOGLE_SHEETS_CREDENTIALS_PATH nor GOOGLE_SHEETS_API_KEY set. "
                "Google Sheets integration disabled. "
                "For private sheets, set GOOGLE_SHEETS_CREDENTIALS_PATH to a service account JSON file."
            )
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
        if not spreadsheet_id:
            spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
            if not spreadsheet_id:
                logger.warning("GOOGLE_SHEETS_SPREADSHEET_ID not set. Cannot write to sheet.")
                return False
        
        # Use API key method if available
        if self.use_api_key and self.api_key:
            return self._write_with_api_key(spreadsheet_id, worksheet_name, target_date)
        
        # Use gspread with service account
        if not self.client:
            logger.warning("Google Sheets client not initialized. Skipping sheet write.")
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
                headers = ["Date", "Game ID", "Bet Type", "Team", "Bet", "Odds", "Projected", "Actual", "Win/Loss", "Game Result", "Best Bet", "Confidence Score"]
                worksheet.append_row(headers)
                logger.info(f"Created new worksheet: {worksheet_name}")
            
            # Upsert: Delete existing rows for this date, then insert new ones
            date_str = target_date.isoformat()
            try:
                all_values = worksheet.get_all_values()
                if len(all_values) > 1:  # Skip header row
                    # Find row indices (1-indexed in gspread) for rows with this date
                    rows_to_delete = []
                    for idx, row in enumerate(all_values[1:], start=2):  # Start at 2 (skip header)
                        if row and row[0] == date_str:
                            rows_to_delete.append(idx)
                    
                    # Delete rows in reverse order to maintain indices
                    if rows_to_delete:
                        for row_idx in reversed(rows_to_delete):
                            worksheet.delete_rows(row_idx)
                        logger.info(f"Deleted {len(rows_to_delete)} existing row(s) for {target_date} before inserting new data")
            except Exception as e:
                logger.warning(f"Error upserting rows for {target_date}: {e}. Proceeding with insert.")
            
            # Get picks for the date using analytics service
            picks = self.db.get_picks_for_date(target_date)
            
            if not picks:
                logger.info(f"No picks found for {target_date}")
                return True
            
            # Log best_bet statistics
            best_bet_count = sum(1 for p in picks if p.best_bet)
            logger.info(f"Writing {len(picks)} picks to Google Sheets for {target_date} ({best_bet_count} best bets, {len(picks) - best_bet_count} others)")
            
            # Prepare rows
            session = self.db.get_session()
            try:
                rows = []
                skipped_count = 0
                for pick in picks:
                    try:
                        row = self._pick_to_row(pick, target_date, session)
                        if row:
                            rows.append(row)
                        else:
                            skipped_count += 1
                            logger.debug(f"Skipping pick {pick.id} (game_id={pick.game_id}): _pick_to_row returned None")
                    except Exception as e:
                        skipped_count += 1
                        logger.error(f"Error converting pick {pick.id} to row: {e}", exc_info=True)
                
                if skipped_count > 0:
                    logger.warning(f"Skipped {skipped_count} picks due to errors or invalid data")
                
                # Write rows to sheet
                if rows:
                    worksheet.append_rows(rows)
                    logger.info(f"Wrote {len(rows)} picks to Google Sheets for {target_date}")
                    return True
                else:
                    logger.warning(f"No valid rows to write for {target_date} (tried {len(picks)} picks, {skipped_count} skipped)")
                    return False
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error writing to Google Sheets: {e}", exc_info=True)
            return False
    
    def _write_with_api_key(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        target_date: date
    ) -> bool:
        """Write picks to sheet using API key (REST API)
        
        Note: API keys can only read/write public sheets. For private sheets,
        you must use service account credentials (GOOGLE_SHEETS_CREDENTIALS_PATH).
        """
        try:
            # Get picks for the date using analytics service
            picks = self.db.get_picks_for_date(target_date)
            
            if not picks:
                logger.info(f"No picks found for {target_date}")
                return True
            
            # Log best_bet statistics
            best_bet_count = sum(1 for p in picks if p.best_bet)
            logger.info(f"Writing {len(picks)} picks to Google Sheets for {target_date} ({best_bet_count} best bets, {len(picks) - best_bet_count} others)")
            
            # Prepare rows
            session = self.db.get_session()
            try:
                rows = []
                skipped_count = 0
                for pick in picks:
                    try:
                        row = self._pick_to_row(pick, target_date, session)
                        if row:
                            rows.append(row)
                        else:
                            skipped_count += 1
                            logger.debug(f"Skipping pick {pick.id} (game_id={pick.game_id}): _pick_to_row returned None")
                    except Exception as e:
                        skipped_count += 1
                        logger.error(f"Error converting pick {pick.id} to row: {e}", exc_info=True)
                
                if skipped_count > 0:
                    logger.warning(f"Skipped {skipped_count} picks due to errors or invalid data")
                
                if not rows:
                    logger.warning(f"No valid rows to write for {target_date} (tried {len(picks)} picks, {skipped_count} skipped)")
                    return False
            finally:
                session.close()
            
            # First, check if worksheet exists and get its ID
            sheets_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            response = requests.get(f"{sheets_url}?key={self.api_key}")
            
            if response.status_code == 403:
                logger.error(
                    "403 Forbidden: API keys cannot write to private Google Sheets. "
                    "You must use service account credentials. "
                    "Please set GOOGLE_SHEETS_CREDENTIALS_PATH to a service account JSON file. "
                    "See GOOGLE_SHEETS_SETUP.md for instructions."
                )
                return False
            
            response.raise_for_status()
            spreadsheet_data = response.json()
            
            # Find or create worksheet
            worksheet_id = None
            for sheet in spreadsheet_data.get('sheets', []):
                if sheet['properties']['title'] == worksheet_name:
                    worksheet_id = sheet['properties']['sheetId']
                    break
            
            if not worksheet_id:
                # Create worksheet
                create_url = f"{sheets_url}:batchUpdate"
                create_payload = {
                    "requests": [{
                        "addSheet": {
                            "properties": {
                                "title": worksheet_name,
                                "gridProperties": {
                                    "rowCount": 1000,
                                    "columnCount": 10
                                }
                            }
                        }
                    }]
                }
                response = requests.post(f"{create_url}?key={self.api_key}", json=create_payload)
                response.raise_for_status()
                worksheet_id = response.json()['replies'][0]['addSheet']['properties']['sheetId']
                
                # Write headers
                headers = ["Date", "Game ID", "Bet Type", "Team", "Bet", "Odds", "Projected", "Actual", "Win/Loss", "Game Result", "Best Bet", "Confidence Score"]
                self._append_row_api(spreadsheet_id, worksheet_name, headers)
                logger.info(f"Created new worksheet: {worksheet_name}")
            
            # Check if rows for this date already exist
            date_str = target_date.isoformat()
            try:
                # Read existing values from the sheet
                read_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{worksheet_name}"
                read_response = requests.get(f"{read_url}?key={self.api_key}")
                read_response.raise_for_status()
                existing_data = read_response.json()
                
                # Upsert: Delete existing rows for this date using batchUpdate
                if 'values' in existing_data and len(existing_data['values']) > 1:  # Skip header row
                    # Find row indices (1-indexed) for rows with this date
                    rows_to_delete = []
                    for idx, row in enumerate(existing_data['values'][1:], start=2):  # Start at 2 (skip header)
                        if row and row[0] == date_str:
                            rows_to_delete.append(idx)
                    
                    # Delete rows using batchUpdate
                    if rows_to_delete:
                        delete_requests = [
                            {
                                "deleteDimension": {
                                    "range": {
                                        "sheetId": worksheet_id,
                                        "dimension": "ROWS",
                                        "startIndex": row_idx - 1,  # 0-indexed
                                        "endIndex": row_idx
                                    }
                                }
                            }
                            for row_idx in reversed(rows_to_delete)  # Delete in reverse to maintain indices
                        ]
                        batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
                        batch_body = {"requests": delete_requests}
                        batch_response = requests.post(f"{batch_url}?key={self.api_key}", json=batch_body)
                        batch_response.raise_for_status()
                        logger.info(f"Deleted {len(rows_to_delete)} existing row(s) for {target_date} before inserting new data")
            except Exception as e:
                logger.warning(f"Error upserting rows for {target_date}: {e}. Proceeding with insert.")
            
            # Append rows
            for row in rows:
                self._append_row_api(spreadsheet_id, worksheet_name, row)
            
            logger.info(f"Wrote {len(rows)} best_bet picks to Google Sheets for {target_date}")
            return True
                
        except Exception as e:
            logger.error(f"Error writing to Google Sheets with API key: {e}", exc_info=True)
            return False
    
    def _append_row_api(self, spreadsheet_id: str, worksheet_name: str, row: List[Any]) -> None:
        """Append a row using the REST API"""
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{worksheet_name}:append"
        params = {
            "key": self.api_key,
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS"
        }
        body = {
            "values": [row]
        }
        response = requests.post(url, params=params, json=body)
        response.raise_for_status()
    
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
                logger.warning(f"Pick {pick.id} has invalid game_id {pick.game_id}: game not found in database")
                return None
            
            # Use game date instead of pick_date for analytics queries
            # Analytics tables use game_date (when game was played), not pick creation date
            game_date = game.date
            
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
            
            # Determine team names - always use utility function with database game model
            from src.utils.team_normalizer import get_home_away_team_names
            home_team, away_team = get_home_away_team_names(
                game.team1_id,
                game.team2_id,
                game.result,  # Use game.result if available, None otherwise
                session,
                fallback_team1_is_home=True  # Fallback: assume team1=home if can't determine
            )
            
            # Build the "Bet" column - the full bet description
            bet_description = ""
            if pick.selection_text:
                bet_description = pick.selection_text
            elif pick.bet_type == BetType.TOTAL:
                # For totals, construct from rationale or line
                if pick.rationale:
                    rationale_lower = pick.rationale.lower()
                    if "over" in rationale_lower and "under" not in rationale_lower:
                        bet_description = f"Over {pick.line:.1f}"
                    elif "under" in rationale_lower:
                        bet_description = f"Under {pick.line:.1f}"
                    else:
                        bet_description = f"Over {pick.line:.1f}"  # Default
                else:
                    bet_description = f"Over {pick.line:.1f}"
            elif pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                # For spread/moneyline, we need to determine the team first
                # Then construct "Team Name +3.5" or "Team Name -142"
                pass  # Will fill this after determining team
            
            team = None
            if pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                # Extract team from selection_text - the team that was bet on
                if pick.selection_text:
                    selection = pick.selection_text
                    # The selection should be like "Team Name +3.5" or "Team Name -142"
                    # Find the team name by looking for the part before the line/odds
                    import re
                    # Pattern to match line/odds: +3.5, -1.5, -142, etc.
                    line_pattern = r'[+-]?\d+\.?\d*'
                    # Find where the line appears in the selection
                    match = re.search(line_pattern, selection)
                    if match:
                        # Team name is everything before the line
                        team = selection[:match.start()].strip()
                        # Remove "vs" and opponent name if present
                        if " vs " in team:
                            team = team.split(" vs ")[0].strip()
                        if " @ " in team:
                            team = team.split(" @ ")[0].strip()
                    else:
                        # No line found, try to extract team name
                        # Remove common betting terms
                        team = selection.replace("ML", "").replace("moneyline", "").strip()
                        if " vs " in team:
                            team = team.split(" vs ")[0].strip()
                        if " @ " in team:
                            team = team.split(" @ ")[0].strip()
                
                # If we still don't have a team, infer from line and bet type
                if not team and pick.bet_type == BetType.SPREAD:
                    # For spread bets, match the pick's line to the betting line to get the team
                    from src.data.storage import BettingLineModel
                    # Query all betting lines for this game and bet type
                    betting_lines = session.query(BettingLineModel).filter(
                        BettingLineModel.game_id == pick.game_id,
                        BettingLineModel.bet_type == BetType.SPREAD
                    ).all()
                    
                    # Try to match by book first, then by line value
                    matched_line = None
                    for bl in betting_lines:
                        # Match if book matches and line matches (absolute value for spread)
                        if bl.book == pick.book and abs(bl.line - pick.line) < 0.1:
                            matched_line = bl
                            break
                    
                    # If no match by book, try matching just by line value
                    if not matched_line:
                        for bl in betting_lines:
                            if abs(bl.line - pick.line) < 0.1:
                                matched_line = bl
                                break
                    
                    if matched_line and matched_line.team:
                        # Use the team from the matched betting line
                        line_team = matched_line.team
                        # Match the team name to home_team or away_team
                        from src.utils.team_normalizer import normalize_team_name
                        norm_line = normalize_team_name(line_team, for_matching=True)
                        norm_home = normalize_team_name(home_team, for_matching=True)
                        norm_away = normalize_team_name(away_team, for_matching=True)
                        
                        # Try exact match first
                        if line_team.lower() == home_team.lower():
                            team = home_team
                        elif line_team.lower() == away_team.lower():
                            team = away_team
                        # Try normalized match
                        elif norm_line == norm_home or (norm_line and norm_home and norm_line in norm_home):
                            team = home_team
                        elif norm_line == norm_away or (norm_line and norm_away and norm_line in norm_away):
                            team = away_team
                        # Try partial match
                        elif home_team.lower() in line_team.lower() or line_team.lower() in home_team.lower():
                            team = home_team
                        elif away_team.lower() in line_team.lower() or line_team.lower() in away_team.lower():
                            team = away_team
                        else:
                            # Can't match exactly, but try to use full team name from game
                            # Check if line_team is an abbreviation or variation
                            # If line_team contains key words from home/away, use the full name
                            line_lower = line_team.lower()
                            if any(word in line_lower for word in home_team.lower().split() if len(word) > 3):
                                team = home_team
                            elif any(word in line_lower for word in away_team.lower().split() if len(word) > 3):
                                team = away_team
                            else:
                                # Last resort: use the team name from betting line
                                team = line_team
                    
                    # If still no team, infer from line sign
                    if not team:
                        # Positive line typically means away team (underdog)
                        # Negative line typically means home team (favorite)
                        if pick.line > 0:
                            # Positive line = team getting points = likely underdog = away team
                            team = away_team
                        elif pick.line < 0:
                            # Negative line = team giving points = likely favorite = home team
                            team = home_team
                        else:
                            # Pick'em - can't determine, default to away
                            team = away_team
                
                # If we still don't have a team, try rationale (but be careful - rationale might mention the favorite)
                if not team and pick.rationale:
                    rationale_lower = pick.rationale.lower()
                    # Look for keywords that indicate which side was bet
                    # "underdog", "dog", "getting points", "+" with number, "big number"
                    is_underdog_bet = any(phrase in rationale_lower for phrase in [
                        "underdog", "dog", "getting points", "big number", 
                        f"+{pick.line:.1f}", f"+{int(pick.line)}"
                    ])
                    is_favorite_bet = any(phrase in rationale_lower for phrase in [
                        "favorite", "favored", "laying points", "giving points",
                        f"-{pick.line:.1f}", f"-{int(pick.line)}"
                    ])
                    
                    if is_underdog_bet and not is_favorite_bet:
                        # Betting on underdog = away team typically
                        team = away_team
                    elif is_favorite_bet and not is_underdog_bet:
                        # Betting on favorite = home team typically
                        team = home_team
                    else:
                        # Ambiguous - try to match team names but prefer the one that makes sense with the line
                        # Check for team names in rationale
                        if home_team.lower() in rationale_lower:
                            # If home team mentioned, check if line makes sense
                            if pick.line < 0:  # Negative line = favorite = home team makes sense
                                team = home_team
                            elif pick.line > 0:  # Positive line = underdog, but home mentioned - might be wrong
                                # Check if away team also mentioned
                                if away_team.lower() not in rationale_lower:
                                    team = home_team  # Only home mentioned, use it
                        elif away_team.lower() in rationale_lower:
                            # If away team mentioned, check if line makes sense
                            if pick.line > 0:  # Positive line = underdog = away team makes sense
                                team = away_team
                            elif pick.line < 0:  # Negative line = favorite, but away mentioned - might be wrong
                                # Check if home team also mentioned
                                if home_team.lower() not in rationale_lower:
                                    team = away_team  # Only away mentioned, use it
                
                # Build bet description if we have team
                if team and not bet_description:
                    if pick.bet_type == BetType.SPREAD:
                        bet_description = f"{team} {pick.line:+.1f}"
                    elif pick.bet_type == BetType.MONEYLINE:
                        bet_description = f"{team} {pick.odds:+d}"
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
            
            # Calculate projected value from PredictionModel
            projected = None
            prediction = session.query(PredictionModel).filter_by(
                game_id=pick.game_id
            ).order_by(PredictionModel.created_at.desc()).first()
            
            if not prediction:
                logger.debug(f"No PredictionModel found for game_id={pick.game_id}, pick_id={pick.id}")
            
            if prediction:
                    if pick.bet_type == BetType.SPREAD:
                        # Projected spread for the team
                        if team:
                            if team == home_team:
                                # Home team spread (negative means home favored)
                                projected = prediction.predicted_spread
                            elif team == away_team:
                                # Away team spread (positive means away getting points)
                                projected = -prediction.predicted_spread
                    elif pick.bet_type == BetType.TOTAL:
                        if prediction.predicted_total is not None:
                            projected = prediction.predicted_total
                        else:
                            logger.debug(f"prediction.predicted_total is None for game_id={pick.game_id}, pick_id={pick.id}")
                    elif pick.bet_type == BetType.MONEYLINE:
                        # For moneyline, calculate projected score from spread and total
                        if prediction.predicted_total is not None and prediction.predicted_spread is not None:
                            if team:
                                # Formula: home_score = (total + spread) / 2, away_score = (total - spread) / 2
                                home_projected = (prediction.predicted_total + prediction.predicted_spread) / 2.0
                                away_projected = (prediction.predicted_total - prediction.predicted_spread) / 2.0
                                if team == home_team:
                                    projected = home_projected
                                elif team == away_team:
                                    projected = away_projected
            
            # Get mapped scores once - always use game.result for consistency
            mapped_home_score = None
            mapped_away_score = None
            if game.result:
                # Use utility to get correctly mapped scores (relative to our determined home/away teams)
                from src.utils.team_normalizer import get_home_away_scores
                mapped_home_score, mapped_away_score = get_home_away_scores(
                    game.team1_id,
                    game.team2_id,
                    game.result,
                    session,
                    fallback_team1_is_home=True
                )
            
            # Calculate actual value using mapped scores
            actual = None
            if mapped_home_score is not None and mapped_away_score is not None:
                if pick.bet_type == BetType.SPREAD:
                    # Actual spread
                    if team:
                        if team == home_team:
                            actual = float(mapped_home_score - mapped_away_score)
                        elif team == away_team:
                            actual = float(mapped_away_score - mapped_home_score)
                    else:
                        logger.debug(f"No team determined for spread bet, game_id={pick.game_id}, pick_id={pick.id}")
                elif pick.bet_type == BetType.TOTAL:
                    actual = float(mapped_home_score + mapped_away_score)
                elif pick.bet_type == BetType.MONEYLINE:
                    # For moneyline, show actual score
                    if team:
                        if team == home_team:
                            actual = float(mapped_home_score)
                        elif team == away_team:
                            actual = float(mapped_away_score)
                    else:
                        logger.debug(f"No team determined for moneyline bet, game_id={pick.game_id}, pick_id={pick.id}")
            elif game.result:
                logger.debug(f"Could not get mapped scores for game_id={pick.game_id}, pick_id={pick.id}")
            
            # Format bet type
            bet_type_str = pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)
            
            # Format odds
            odds_str = f"{pick.odds:+d}"
            
            # Calculate confidence_score from confidence (0.0-1.0) to confidence_score (1-10)
            confidence_value = pick.confidence or 0.5
            if confidence_value == 0.0:
                confidence_score = 1
            else:
                # Convert 0.0-1.0 to 1-10 scale: 0.1->1, 0.3->3, 0.5->5, 0.7->7, 1.0->10
                confidence_score = max(1, min(10, int(round(confidence_value * 10))))
            
            # Build bet description if not already set (for totals)
            if not bet_description and pick.bet_type == BetType.TOTAL:
                if "over" in (pick.selection_text or "").lower() or ("over" in (pick.rationale or "").lower() and "under" not in (pick.rationale or "").lower()):
                    bet_description = f"Over {pick.line:.1f}"
                elif "under" in (pick.selection_text or "").lower() or "under" in (pick.rationale or "").lower():
                    bet_description = f"Under {pick.line:.1f}"
                else:
                    bet_description = f"Over {pick.line:.1f}"  # Default
            
            # Format game result string - use the mapped scores we already calculated
            game_result = ""
            if mapped_home_score is not None and mapped_away_score is not None:
                # Format as "Away Team Score - Home Team Score"
                game_result = f"{away_team} {mapped_away_score} - {home_team} {mapped_home_score}"
            
            # Build row
            row = [
                pick_date.isoformat(),  # Date
                pick.game_id,  # Game ID
                bet_type_str,  # Bet Type
                team or "",  # Team (empty for totals)
                bet_description or "",  # Bet (full bet description)
                odds_str,  # Odds
                f"{projected:.1f}" if projected is not None else "",  # Projected
                f"{actual:.1f}" if actual is not None else "",  # Actual
                win_loss or "Pending",  # Win/Loss
                game_result,  # Game Result (e.g., "Long Island 27 - Illinois 40")
                "Yes" if pick.best_bet else "No",  # Best Bet (for filtering in analytics)
                confidence_score  # Confidence Score (1-10)
            ]
            
            return row
            
        except Exception as e:
            logger.error(f"Error converting pick to row: {e}", exc_info=True)
            return None

