#!/usr/bin/env python3
"""Test script to debug The Odds API and see why only 2 games have lines"""

import os
import sys
import json
from datetime import date, datetime
import requests
from typing import List, Dict, Any

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.models import Game


def test_odds_api():
    """Test The Odds API directly and compare with games"""
    target_date = date(2025, 11, 18)
    
    # Get API key
    api_key = os.getenv('THE_ODDS_API_KEY')
    if not api_key:
        print("❌ THE_ODDS_API_KEY environment variable not set")
        return
    
    print(f"🔍 Testing The Odds API for {target_date}")
    print("=" * 80)
    
    # Step 1: Get games from ESPN
    print("\n1️⃣ Fetching games from ESPN...")
    games_scraper = GamesScraper()
    games = games_scraper.scrape_games(target_date)
    print(f"   Found {len(games)} games from ESPN")
    
    if games:
        print(f"\n   First 5 games:")
        for i, game in enumerate(games[:5], 1):
            print(f"   {i}. {game.team1} vs {game.team2} (ID: {game.id})")
    
    # Step 2: Call The Odds API directly
    print("\n2️⃣ Calling The Odds API...")
    base_url = "https://api.the-odds-api.com/v4"
    date_str = target_date.strftime('%Y-%m-%d')
    sport = 'basketball_ncaab'
    book = 'draftkings'
    
    # Convert to EST/EDT for proper filtering
    import pytz
    est_tz = pytz.timezone('America/New_York')
    est_start = est_tz.localize(datetime.combine(target_date, datetime.min.time()))
    est_end = est_tz.localize(datetime.combine(target_date, datetime.max.time().replace(microsecond=0)))
    utc_start = est_start.astimezone(pytz.UTC)
    utc_end = est_end.astimezone(pytz.UTC)
    commence_time_from = utc_start.strftime('%Y-%m-%dT%H:%M:%SZ')
    commence_time_to = utc_end.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    print(f"   Date filter: {target_date} EST ({est_start} to {est_end})")
    print(f"   Converted to UTC: {utc_start} to {utc_end}")
    
    url = f"{base_url}/sports/{sport}/odds"
    params = {
        'apiKey': api_key,
        'regions': 'us',
        'markets': 'spreads,totals,h2h',
        'oddsFormat': 'american',
        'dateFormat': 'iso',
        'bookmakers': book,
        'commenceTimeFrom': commence_time_from,
        'commenceTimeTo': commence_time_to
    }
    
    print(f"   URL: {url}")
    print(f"   Params: {json.dumps({k: v for k, v in params.items() if k != 'apiKey'}, indent=2)}")
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        print(f"\n   ✅ API Response received")
        print(f"   Number of events: {len(data)}")
        
        # Step 3: Analyze API response
        print("\n3️⃣ Analyzing API response...")
        if data:
            print(f"\n   First 5 events from API:")
            for i, event in enumerate(data[:5], 1):
                home = event.get('home_team', 'N/A')
                away = event.get('away_team', 'N/A')
                bookmakers = event.get('bookmakers', [])
                dk_count = sum(1 for b in bookmakers if b.get('key', '').lower() == 'draftkings')
                print(f"   {i}. {away} @ {home}")
                print(f"      DraftKings bookmakers: {dk_count}")
                if dk_count > 0:
                    dk = next((b for b in bookmakers if b.get('key', '').lower() == 'draftkings'), None)
                    if dk:
                        markets = [m.get('key') for m in dk.get('markets', [])]
                        print(f"      Markets: {', '.join(markets)}")
        else:
            print("   ⚠️  No events returned from API")
        
        # Step 4: Test matching logic
        print("\n4️⃣ Testing matching logic...")
        lines_scraper = LinesScraper()
        
        matched_count = 0
        unmatched_events = []
        matched_games = []
        
        for event in data:
            event_home = event.get('home_team', '').strip()
            event_away = event.get('away_team', '').strip()
            
            # Try to match with games
            matched = False
            matched_game_id = None
            
            for game in games:
                team1_api = lines_scraper._map_team_name(game.team1)
                team2_api = lines_scraper._map_team_name(game.team2)
                
                if lines_scraper._matches_game(event, team1_api, team2_api):
                    matched = True
                    matched_game_id = game.id
                    matched_games.append({
                        'game_id': game.id,
                        'game_teams': f"{game.team1} vs {game.team2}",
                        'api_teams': f"{event_away} @ {event_home}",
                        'event': event
                    })
                    matched_count += 1
                    break
            
            if not matched:
                unmatched_events.append({
                    'api_teams': f"{event_away} @ {event_home}",
                    'event': event
                })
        
        print(f"\n   Matched: {matched_count} / {len(data)} events")
        print(f"   Unmatched: {len(unmatched_events)} events")
        
        if matched_games:
            print(f"\n   ✅ Matched games (first 5):")
            for mg in matched_games[:5]:
                print(f"      Game ID {mg['game_id']}: {mg['game_teams']}")
                print(f"         API: {mg['api_teams']}")
        
        if unmatched_events:
            print(f"\n   ⚠️  Unmatched events (first 10):")
            for ue in unmatched_events[:10]:
                print(f"      {ue['api_teams']}")
            
            # Show some examples of why they might not match
            if unmatched_events and games:
                print(f"\n   🔍 Sample matching attempt for first unmatched event:")
                ue = unmatched_events[0]
                game = games[0]
                print(f"      ESPN game: {game.team1} vs {game.team2}")
                print(f"      API event: {ue['api_teams']}")
                
                # Show normalized names
                team1_norm = lines_scraper._normalize_team_name_for_matching(game.team1)
                team2_norm = lines_scraper._normalize_team_name_for_matching(game.team2)
                api_home_norm = lines_scraper._normalize_team_name_for_matching(ue['event'].get('home_team', ''))
                api_away_norm = lines_scraper._normalize_team_name_for_matching(ue['event'].get('away_team', ''))
                
                print(f"      Normalized ESPN: {team1_norm} vs {team2_norm}")
                print(f"      Normalized API: {api_away_norm} @ {api_home_norm}")
        
        # Step 5: Count games with lines
        print("\n5️⃣ Counting games with betting lines...")
        games_with_lines = set()
        for mg in matched_games:
            games_with_lines.add(mg['game_id'])
        
        print(f"   Games with at least one matched event: {len(games_with_lines)}")
        print(f"   Total games from ESPN: {len(games)}")
        
        # Step 6: Save detailed comparison to file
        print("\n6️⃣ Saving detailed comparison...")
        comparison = {
            'date': date_str,
            'espn_games_count': len(games),
            'api_events_count': len(data),
            'matched_count': matched_count,
            'unmatched_count': len(unmatched_events),
            'games_with_lines': len(games_with_lines),
            'espn_games': [
                {
                    'id': g.id,
                    'team1': g.team1,
                    'team2': g.team2,
                    'venue': g.venue
                }
                for g in games
            ],
            'api_events': [
                {
                    'home_team': e.get('home_team'),
                    'away_team': e.get('away_team'),
                    'bookmakers': [
                        {
                            'key': b.get('key'),
                            'markets': [m.get('key') for m in b.get('markets', [])]
                        }
                        for b in e.get('bookmakers', [])
                    ]
                }
                for e in data
            ],
            'matched_games': matched_games[:20],  # Limit for readability
            'unmatched_events_sample': unmatched_events[:20]
        }
        
        output_file = f"odds_api_test_{date_str}.json"
        with open(output_file, 'w') as f:
            json.dump(comparison, f, indent=2, default=str)
        print(f"   ✅ Saved to {output_file}")
        
    except requests.RequestException as e:
        print(f"\n   ❌ Request error: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"   Response: {e.response.text[:500]}")
    except Exception as e:
        print(f"\n   ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    test_odds_api()

