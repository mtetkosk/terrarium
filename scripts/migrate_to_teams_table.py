#!/usr/bin/env python3
"""Migration script to populate teams table from existing data sources"""

import sys
from pathlib import Path
import json
from collections import defaultdict

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.storage import Database, TeamModel, GameModel, PickModel
from src.utils.team_normalizer import normalize_team_name
from src.utils.logging import get_logger

logger = get_logger("migration.teams")

def collect_team_names_from_sources(db: Database) -> set:
    """Collect all unique team names from various data sources"""
    team_names = set()
    session = db.get_session()
    
    try:
        # 1. From games table (team1, team2) - use raw SQL to access old columns
        logger.info("Collecting team names from games table...")
        try:
            from sqlalchemy import text
            # Use raw SQL to query old columns (team1, team2) that still exist in DB
            result = session.execute(text("SELECT team1, team2 FROM games"))
            for row in result:
                if row[0]:
                    team_names.add(row[0])
                if row[1]:
                    team_names.add(row[1])
        except Exception as e:
            logger.warning(f"Error querying games table: {e}")
            # Try alternative approach - check if columns exist first
            try:
                from sqlalchemy import inspect, text
                inspector = inspect(db.engine)
                columns = [col['name'] for col in inspector.get_columns('games')]
                if 'team1' in columns and 'team2' in columns:
                    result = session.execute(text("SELECT team1, team2 FROM games"))
                    for row in result:
                        if row[0]:
                            team_names.add(row[0])
                        if row[1]:
                            team_names.add(row[1])
            except Exception as e2:
                logger.error(f"Could not query games table: {e2}")
        
        logger.info(f"Found {len(team_names)} unique team names from games table")
        
        # 2. From KenPom cache
        logger.info("Collecting team names from KenPom cache...")
        kenpom_cache_file = Path("data/cache/kenpom_cache.json")
        if kenpom_cache_file.exists():
            try:
                with open(kenpom_cache_file, 'r') as f:
                    kenpom_data = json.load(f)
                    teams_data = kenpom_data.get("teams", {})
                    for team_key, team_info in teams_data.items():
                        team_name = team_info.get("team")
                        if team_name:
                            team_names.add(team_name)
                logger.info(f"Found {len(teams_data)} teams in KenPom cache")
            except Exception as e:
                logger.warning(f"Error reading KenPom cache: {e}")
        
        # 3. From picks table (if team_name column exists)
        logger.info("Collecting team names from picks table...")
        try:
            picks = session.query(PickModel).filter(PickModel.team_name.isnot(None)).all()
            for pick in picks:
                if pick.team_name:
                    team_names.add(pick.team_name)
            logger.info(f"Found {len(picks)} picks with team names")
        except Exception as e:
            logger.debug(f"team_name column may not exist yet: {e}")
        
        # 4. From selection_text in picks (extract team names)
        logger.info("Extracting team names from pick selection_text...")
        try:
            picks_with_selection = session.query(PickModel.selection_text).filter(
                PickModel.selection_text.isnot(None)
            ).all()
            import re
            for (selection_text,) in picks_with_selection:
                if selection_text:
                    # Try to extract team name (everything before the line)
                    line_pattern = r'\s*([+-]?(?:over|under)\s*)?[+-]?\d+\.?\d*'
                    team_name = re.sub(line_pattern, '', selection_text, flags=re.IGNORECASE).strip()
                    if team_name and len(team_name) > 2:
                        team_names.add(team_name)
            logger.info(f"Extracted team names from {len(picks_with_selection)} selection texts")
        except Exception as e:
            logger.debug(f"Error extracting from selection_text: {e}")
        
    finally:
        session.close()
    
    logger.info(f"Total unique team names collected: {len(team_names)}")
    return team_names

def create_teams_table(db: Database, team_names: set) -> dict:
    """Create teams table and populate with normalized team names"""
    from src.utils.team_normalizer import normalize_team_name_for_lookup
    
    session = db.get_session()
    team_id_map = {}  # Maps normalized_name -> team_id
    
    try:
        # First, normalize all team names using the lookup normalizer (strips suffixes)
        # This ensures "Air Force" and "Air Force Falcons" map to the same core name
        lookup_normalized_to_original = defaultdict(list)
        for team_name in team_names:
            # Use normalize_team_name_for_lookup which strips suffixes like "Falcons", "Wildcats"
            lookup_normalized = normalize_team_name_for_lookup(team_name)
            lookup_normalized_to_original[lookup_normalized].append(team_name)
        
        logger.info(f"Found {len(lookup_normalized_to_original)} unique teams after lookup normalization...")
        
        # Now create teams using the lookup-normalized names (core team names without suffixes)
        for lookup_normalized, original_names in lookup_normalized_to_original.items():
            # Check if team already exists
            existing_team = session.query(TeamModel).filter_by(
                normalized_team_name=lookup_normalized
            ).first()
            
            if existing_team:
                team_id = existing_team.id
                logger.debug(f"Team already exists: {lookup_normalized} (ID: {team_id})")
            else:
                # Create new team (use the most common/longest original name as reference)
                canonical_name = max(original_names, key=len)  # Prefer longer/more complete names
                team = TeamModel(normalized_team_name=lookup_normalized)
                session.add(team)
                session.flush()
                team_id = team.id
                logger.debug(f"Created team: {lookup_normalized} -> {canonical_name} (ID: {team_id})")
            
            # Map all original names and their variations to this team_id
            for original_name in original_names:
                # Map the original name's normalized form to the team_id
                original_normalized = normalize_team_name(original_name, for_matching=True)
                team_id_map[original_normalized] = team_id
                # Also map the lookup-normalized version
                team_id_map[lookup_normalized] = team_id
        
        session.commit()
        logger.info(f"Created/updated {len(lookup_normalized_to_original)} teams in database")
        logger.info(f"Created {len(team_id_map)} name mappings")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error creating teams table: {e}", exc_info=True)
        raise
    finally:
        session.close()
    
    return team_id_map

