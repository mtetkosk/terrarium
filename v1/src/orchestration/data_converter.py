"""Data conversion utilities for converting between JSON, objects, and database models"""

import re
from typing import Any, Dict, List, Optional
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
    def parse_odds(odds_str: str) -> Optional[int]:
        """
        Parse odds from string format (e.g., "-110", "+150")
        
        Args:
            odds_str: Odds as string (may be "market_unavailable" or similar)
            
        Returns:
            Odds as integer (negative for favorites, positive for underdogs), or None if
            unavailable/unparseable. Callers should skip the pick when None is returned.
        """
        odds_str_original = str(odds_str).strip()
        odds_str_lower = odds_str_original.lower()
        
        # Handle unavailable/missing odds
        if not odds_str_original or odds_str_lower in ["market_unavailable", "unavailable", "n/a", "na", "none", ""]:
            logger.warning(
                f"Odds unavailable ('{odds_str_original}'). Pick will be skipped - investigate missing odds data."
            )
            return None
        
        try:
            # Parse the numeric value (remove + or - for parsing)
            odds = int(odds_str_original.replace("+", "").replace("-", ""))
            # Apply negative sign if original had it
            if "-" in odds_str_original or odds_str_original.startswith("-"):
                odds = -odds
            return odds
        except (ValueError, AttributeError) as e:
            logger.warning(
                f"Could not parse odds '{odds_str_original}': {e}. Pick will be skipped."
            )
            return None
    
    @staticmethod
    def _parse_single_pick(
        pick_data: Dict[str, Any],
        games: List[Game],
        game_map: Dict[int, Game],
    ) -> Optional[Pick]:
        """
        Convert a single pick dict to a Pick object.
        Returns None if pick should be skipped (e.g. odds unavailable).
        Raises ValueError for invalid required fields (game_id, rationale).
        """
        game_id_str = str(pick_data.get("game_id", ""))
        game_id = DataConverter.parse_game_id(game_id_str, games)
        if not game_id or game_id == 0:
            logger.error(f"Invalid or missing game_id for pick: {pick_data}")
            raise ValueError(f"Pick is missing required game_id field. game_id_str={game_id_str}")

        bet_type = DataConverter.parse_bet_type(pick_data.get("bet_type", ""))
        odds_str = str(pick_data.get("odds", ""))
        odds = DataConverter.parse_odds(odds_str)
        if odds is None:
            logger.warning(
                f"Skipping pick for game_id={pick_data.get('game_id')} - odds unavailable or unparseable: '{odds_str}'"
            )
            return None

        selection_text = pick_data.get("selection", "")
        line = pick_data.get("line", 0.0)
        if not line or line == 0.0:
            if selection_text:
                if bet_type == BetType.TOTAL:
                    match = re.search(r'(?:over|under)\s+(\d+\.?\d*)', selection_text, re.IGNORECASE)
                    if match:
                        line = float(match.group(1))
                elif bet_type == BetType.SPREAD:
                    match = re.search(r'([+-]?\d+\.?\d*)', selection_text)
                    if match:
                        line = float(match.group(1))
            if not line or line == 0.0:
                logger.warning(
                    f"Pick for game_id={game_id} missing 'line' field and could not parse from selection_text: {selection_text}"
                )

        team_name = pick_data.get("team_name")
        if not team_name and selection_text and bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
            clean_text = re.sub(r'\s+ML(\s*[+-]?\d+)?$', '', selection_text, flags=re.IGNORECASE)
            line_pattern = r'\s*([+-]?(?:over|under)\s*)?[+-]?\d+\.?\d*'
            team_name = re.sub(line_pattern, '', clean_text, flags=re.IGNORECASE).strip() or None
        if team_name and re.search(r'\s+ML(\s*[+-]?\d+)?$', team_name, flags=re.IGNORECASE):
            team_name = re.sub(r'\s+ML(\s*[+-]?\d+)?$', '', team_name, flags=re.IGNORECASE).strip()

        justification = pick_data.get("justification", [])
        if isinstance(justification, list):
            rationale = " | ".join(justification) if justification else pick_data.get("notes", "")
        else:
            rationale = str(justification) if justification else pick_data.get("notes", "")
        if not rationale or not rationale.strip():
            logger.error(f"Pick for game_id={game_id} has empty rationale! justification={justification}, notes={pick_data.get('notes')}")
            raise ValueError(f"Pick for game_id={game_id} is missing required rationale field")

        best_bet = pick_data.get("best_bet", False)
        high_confidence = pick_data.get("high_confidence", False)
        confidence_score = pick_data.get("confidence_score")
        if confidence_score is None:
            confidence_value = pick_data.get("confidence", 0.5)
            confidence_score = max(1, min(10, int(round(confidence_value * 10))))
            if confidence_value == 0.0:
                confidence_score = 1
        confidence_score = max(1, min(10, int(confidence_score)))

        confidence_raw = pick_data.get("confidence")
        if confidence_raw is not None:
            confidence_value = float(confidence_raw)
            confidence = max(0.0, min(1.0, confidence_value / 10.0 if confidence_value > 1.0 else confidence_value))
        else:
            confidence = max(0.0, min(1.0, confidence_score / 10.0))

        book_value = pick_data.get("book", "draftkings")
        if not book_value or (isinstance(book_value, str) and not book_value.strip()):
            book_value = "draftkings"

        return Pick(
            game_id=game_id,
            bet_type=bet_type,
            line=float(line),
            odds=odds,
            rationale=rationale,
            confidence=confidence,
            expected_value=float(pick_data.get("edge_estimate", 0.0)),
            book=book_value,
            selection_text=selection_text,
            team_name=team_name,
            team_id=None,
            best_bet=best_bet,
            high_confidence=high_confidence,
            confidence_score=confidence_score,
            parlay_legs=None,
        )

    @staticmethod
    def picks_from_json(candidate_picks: List[Dict[str, Any]], games: List[Game]) -> List[Pick]:
        """
        Convert JSON candidate picks from Picker to Pick objects.

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
                pick = DataConverter._parse_single_pick(pick_data, games, game_map)
                if pick is not None:
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
                "high_confidence": pick.high_confidence,
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
                high_confidence = approved_data.get("high_confidence", False)  # Get high_confidence flag from President's response
                
                # Match pick by game_id and bet_type only (line is stored in pick, not parsed from text)
                try:
                    game_id = int(game_id_str) if game_id_str and game_id_str.isdigit() else 0
                    bet_type = DataConverter.parse_bet_type(bet_type_str)
                    key = (game_id, bet_type.value if hasattr(bet_type, 'value') else str(bet_type))
                    matched_pick = pick_map.get(key)
                    if matched_pick and matched_pick.id:
                        approved_pick_ids.append(matched_pick.id)
                        # Update best_bet and high_confidence flags on the pick object
                        matched_pick.best_bet = best_bet
                        matched_pick.high_confidence = high_confidence
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
