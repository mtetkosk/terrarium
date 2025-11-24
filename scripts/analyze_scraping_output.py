#!/usr/bin/env python3
"""Analyze what's being scraped to understand prompt size"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.web_browser import get_web_browser
from src.data.models import Game, GameStatus
from datetime import date

def analyze_scraping_output():
    """Test scraping and analyze output sizes"""
    web_browser = get_web_browser()
    
    # Test with a sample team
    test_team = "Duke"
    
    print("=" * 80)
    print(f"Testing scraping for: {test_team}")
    print("=" * 80)
    
    # Test 1: Advanced stats
    print("\n1. TESTING search_advanced_stats:")
    print("-" * 80)
    advanced_stats = web_browser.search_advanced_stats(test_team, sport="basketball")
    print(f"Number of results: {len(advanced_stats)}")
    
    total_chars = 0
    for i, result in enumerate(advanced_stats):
        content_len = len(result.get('content', ''))
        total_chars += content_len
        print(f"\n  Result {i+1}:")
        print(f"    Source: {result.get('source', 'N/A')}")
        print(f"    URL: {result.get('url', 'N/A')}")
        print(f"    Content length: {content_len:,} chars")
        print(f"    Is advanced stats: {result.get('is_advanced_stats', False)}")
        print(f"    Is KenPom: {result.get('is_kenpom', False)}")
        print(f"    Is Torvik: {result.get('is_torvik', False)}")
        # Show first 200 chars of content
        content_preview = result.get('content', '')[:200]
        print(f"    Content preview: {content_preview}...")
    
    print(f"\n  Total chars from advanced stats: {total_chars:,}")
    
    # Test 2: Game predictions
    print("\n2. TESTING search_game_predictions:")
    print("-" * 80)
    team1 = "Duke"
    team2 = "North Carolina"
    game_date = date(2025, 11, 24)
    
    predictions = web_browser.search_game_predictions(team1, team2, sport="basketball", game_date=game_date)
    print(f"Number of results: {len(predictions)}")
    
    total_chars = 0
    for i, result in enumerate(predictions):
        content_len = len(result.get('content', ''))
        total_chars += content_len
        print(f"\n  Result {i+1}:")
        print(f"    Source: {result.get('source', 'N/A')}")
        print(f"    URL: {result.get('url', 'N/A')}")
        print(f"    Content length: {content_len:,} chars")
        # Show first 200 chars of content
        content_preview = result.get('content', '')[:200]
        print(f"    Content preview: {content_preview}...")
    
    print(f"\n  Total chars from predictions: {total_chars:,}")
    
    # Test 3: Team stats
    print("\n3. TESTING search_team_stats:")
    print("-" * 80)
    team_stats = web_browser.search_team_stats(test_team, sport="basketball")
    print(f"Number of results: {len(team_stats)}")
    
    total_chars = 0
    for i, result in enumerate(team_stats):
        content_len = len(result.get('content', ''))
        total_chars += content_len
        print(f"\n  Result {i+1}:")
        print(f"    Source: {result.get('source', 'N/A')}")
        print(f"    URL: {result.get('url', 'N/A')}")
        print(f"    Content length: {content_len:,} chars")
        print(f"    Is advanced stats: {result.get('is_advanced_stats', False)}")
        # Show first 200 chars of content
        content_preview = result.get('content', '')[:200]
        print(f"    Content preview: {content_preview}...")
    
    print(f"\n  Total chars from team stats: {total_chars:,}")
    
    # Estimate token usage (rough: 4 chars per token)
    print("\n" + "=" * 80)
    print("TOKEN ESTIMATION (rough: 4 chars per token):")
    print("=" * 80)
    print(f"Advanced stats: ~{total_chars // 4:,} tokens")
    print(f"Predictions: ~{total_chars // 4:,} tokens")
    print(f"Team stats: ~{total_chars // 4:,} tokens")
    
    # Simulate what would happen with 5 games, 2 teams each
    print("\n" + "=" * 80)
    print("SIMULATION: 5 games, 2 teams per game:")
    print("=" * 80)
    print("Assuming each game needs:")
    print("  - 2x advanced_stats (one per team)")
    print("  - 1x game_predictions (per game)")
    print("  - Possibly 2x team_stats (fallback)")
    print()
    
    # Rough estimate
    avg_advanced_stats_chars = 2000  # Conservative estimate
    avg_predictions_chars = 1500
    avg_team_stats_chars = 1000
    
    per_game_chars = (2 * avg_advanced_stats_chars) + avg_predictions_chars + (2 * avg_team_stats_chars)
    per_batch_chars = 5 * per_game_chars
    
    print(f"Per game: ~{per_game_chars:,} chars (~{per_game_chars // 4:,} tokens)")
    print(f"Per batch (5 games): ~{per_batch_chars:,} chars (~{per_batch_chars // 4:,} tokens)")
    print(f"\nThis is likely contributing to the 25K+ token prompts!")

if __name__ == "__main__":
    analyze_scraping_output()