def update_games_table(db: Database, team_id_map: dict):
    """Update games table to use team IDs instead of team names"""
    session = db.get_session()
    
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('games')]
        
        has_old_columns = 'team1' in columns and 'team2' in columns
        has_new_columns = 'team1_id' in columns and 'team2_id' in columns
        
        if not has_new_columns:
            logger.warning("team1_id and team2_id columns don't exist yet. Adding them...")
            # Add new columns
            try:
                session.execute(text("ALTER TABLE games ADD COLUMN team1_id INTEGER"))
                session.execute(text("ALTER TABLE games ADD COLUMN team2_id INTEGER"))
                session.commit()
                logger.info("Added team1_id and team2_id columns to games table")
            except Exception as e:
                logger.warning(f"Could not add columns (they may already exist): {e}")
                session.rollback()
        
        if not has_old_columns:
            logger.info("Games table already uses team IDs, skipping update")
            return
        
        # Get all games using raw SQL to access old columns
        result = session.execute(text("SELECT id, team1, team2 FROM games"))
        games_data = result.fetchall()
        logger.info(f"Updating {len(games_data)} games to use team IDs...")
        
        updated_count = 0
        for game_id, team1_name, team2_name in games_data:
            if team1_name and team2_name:
                # Get team IDs for team1 and team2
                team1_normalized = normalize_team_name(team1_name, for_matching=True)
                team2_normalized = normalize_team_name(team2_name, for_matching=True)
                
                team1_id = team_id_map.get(team1_normalized)
                team2_id = team_id_map.get(team2_normalized)
                
                if team1_id and team2_id:
                    session.execute(
                        text("UPDATE games SET team1_id = :team1_id, team2_id = :team2_id WHERE id = :game_id"),
                        {"team1_id": team1_id, "team2_id": team2_id, "game_id": game_id}
                    )
                    updated_count += 1
                else:
                    logger.warning(
                        f"Could not find team IDs for game {game_id}: "
                        f"team1={team1_name} (normalized: {team1_normalized}), "
                        f"team2={team2_name} (normalized: {team2_normalized})"
                    )
        
        session.commit()
        logger.info(f"Updated {updated_count} games with team IDs")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating games table: {e}", exc_info=True)
        raise
    finally:
        session.close()

def update_picks_table(db: Database, team_id_map: dict):
    """Update picks table to use team IDs where team_name is available"""
    session = db.get_session()
    
    try:
        from sqlalchemy import text, inspect
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('picks')]
        
        has_team_name = 'team_name' in columns
        has_team_id = 'team_id' in columns
        
        if not has_team_id:
            logger.warning("team_id column doesn't exist yet. Adding it...")
            try:
                session.execute(text("ALTER TABLE picks ADD COLUMN team_id INTEGER"))
                session.commit()
                logger.info("Added team_id column to picks table")
            except Exception as e:
                logger.warning(f"Could not add column (it may already exist): {e}")
                session.rollback()
        
        if not has_team_name:
            logger.info("No team_name column in picks table, skipping picks update")
            return
        
        # Get picks that have team_name but not team_id using raw SQL
        result = session.execute(
            text("SELECT id, team_name FROM picks WHERE team_name IS NOT NULL AND (team_id IS NULL OR team_id = 0)")
        )
        picks_data = result.fetchall()
        logger.info(f"Updating {len(picks_data)} picks to use team IDs...")
        
        updated_count = 0
        for pick_id, team_name in picks_data:
            if team_name:
                normalized = normalize_team_name(team_name, for_matching=True)
                team_id = team_id_map.get(normalized)
                
                if team_id:
                    session.execute(
                        text("UPDATE picks SET team_id = :team_id WHERE id = :pick_id"),
                        {"team_id": team_id, "pick_id": pick_id}
                    )
                    updated_count += 1
                else:
                    logger.warning(
                        f"Could not find team ID for pick {pick_id}: "
                        f"team_name={team_name} (normalized: {normalized})"
                    )
        
        session.commit()
        logger.info(f"Updated {updated_count} picks with team IDs")
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating picks table: {e}", exc_info=True)
        raise
    finally:
        session.close()

def main():
    """Run the migration"""
    logger.info("Starting teams table migration...")
    
    db = Database()
    
    # Step 1: Collect all team names from various sources
    team_names = collect_team_names_from_sources(db)
    
    if not team_names:
        logger.warning("No team names found! Cannot proceed with migration.")
        return
    
    # Step 2: Create teams table and populate
    team_id_map = create_teams_table(db, team_names)
    
    # Step 3: Update games table
    update_games_table(db, team_id_map)
    
    # Step 4: Update picks table
    update_picks_table(db, team_id_map)
    
    logger.info("Migration completed successfully!")
    logger.info(f"Created {len(team_id_map)} teams in the database")

if __name__ == "__main__":
    main()

