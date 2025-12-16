"""Data conversion utilities for converting between JSON, objects, and database models"""

import re
from typing import List, Optional, Dict, Any
from datetime import date

from src.data.models import (
    Game, Pick, BetType, CardReview, RevisionRequest, RevisionRequestType
)
from src.utils.logging import get_logger

logger = get_logger("orchestration.data_converter")


class DataConverter:
    """Centralized data conversion between JSON, objects, and database models"""
    
    @staticmethod
    def parse_game_id(game_id_str: str, games: List[Game]) -> Optional[int]:
        """
        Parse game_id from string, handling various formats
        
        Args:
            game_id_str: Game ID as string (could be int string or team name pattern)
            games: List of games to search for matching team names
            
        Returns:
            Parsed game_id or None if not found
        """
        if not game_id_str or game_id_str == "parlay":
            return None
        
        # Try direct ID match
        try:
            return int(game_id_str)
        except ValueError:
            # Try to match by team names in game_id string
            for game in games:
                if game_id_str in f"{game.team1}_{game.team2}" or game_id_str in f"{game.team2}_{game.team1}":
                    return game.id
        return None
    
    @staticmethod
    def parse_bet_type(bet_type_str: str) -> BetType:
        """
        Parse and validate bet type
        
        Args:
            bet_type_str: Bet type as string
            
        Returns:
            BetType enum value, defaults to SPREAD if invalid
        """
        if not bet_type_str or not bet_type_str.strip():
            return BetType.SPREAD  # Default if empty
        
        bet_type_str = bet_type_str.lower().strip()
        try:
            return BetType(bet_type_str)
        except ValueError:
            logger.warning(f"Invalid bet type: '{bet_type_str}', defaulting to SPREAD")
            return BetType.SPREAD
    
    @staticmethod
    def infer_bet_type_from_selection(selection: str) -> BetType:
        """
        Infer bet type from selection text
        
        Args:
            selection: Selection string (e.g., "Team A +3.5", "Over 152.5", "Team A +150")
            
        Returns:
            BetType enum value
        """
        if not selection:
            return BetType.SPREAD
        
        selection_lower = selection.lower()
        
        # Check for total indicators
        if "over" in selection_lower or "under" in selection_lower:
            return BetType.TOTAL
        
        # Check for moneyline indicators (odds in selection, or explicit ML mention)
        if "moneyline" in selection_lower or "ml" in selection_lower:
            return BetType.MONEYLINE
        
        # Check if it looks like odds (e.g., "+150", "-180")
        if re.search(r'[+-]\d{3,}', selection_lower):
            # If it's just odds without spread/total indicators, likely moneyline
            if "spread" not in selection_lower and "total" not in selection_lower:
                return BetType.MONEYLINE
        
        # Default to spread (most common)
        return BetType.SPREAD
    
    @staticmethod
    def parse_odds(odds_str: str) -> int:
        """
        Parse odds from string format (e.g., "-110", "+150")
        
        Args:
            odds_str: Odds as string (may be "market_unavailable" or similar)
            
        Returns:
            Odds as integer (negative for favorites, positive for underdogs)
            Defaults to -110 if odds are unavailable (standard market odds)
            
        Note:
            Defaulting to -110 may hide data quality issues. Consider investigating
            why odds are unavailable if this warning appears frequently.
        """
        odds_str_original = str(odds_str).strip()
        odds_str_lower = odds_str_original.lower()
        
        # Handle unavailable/missing odds
        if not odds_str_original or odds_str_lower in ["market_unavailable", "unavailable", "n/a", "na", "none", ""]:
            logger.warning(
                f"Odds unavailable ('{odds_str_original}'), defaulting to -110. "
                f"This may indicate a data quality issue - investigate missing odds data."
            )
            return -110
        
        try:
            # Parse the numeric value (remove + or - for parsing)
            odds = int(odds_str_original.replace("+", "").replace("-", ""))
            # Apply negative sign if original had it
            if "-" in odds_str_original or odds_str_original.startswith("-"):
                odds = -odds
            return odds
        except (ValueError, AttributeError) as e:
            logger.warning(
                f"Could not parse odds '{odds_str_original}', defaulting to -110: {e}. "
                f"This may indicate a data quality issue."
            )
            return -110
    
    @staticmethod
    def picks_from_json(candidate_picks: List[Dict[str, Any]], games: List[Game]) -> List[Pick]:
        """
        Convert JSON candidate picks from Picker to Pick objects
        
        Args:
            candidate_picks: List of pick dictionaries from Picker agent
            games: List of games for matching game_ids
            
        Returns:
            List of Pick objects
        """
        picks = []
        game_map = {g.id: g for g in games if g.id}
        
        for pick_data in candidate_picks:
            try:
                # Extract game_id (required field - must be valid)
                game_id_str = str(pick_data.get("game_id", ""))
                game_id = DataConverter.parse_game_id(game_id_str, games)
                if not game_id or game_id == 0:
                    logger.error(f"Invalid or missing game_id for pick: {pick_data}")
                    raise ValueError(f"Pick is missing required game_id field. game_id_str={game_id_str}")
                
                # Parse bet type
                bet_type = DataConverter.parse_bet_type(pick_data.get("bet_type", ""))
                
                # Parse odds
                odds_str = str(pick_data.get("odds", "-110"))
                odds = DataConverter.parse_odds(odds_str)
                
                # Get line from pick_data, or parse from selection_text
                selection_text = pick_data.get("selection", "")
                line = pick_data.get("line", 0.0)
                
                # If line is missing or 0.0, try to parse it from selection_text
                if not line or line == 0.0:
                    if selection_text:
                        if bet_type == BetType.TOTAL:
                            # For totals, look for "Over/Under NUMBER"
                            match = re.search(r'(?:over|under)\s+(\d+\.?\d*)', selection_text, re.IGNORECASE)
                            if match:
                                line = float(match.group(1))
                        elif bet_type == BetType.SPREAD:
                            # For spreads, look for "+/-NUMBER" pattern (e.g., "+13.5", "-15.5")
                            match = re.search(r'([+-]?\d+\.?\d*)', selection_text)
                            if match:
                                line = float(match.group(1))
                        # For moneylines, line stays 0.0 (odds are in the odds field, not line)
                    
                    if not line or line == 0.0:
                        logger.warning(
                            f"Pick for game_id={game_id} missing 'line' field and could not parse from selection_text: {selection_text}"
                        )
                
                # Extract team_name/team_id from pick data (for spread/moneyline bets)
                # Picker should provide "team_name" field, or we can extract from "selection"
                # We'll look up the team_id later when saving to database
                team_name = pick_data.get("team_name")
                if not team_name and selection_text and bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                    # Try to extract team name from selection text (e.g., "Team A +3.5" -> "Team A")
                    # Remove the line part (e.g., "+3.5", "-7.5", "Over 160.5")
                    line_pattern = r'\s*([+-]?(?:over|under)\s*)?[+-]?\d+\.?\d*'
                    team_name = re.sub(line_pattern, '', selection_text, flags=re.IGNORECASE).strip()
                    if not team_name:
                        team_name = None
                
                # team_id will be looked up when saving to database (based on team_name)
                team_id = None
                
                # Combine justification into rationale (required field)
                justification = pick_data.get("justification", [])
                if isinstance(justification, list):
                    rationale = " | ".join(justification) if justification else pick_data.get("notes", "")
                else:
                    rationale = str(justification) if justification else pick_data.get("notes", "")
                
                # Validate rationale is provided (required NOT NULL field)
                if not rationale or not rationale.strip():
                    logger.error(f"Pick for game_id={game_id} has empty rationale! justification={justification}, notes={pick_data.get('notes')}")
                    raise ValueError(f"Pick for game_id={game_id} is missing required rationale field")
                
                # Parse best_bet flag
                best_bet = pick_data.get("best_bet", False)
                
                # Get confidence_score if provided, otherwise derive from confidence (0.0-1.0)
                confidence_score = pick_data.get("confidence_score")
                if confidence_score is None:
                    # Derive confidence_score (1-10) from confidence (0.0-1.0)
                    confidence_value = pick_data.get("confidence", 0.5)
                    # Convert 0.0-1.0 to 1-10 scale
                    # 0.0 -> 1, 0.5 -> 5, 1.0 -> 10
                    confidence_score = max(1, min(10, int(round(confidence_value * 10))))
                    if confidence_value == 0.0:
                        confidence_score = 1
                # Ensure confidence_score is between 1-10
                confidence_score = max(1, min(10, int(confidence_score)))
                
                # Convert confidence_score (1-10) to confidence (0.0-1.0) for the Pick model
                # The Pick model expects confidence in 0.0-1.0 range, not 1-10
                # If confidence is explicitly provided in 0.0-1.0 range, use it; otherwise convert from confidence_score
                confidence_raw = pick_data.get("confidence")
                if confidence_raw is not None:
                    # Check if it's already in 0.0-1.0 range (should be <= 1.0)
                    confidence_value = float(confidence_raw)
                    if confidence_value <= 1.0:
                        # Already in correct range (0.0-1.0)
                        confidence = max(0.0, min(1.0, confidence_value))
                    else:
                        # Likely a confidence_score value (1-10) incorrectly provided as "confidence"
                        # Convert it: divide by 10 and clamp
                        confidence = max(0.0, min(1.0, confidence_value / 10.0))
                else:
                    # No confidence provided, convert from confidence_score (1-10) to (0.0-1.0)
                    # confidence_score 1 -> 0.1, 5 -> 0.5, 10 -> 1.0
                    confidence = max(0.0, min(1.0, confidence_score / 10.0))
                
                # Extract book field, defaulting to "draftkings" if missing, None, or empty
                book_value = pick_data.get("book", "draftkings")
                if not book_value or (isinstance(book_value, str) and not book_value.strip()):
                    book_value = "draftkings"
                
                pick = Pick(
                    game_id=game_id,
                    bet_type=bet_type,
                    line=float(line),
                    odds=odds,
                    rationale=rationale,
                    confidence=confidence,
                    expected_value=float(pick_data.get("edge_estimate", 0.0)),
                    book=book_value,
                    selection_text=selection_text,
                    team_name=team_name,  # Keep for backwards compatibility
                    team_id=team_id,  # Will be set when saving to database
                    best_bet=best_bet,
                    confidence_score=confidence_score,
                    parlay_legs=None  # Will be set later if parlay
                )
                picks.append(pick)
            except Exception as e:
                logger.error(f"Error converting pick from JSON: {e}, pick_data: {pick_data}", exc_info=True)
        
        return picks
    
    @staticmethod
    def picks_to_dict(picks: List[Pick]) -> List[Dict[str, Any]]:
        """
        Convert Pick objects to dict format for agents
        
        Args:
            picks: List of Pick objects
            
        Returns:
            List of pick dictionaries
        """
        return [
            {
                "game_id": str(pick.game_id),
                "bet_type": pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type),
                "selection": f"{pick.line:+.1f}" if pick.line else "",
                "odds": str(pick.odds),
                "units": pick.stake_units,
                "edge_estimate": pick.expected_value,
                "confidence": pick.confidence,
                "confidence_score": pick.confidence_score,
                "best_bet": pick.best_bet,
                "book": pick.book,
                "rationale": pick.rationale
            }
            for pick in picks
        ]
    
    @staticmethod
    def card_review_from_json(
        president_response: Dict[str, Any],
        picks: List[Pick],
        target_date: date
    ) -> CardReview:
        """
        Convert President's JSON response to CardReview object
        
        Args:
            president_response: JSON response from President agent
            picks: List of Pick objects for matching
            target_date: Target date for the review
            
        Returns:
            CardReview object
        """
        # Create a map of picks by (game_id, bet_type) for matching
        # Only one pick per game per day, so game_id + bet_type is sufficient
        pick_map = {}
        for pick in picks:
            key = (pick.game_id, pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type))
            # There should only be one pick per game, but if there are duplicates, keep the first one
            if key not in pick_map:
                pick_map[key] = pick
        
        # Extract approved pick IDs and update best_bet flags
        approved_pick_ids = []
        best_bet_pick_ids = set()  # Track which picks are marked as best bets
        approved_picks_data = president_response.get("approved_picks", [])
        for approved_data in approved_picks_data:
            try:
                game_id_str = str(approved_data.get("game_id", ""))
                bet_type_str = approved_data.get("bet_type", "").lower()
                best_bet = approved_data.get("best_bet", False)  # Get best_bet flag from President's response
                
                # Match pick by game_id and bet_type only (line is stored in pick, not parsed from text)
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                    bet_type = DataConverter.parse_bet_type(bet_type_str)
                    key = (game_id, bet_type.value if hasattr(bet_type, 'value') else str(bet_type))
                    matched_pick = pick_map.get(key)
                    if matched_pick and matched_pick.id:
                        approved_pick_ids.append(matched_pick.id)
                        # Update best_bet flag on the pick object
                        matched_pick.best_bet = best_bet
                        if best_bet:
                            best_bet_pick_ids.add(matched_pick.id)
                except (ValueError, KeyError) as e:
                    logger.debug(f"Could not match approved pick: game_id={game_id_str}, bet_type={bet_type_str}, error={e}")
                    pass
            except Exception as e:
                logger.error(f"Error matching approved pick: {e}")
        
        # All picks are approved in new format - no rejected picks
        rejected_pick_ids = []
        
        # CRITICAL: Ensure ALL picks that are NOT explicitly marked as best_bet=True by President have best_bet=False
        for pick in picks:
            if pick.id and pick.id not in best_bet_pick_ids:
                pick.best_bet = False
        
        # Extract strategy notes from daily_report_summary
        daily_report_summary = president_response.get("daily_report_summary", {})
        strategy_notes = daily_report_summary.get("strategic_notes", [])
        if isinstance(strategy_notes, list):
            review_notes = " | ".join(strategy_notes)
        else:
            review_notes = str(strategy_notes) if strategy_notes else ""
        
        # All picks are approved in new format
        approved = len(approved_pick_ids) > 0
        
        return CardReview(
            date=target_date,
            approved=approved,
            picks_approved=approved_pick_ids,
            picks_rejected=rejected_pick_ids,
            review_notes=review_notes,
            strategic_directives={},
            revision_requests=[]  # No revision requests in new format
        )
