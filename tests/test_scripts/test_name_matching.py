"""Test script to identify team names that can't be matched between betting lines and KenPom"""

import os
from datetime import date, timedelta
from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.scrapers.kenpom_scraper import KenPomScraper
from src.utils.team_normalizer import are_teams_matching, normalize_team_name
from src.utils.logging import get_logger

logger = get_logger("test_name_matching")


def test_matching():
    """Test matching between games, betting lines, and KenPom data"""
    
    # Get games for today
    target_date = date.today()
    games_scraper = GamesScraper()
    games = games_scraper.scrape_games(target_date)
    
    if not games:
        logger.warning(f"No games found for {target_date}")
        return
    
    logger.info(f"Found {len(games)} games for {target_date}")
    
    # Get betting lines
    lines_scraper = LinesScraper()
    lines = lines_scraper.scrape_lines(games)
    
    logger.info(f"Found {len(lines)} betting lines")
    
    # Get KenPom scraper
    kenpom_scraper = KenPomScraper()
    
    # Track unmatched teams
    unmatched_teams = set()
    matched_teams = set()
    
    # Test matching for each game
    for game in games:
        team1 = game.team1
        team2 = game.team2
        
        # Normalize game team names
        team1_norm = normalize_team_name(team1, for_matching=True)
        team2_norm = normalize_team_name(team2, for_matching=True)
        
        # Check if we can find KenPom stats for both teams
        team1_stats = kenpom_scraper.get_team_stats(team1, target_date)
        team2_stats = kenpom_scraper.get_team_stats(team2, target_date)
        
        if not team1_stats:
            unmatched_teams.add((team1, "kenpom"))
            logger.warning(f"❌ Could not find KenPom stats for: {team1}")
        else:
            matched_teams.add(team1)
            logger.info(f"✓ Found KenPom stats for: {team1}")
        
        if not team2_stats:
            unmatched_teams.add((team2, "kenpom"))
            logger.warning(f"❌ Could not find KenPom stats for: {team2}")
        else:
            matched_teams.add(team2)
            logger.info(f"✓ Found KenPom stats for: {team2}")
        
        # Check betting lines matching
        game_lines = [line for line in lines if line.game_id == game.id]
        if game_lines:
            # Extract unique team names from lines
            line_teams = set()
            for line in game_lines:
                if line.team:
                    line_teams.add(line.team)
            
            # Check if line teams match game teams
            for line_team in line_teams:
                matches_team1 = are_teams_matching(line_team, team1)
                matches_team2 = are_teams_matching(line_team, team2)
                
                if not matches_team1 and not matches_team2:
                    unmatched_teams.add((line_team, "betting_lines"))
                    logger.warning(f"❌ Betting line team '{line_team}' doesn't match game teams '{team1}' or '{team2}'")
                else:
                    matched_team = team1 if matches_team1 else team2
                    logger.info(f"✓ Betting line team '{line_team}' matches '{matched_team}'")
        else:
            logger.warning(f"⚠️  No betting lines found for game: {team1} vs {team2}")
    
    # Print summary
    print("\n" + "="*80)
    print("MATCHING SUMMARY")
    print("="*80)
    print(f"Total games: {len(games)}")
    print(f"Matched teams: {len(matched_teams)}")
    print(f"Unmatched teams: {len(unmatched_teams)}")
    
    if unmatched_teams:
        print("\nUNMATCHED TEAMS:")
        print("-"*80)
        for team, source in sorted(unmatched_teams):
            print(f"  {team} (from {source})")
            # Show normalized version
            norm = normalize_team_name(team, for_matching=True)
            print(f"    Normalized: '{norm}'")
    
    return unmatched_teams


if __name__ == "__main__":
    test_matching()

