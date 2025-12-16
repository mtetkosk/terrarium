"""Service for persisting games, picks, and bets to the database"""

from datetime import date, datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from src.data.models import Game, Pick, Bet, BetType, BetResult
from src.data.storage import (
    Database, GameModel, BettingLineModel, BetModel, PickModel, TeamModel
)
from src.utils.team_normalizer import normalize_team_name, remove_mascot_from_team_name
from src.utils.logging import get_logger

logger = get_logger("orchestration.persistence_service")


class PersistenceService:
    """Service for persisting games, picks, and bets"""
    
    def __init__(self, db: Database):
        """Initialize persistence service"""
        self.db = db
    
    def save_games(self, games: List[Game]) -> List[Game]:
        """Save games to database and return with IDs"""
        if not self.db:
            return games
        
        session = self.db.get_session()
        try:
            saved_games = []
            for game in games:
                saved_game = self._save_single_game(game, session)
                if saved_game:
                    saved_games.append(saved_game)
            
            session.commit()
            return saved_games
            
        except Exception as e:
            logger.error(f"Error saving games: {e}", exc_info=True)
            session.rollback()
            return games
        finally:
            session.close()
    
    def _save_single_game(self, game: Game, session: Session) -> Optional[Game]:
        """Save a single game to database"""
        # Get or create team IDs
        # First normalize, then remove mascots to prevent duplicates
        norm_team1 = normalize_team_name(game.team1, for_matching=True)
        norm_team2 = normalize_team_name(game.team2, for_matching=True)
        cleaned_team1 = remove_mascot_from_team_name(norm_team1)
        cleaned_team2 = remove_mascot_from_team_name(norm_team2)
        
        # CRITICAL: Handle ambiguous team names by validating against opponent
        # This prevents "North Carolina" from matching "North Carolina A&T" incorrectly
        # Use cleaned names (without mascots) for team creation
        team1_model = self._get_or_create_team_with_disambiguation(
            game.team1, cleaned_team1, game.team2, session
        )
        team2_model = self._get_or_create_team_with_disambiguation(
            game.team2, cleaned_team2, game.team1, session
        )
        
        # CRITICAL: Validate that teams are using normalized names (no mascots)
        # This ensures games are ALWAYS saved with normalized team names
        if team1_model.normalized_team_name != cleaned_team1:
            logger.error(
                f"CRITICAL: Team1 model has non-normalized name '{team1_model.normalized_team_name}' "
                f"but expected '{cleaned_team1}'. This violates the normalization requirement."
            )
            raise ValueError(
                f"Team1 must use normalized name without mascots. "
                f"Found: '{team1_model.normalized_team_name}', Expected: '{cleaned_team1}'"
            )
        
        if team2_model.normalized_team_name != cleaned_team2:
            logger.error(
                f"CRITICAL: Team2 model has non-normalized name '{team2_model.normalized_team_name}' "
                f"but expected '{cleaned_team2}'. This violates the normalization requirement."
            )
            raise ValueError(
                f"Team2 must use normalized name without mascots. "
                f"Found: '{team2_model.normalized_team_name}', Expected: '{cleaned_team2}'"
            )
        
        # Check if game exists by team_ids and date
        existing = session.query(GameModel).filter_by(
            team1_id=team1_model.id,
            team2_id=team2_model.id,
            date=game.date
        ).first()
        
        if existing:
            # Update game_time_est if provided in the incoming game
            if game.game_time_est is not None and existing.game_time_est != game.game_time_est:
                existing.game_time_est = game.game_time_est
                session.flush()
            
            # Get team names from relationships for Game dataclass
            team1_name = existing.team1_ref.normalized_team_name if existing.team1_ref else game.team1
            team2_name = existing.team2_ref.normalized_team_name if existing.team2_ref else game.team2
            
            return Game(
                id=existing.id,
                team1=team1_name,
                team2=team2_name,
                team1_id=existing.team1_id,
                team2_id=existing.team2_id,
                date=existing.date,
                venue=existing.venue,
                status=existing.status,
                result=existing.result,
                game_time_est=existing.game_time_est
            )
        else:
            # Create new game
            game_model = GameModel(
                team1_id=team1_model.id,
                team2_id=team2_model.id,
                date=game.date,
                venue=game.venue,
                status=game.status,
                result=game.result,
                game_time_est=game.game_time_est
            )
            session.add(game_model)
            session.flush()
            
            return Game(
                id=game_model.id,
                team1=game.team1,  # Keep for Game dataclass (in-memory use)
                team2=game.team2,  # Keep for Game dataclass (in-memory use)
                team1_id=game_model.team1_id,
                team2_id=game_model.team2_id,
                date=game_model.date,
                venue=game_model.venue,
                status=game_model.status,
                result=game_model.result,
                game_time_est=game_model.game_time_est
            )
    
    def save_lines(self, lines: List, games: List[Game]) -> List:
        """Save betting lines to database using upsert pattern to prevent duplicates"""
        if not self.db:
            return lines
        
        session = self.db.get_session()
        try:
            upserted_count = 0
            created_count = 0
            updated_count = 0
            
            for line in lines:
                # Check if betting line already exists (upsert pattern)
                # Unique key: (game_id, book, bet_type, team)
                existing = session.query(BettingLineModel).filter_by(
                    game_id=line.game_id,
                    book=line.book.lower() if line.book else None,
                    bet_type=line.bet_type,
                    team=line.team
                ).first()
                
                if existing:
                    # Update existing record with latest line data
                    existing.line = line.line
                    existing.odds = line.odds
                    existing.timestamp = line.timestamp
                    updated_count += 1
                    logger.debug(
                        f"Updated betting line: game_id={line.game_id}, book={line.book}, "
                        f"bet_type={line.bet_type.value}, team={line.team}, line={line.line}"
                    )
                else:
                    # Create new record
                    line_model = BettingLineModel(
                        game_id=line.game_id,
                        book=line.book.lower() if line.book else line.book,
                        bet_type=line.bet_type,
                        line=line.line,
                        odds=line.odds,
                        team=line.team,
                        timestamp=line.timestamp
                    )
                    session.add(line_model)
                    created_count += 1
                    logger.debug(
                        f"Created betting line: game_id={line.game_id}, book={line.book}, "
                        f"bet_type={line.bet_type.value}, team={line.team}, line={line.line}"
                    )
            
            session.commit()
            upserted_count = created_count + updated_count
            logger.info(
                f"Upserted {upserted_count} betting lines: {created_count} created, {updated_count} updated"
            )
            return lines
            
        except Exception as e:
            logger.error(f"Error saving lines: {e}", exc_info=True)
            session.rollback()
            return lines
        finally:
            session.close()
    
    def save_pick(self, pick: Pick, target_date: Optional[date] = None) -> None:
        """
        Save pick to database using upsert pattern.
        
        CRITICAL: Database unique constraint on (game_id, pick_date) ensures only one pick per game per day.
        This method updates existing pick or creates new one. No manual duplicate handling needed.
        
        Args:
            pick: Pick object to save
            target_date: Optional target date for the pick. If not provided, uses pick.created_at.date() or date.today()
        """
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            # Validate critical fields before saving
            if not pick.game_id:
                logger.error(f"Cannot save pick: missing game_id. Pick data: {pick}")
                return
            
            # Validate required NOT NULL fields
            if not pick.rationale or not pick.rationale.strip():
                logger.error(f"Cannot save pick: missing or empty rationale for game_id={pick.game_id}")
                raise ValueError(f"Pick for game_id={pick.game_id} is missing required rationale field")
            
            if pick.expected_value is None:
                logger.error(f"Cannot save pick: missing expected_value for game_id={pick.game_id}")
                raise ValueError(f"Pick for game_id={pick.game_id} is missing required expected_value field")
            
            if not pick.book or not pick.book.strip():
                logger.error(f"Cannot save pick: missing or empty book for game_id={pick.game_id}")
                raise ValueError(f"Pick for game_id={pick.game_id} is missing required book field")
            
            if not pick.selection_text and pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                logger.warning(
                    f"Pick for game_id={pick.game_id} missing selection_text! "
                    f"This is critical for identifying which team we're betting on. "
                    f"Rationale: {pick.rationale[:100] if pick.rationale else 'None'}"
                )
            
            # Get the date for this pick - prefer explicit target_date, then pick.created_at, then today
            if target_date:
                pick_date = target_date
            elif pick.created_at:
                pick_date = pick.created_at.date()
            else:
                pick_date = date.today()
            
            # Get game model to validate team_id matches game teams
            game_model = session.query(GameModel).filter_by(id=pick.game_id).first()
            if not game_model:
                logger.error(f"Cannot save pick: game_id={pick.game_id} not found in database")
                raise ValueError(f"Game {pick.game_id} not found")
            
            # Get or create team_id if this is a spread/moneyline bet
            team_id = pick.team_id
            team_name_for_lookup = None
            if not team_id and pick.team_name and pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                # Try to match pick.team_name against game's teams first
                normalized_pick_name = normalize_team_name(pick.team_name, for_matching=True)
                game_team1 = session.query(TeamModel).filter_by(id=game_model.team1_id).first()
                game_team2 = session.query(TeamModel).filter_by(id=game_model.team2_id).first()
                
                # Check if pick team name matches one of the game's teams
                team_id_from_game = None
                if game_team1:
                    game_team1_norm = normalize_team_name(game_team1.normalized_team_name, for_matching=True)
                    if (normalized_pick_name in game_team1_norm or game_team1_norm in normalized_pick_name or
                        normalized_pick_name == game_team1_norm):
                        team_id_from_game = game_model.team1_id
                if not team_id_from_game and game_team2:
                    game_team2_norm = normalize_team_name(game_team2.normalized_team_name, for_matching=True)
                    if (normalized_pick_name in game_team2_norm or game_team2_norm in normalized_pick_name or
                        normalized_pick_name == game_team2_norm):
                        team_id_from_game = game_model.team2_id
                
                if team_id_from_game:
                    # Use the game's team_id
                    team_id = team_id_from_game
                    # Use the normalized team name from database for betting line lookup consistency
                    team = session.query(TeamModel).filter_by(id=team_id).first()
                    team_name_for_lookup = team.normalized_team_name if team else pick.team_name
                else:
                    # Team name doesn't match game teams - this is an error
                    logger.error(
                        f"Cannot save pick for game_id={pick.game_id}: "
                        f"team_name '{pick.team_name}' does not match either game team "
                        f"(team1_id={game_model.team1_id}, team2_id={game_model.team2_id}). "
                        f"This indicates a data integrity issue."
                    )
                    raise ValueError(
                        f"Pick team_name '{pick.team_name}' does not match game teams. "
                        f"Game teams: team1_id={game_model.team1_id}, team2_id={game_model.team2_id}"
                    )
            elif team_id:
                # CRITICAL: Validate that team_id matches one of the game's teams
                if pick.bet_type in [BetType.SPREAD, BetType.MONEYLINE]:
                    if team_id != game_model.team1_id and team_id != game_model.team2_id:
                        pick_team = session.query(TeamModel).filter_by(id=team_id).first()
                        game_team1 = session.query(TeamModel).filter_by(id=game_model.team1_id).first()
                        game_team2 = session.query(TeamModel).filter_by(id=game_model.team2_id).first()
                        logger.error(
                            f"Cannot save pick for game_id={pick.game_id}: "
                            f"team_id={team_id} ({pick_team.normalized_team_name if pick_team else 'Unknown'}) "
                            f"does not match game teams "
                            f"(team1_id={game_model.team1_id} ({game_team1.normalized_team_name if game_team1 else 'Unknown'}), "
                            f"team2_id={game_model.team2_id} ({game_team2.normalized_team_name if game_team2 else 'Unknown'})). "
                            f"This is a DATA INTEGRITY ERROR."
                        )
                        raise ValueError(
                            f"Pick team_id={team_id} does not match game teams. "
                            f"Game teams: team1_id={game_model.team1_id}, team2_id={game_model.team2_id}"
                        )
                
                # Get team name for betting line lookup
                team = session.query(TeamModel).filter_by(id=team_id).first()
                if team:
                    team_name_for_lookup = team.normalized_team_name
            
            # Use line and odds from pick (already parsed when creating Pick object)
            line_from_db = pick.line
            odds_from_db = pick.odds
            
            # For moneylines, extract odds from selection_text if needed (odds might be in selection_text like "ML +500")
            if pick.bet_type == BetType.MONEYLINE and pick.selection_text and odds_from_db == -110:
                import re
                match = re.search(r'ML\s*([+-]?\d+)', pick.selection_text, re.IGNORECASE)
                if match:
                    odds_from_db = int(match.group(1))
            
            # Check if pick already exists (upsert pattern)
            existing = session.query(PickModel).filter_by(
                game_id=pick.game_id,
                pick_date=pick_date
            ).first()
            
            if existing:
                # Update existing record
                existing.bet_type = pick.bet_type
                existing.line = line_from_db  # Use line from betting line database
                existing.odds = odds_from_db  # Use odds from betting line database
                existing.stake_units = pick.stake_units
                existing.stake_amount = pick.stake_amount
                existing.rationale = pick.rationale
                existing.confidence = pick.confidence
                existing.expected_value = pick.expected_value
                existing.book = pick.book
                existing.parlay_legs = pick.parlay_legs
                existing.selection_text = pick.selection_text
                existing.team_id = team_id
                existing.best_bet = pick.best_bet
                existing.confidence_score = pick.confidence_score
                # Note: favorite field is deprecated in favor of best_bet
                pick.id = existing.id
            else:
                # Create new record
                pick_model = PickModel(
                    game_id=pick.game_id,
                    bet_type=pick.bet_type,
                    line=line_from_db,  # Use line from betting line database
                    odds=odds_from_db,  # Use odds from betting line database
                    stake_units=pick.stake_units,
                    stake_amount=pick.stake_amount,
                    rationale=pick.rationale,
                    confidence=pick.confidence,
                    expected_value=pick.expected_value,
                    book=pick.book,
                    parlay_legs=pick.parlay_legs,
                    selection_text=pick.selection_text,
                    team_id=team_id,
                    best_bet=pick.best_bet,
                    confidence_score=pick.confidence_score,
                    # Note: favorite field is deprecated in favor of best_bet (defaults to False in schema)
                    pick_date=pick_date,
                    created_at=pick.created_at if pick.created_at else datetime.now()
                )
                session.add(pick_model)
                session.flush()
                pick.id = pick_model.id
            
            session.commit()
            
            logger.info(
                f"Upserted pick for game_id={pick.game_id} on {pick_date}: bet_type={pick.bet_type.value}, "
                f"selection_text='{pick.selection_text}', best_bet={pick.best_bet}"
            )
        except Exception as e:
            logger.error(f"Error saving pick: {e}", exc_info=True)
            session.rollback()
            raise
        finally:
            session.close()
    
    def update_pick_stakes(self, pick: Pick) -> None:
        """Update pick stakes in database"""
        if not self.db or not pick.id:
            return
        
        session = self.db.get_session()
        try:
            pick_model = session.query(PickModel).filter_by(id=pick.id).first()
            if pick_model:
                pick_model.stake_units = pick.stake_units
                pick_model.stake_amount = pick.stake_amount
                session.commit()
        except Exception as e:
            logger.error(f"Error updating pick stakes: {e}")
            session.rollback()
        finally:
            session.close()
    
    def place_bets(self, picks: List[Pick]) -> List[Bet]:
        """Place bets (simulation mode)"""
        if not self.db:
            return []
        
        session = self.db.get_session()
        bets = []
        
        try:
            for pick in picks:
                if not pick.id:
                    continue
                
                # Check if bet already exists (upsert pattern)
                existing_bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
                
                if existing_bet:
                    # Update existing bet (if it was cancelled or needs to be re-placed)
                    existing_bet.placed_at = datetime.now()
                    existing_bet.result = BetResult.PENDING
                    existing_bet.payout = 0.0
                    existing_bet.profit_loss = 0.0
                    existing_bet.settled_at = None
                    bet_model = existing_bet
                else:
                    # Create new bet record
                    bet_model = BetModel(
                        pick_id=pick.id,
                        placed_at=datetime.now(),
                        result=BetResult.PENDING
                    )
                    session.add(bet_model)
                
                session.flush()
                
                bet = Bet(
                    id=bet_model.id,
                    pick_id=bet_model.pick_id,
                    placed_at=bet_model.placed_at,
                    result=bet_model.result
                )
                bets.append(bet)
            
            session.commit()
            logger.info(f"Placed {len(bets)} bets")
            
        except Exception as e:
            logger.error(f"Error placing bets: {e}")
            session.rollback()
        finally:
            session.close()
        
        return bets
    
    def _get_or_create_team_with_disambiguation(
        self, 
        original_team_name: str, 
        normalized_name: str,  # This should already have mascot removed
        opponent_name: str, 
        session: Session
    ) -> TeamModel:
        """
        Get or create team with disambiguation for ambiguous team names.
        
        For ambiguous names like "North Carolina", checks opponent context to ensure
        we're matching the correct team (e.g., main UNC vs UNC A&T).
        """
        # Check if this is an ambiguous team name that needs disambiguation
        ambiguous_names = {
            'north carolina': ['north carolina a&t', 'north carolina central', 'unc greensboro', 
                              'unc asheville', 'unc wilmington', 'unc charlotte'],
            'south carolina': ['south carolina state', 'usc upstate']
        }
        
        needs_disambiguation = False
        ambiguous_variants = []
        
        for ambiguous_base, variants in ambiguous_names.items():
            if normalized_name == ambiguous_base:
                needs_disambiguation = True
                ambiguous_variants = variants
                break
        
        # CRITICAL: normalized_name must already have mascot removed
        # Validate this before proceeding
        test_cleaned = remove_mascot_from_team_name(normalized_name)
        if test_cleaned != normalized_name:
            logger.error(
                f"CRITICAL: normalized_name '{normalized_name}' still contains mascots. "
                f"Cleaned version: '{test_cleaned}'. This should not happen."
            )
            raise ValueError(
                f"normalized_name '{normalized_name}' must not contain mascots. "
                f"Use remove_mascot_from_team_name() before calling this function."
            )
        
        # Standard lookup: ONLY look for teams with normalized names (no mascots)
        # This ensures we never match teams with mascots in their names
        team_model = session.query(TeamModel).filter_by(normalized_team_name=normalized_name).first()
        
        if needs_disambiguation and team_model:
            # We found an existing team - validate it makes sense given the opponent
            norm_opponent = normalize_team_name(opponent_name, for_matching=True)
            
            # Check if opponent is a variant (low-major) team
            opponent_is_variant = any(variant in norm_opponent for variant in ambiguous_variants)
            
            # If opponent is a low-major variant (e.g., "usc upstate", "north carolina a&t"),
            # and we matched to the main team (e.g., "north carolina"), that might be wrong
            # Check if there's a variant team that matches the opponent context better
            if opponent_is_variant:
                # Opponent is a variant/low-major team - this game is likely low-major vs low-major
                # or major vs low-major. If the existing team is the main team and opponent is variant,
                # that's actually fine (major vs low-major is common).
                # But if both should be variants, we need to check.
                pass  # Major vs low-major is normal, so existing match is probably fine
            
            # If opponent is NOT a variant and is clearly a major program,
            # then this team should also be the main team (not a variant)
            # This validation is mostly to catch errors
            
            # CRITICAL: Check if original team name has distinguishing info that suggests a variant
            original_lower = original_team_name.lower()
            variant_indicators = ['a&t', 'a t', 'central', 'state', 'greensboro', 'asheville', 'wilmington', 'upstate']
            original_has_variant_indicator = any(indicator in original_lower for indicator in variant_indicators)
            
            if original_has_variant_indicator:
                # Original name has variant indicator, but normalized to ambiguous base name
                # This suggests normalization might have stripped important info
                # Check if a variant team exists that matches better
                variant_teams = session.query(TeamModel).filter(
                    TeamModel.normalized_team_name.in_(ambiguous_variants)
                ).all()
                
                # Try to find a variant that matches the original name better
                for variant_team in variant_teams:
                    variant_norm = variant_team.normalized_team_name
                    # Check if any variant indicators in original match the variant team name
                    if any(indicator in variant_norm for indicator in variant_indicators if indicator in original_lower):
                        logger.warning(
                            f"Team name '{original_team_name}' normalized to '{normalized_name}', but found existing variant team '{variant_norm}' "
                            f"that might be a better match. Opponent: '{opponent_name}'. Using existing '{normalized_name}' team."
                        )
        
        if not team_model:
            # Before creating, check if this is an ambiguous name that might conflict with variants
            if needs_disambiguation:
                variant_teams = session.query(TeamModel).filter(
                    TeamModel.normalized_team_name.in_(ambiguous_variants)
                ).all()
                
                if variant_teams:
                    logger.warning(
                        f"Creating new team '{normalized_name}' from '{original_team_name}', but variant teams exist: "
                        f"{[t.normalized_team_name for t in variant_teams]}. Opponent: '{opponent_name}'. "
                        f"This might indicate a normalization issue."
                    )
            
            # CRITICAL: normalized_name must have mascot removed - validate one more time
            final_cleaned = remove_mascot_from_team_name(normalized_name)
            if final_cleaned != normalized_name:
                logger.error(
                    f"CRITICAL: Attempting to create team with non-normalized name '{normalized_name}'. "
                    f"Cleaned version: '{final_cleaned}'. Rejecting team creation."
                )
                raise ValueError(
                    f"Cannot create team with mascots in name. "
                    f"Input: '{normalized_name}', Cleaned: '{final_cleaned}'"
                )
            
            # Create team with normalized name (no mascots) - this is the ONLY allowed format
            team_model = TeamModel(normalized_team_name=normalized_name)
            session.add(team_model)
            session.flush()
            logger.info(f"Created new team: '{original_team_name}' -> '{normalized_name}' (normalized, no mascots)")
        else:
            # CRITICAL: Validate that existing team has normalized name (no mascots) and matches expected name
            # If it has mascots or doesn't match, this is a data integrity issue
            existing_cleaned = remove_mascot_from_team_name(team_model.normalized_team_name)
            
            # Check if existing team has mascots
            if existing_cleaned != team_model.normalized_team_name:
                logger.error(
                    f"CRITICAL: Found existing team '{team_model.normalized_team_name}' with mascots in database. "
                    f"This violates normalization requirements. Cleaned version: '{existing_cleaned}'. "
                    f"Original input: '{original_team_name}', Expected normalized: '{normalized_name}'"
                )
                # Update the team name to the expected normalized name (not just remove mascots)
                logger.warning(
                    f"Fixing team name: '{team_model.normalized_team_name}' -> '{normalized_name}'"
                )
                team_model.normalized_team_name = normalized_name
                session.flush()
            # Check if existing team name matches expected normalized name
            elif team_model.normalized_team_name != normalized_name:
                logger.error(
                    f"CRITICAL: Found existing team '{team_model.normalized_team_name}' but expected '{normalized_name}'. "
                    f"This indicates a normalization mismatch. Original input: '{original_team_name}'"
                )
                # Update to expected normalized name
                logger.warning(
                    f"Fixing team name mismatch: '{team_model.normalized_team_name}' -> '{normalized_name}'"
                )
                team_model.normalized_team_name = normalized_name
                session.flush()
            
            # Log when we're using an existing team for debugging
            if needs_disambiguation:
                logger.debug(
                    f"Using existing team '{normalized_name}' (from '{original_team_name}') for game vs '{opponent_name}'"
                )
        
        return team_model

