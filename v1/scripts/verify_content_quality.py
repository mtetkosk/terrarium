#!/usr/bin/env python3
"""Verify that filtered content still contains valuable information"""

import json
import sys
from pathlib import Path
from datetime import date

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.web_browser import get_web_browser

def check_content_quality(content: str, content_type: str) -> dict:
    """Check if content contains valuable information"""
    content_lower = content.lower()
    
    # Define valuable keywords for different content types
    valuable_keywords = {
        'advanced_stats': [
            'adjo', 'adjd', 'adjt', 'adjusted offense', 'adjusted defense', 'adjusted tempo',
            'kenpom', 'torvik', 'rank', 'efficiency', 'net rating', 'conference',
            'wins', 'losses', 'record', 'sos', 'luck', 'ncsos'
        ],
        'predictions': [
            'prediction', 'pick', 'predicted', 'forecast', 'winner',
            'spread', 'total', 'over', 'under', 'moneyline',
            'expert', 'analysis', 'odds', 'line', 'betting',
            'score', 'points', 'margin', 'favorite', 'underdog'
        ],
        'injuries': [
            'injury', 'injured', 'out', 'questionable', 'doubtful', 'probable',
            'status', 'player', 'lineup', 'availability', 'gtd', 'game-time decision'
        ],
        'team_stats': [
            'points', 'ppg', 'points per game', 'scoring', 'offense', 'defense',
            'rebounds', 'assists', 'field goal', 'three point', 'free throw',
            'efficiency', 'rating', 'pace', 'stats', 'statistics'
        ]
    }
    
    keywords = valuable_keywords.get(content_type, valuable_keywords['predictions'])
    found_keywords = [kw for kw in keywords if kw in content_lower]
    
    # Check for numbers (stats, scores, etc.)
    import re
    numbers = re.findall(r'\d+\.?\d*', content)
    has_numbers = len(numbers) > 0
    
    # Check for team names (if provided)
    has_team_context = any(word in content_lower for word in ['team', 'vs', 'versus', 'game', 'matchup'])
    
    return {
        'found_keywords': found_keywords,
        'keyword_count': len(found_keywords),
        'has_numbers': has_numbers,
        'number_count': len(numbers),
        'has_team_context': has_team_context,
        'content_length': len(content),
        'is_valuable': len(found_keywords) >= 3 or (has_numbers and has_team_context)
    }

def test_content_extraction():
    """Test actual content extraction quality"""
    web_browser = get_web_browser()
    
    print("=" * 80)
    print("VERIFYING CONTENT QUALITY AFTER FILTERING")
    print("=" * 80)
    
    # Test 1: Advanced Stats
    print("\n1. TESTING ADVANCED STATS EXTRACTION:")
    print("-" * 80)
    team = "Duke"
    stats = web_browser.search_advanced_stats(team, sport="basketball", target_date=date(2025, 11, 24))
    
    for i, result in enumerate(stats):
        content = result.get('content', '')
        quality = check_content_quality(content, 'advanced_stats')
        
        print(f"\n  Result {i+1} ({result.get('source', 'Unknown')}):")
        print(f"    Content length: {len(content)} chars")
        print(f"    Found keywords: {quality['keyword_count']} ({', '.join(quality['found_keywords'][:5])})")
        print(f"    Has numbers: {quality['has_numbers']} ({quality['number_count']} numbers)")
        print(f"    Is valuable: {'✅ YES' if quality['is_valuable'] else '❌ NO'}")
        print(f"    Content preview:")
        print(f"      {content[:200]}...")
        
        if not quality['is_valuable']:
            print(f"    ⚠️  WARNING: Content may not contain useful stats!")
    
    # Test 2: Game Predictions
    print("\n\n2. TESTING GAME PREDICTIONS EXTRACTION:")
    print("-" * 80)
    team1 = "Duke"
    team2 = "North Carolina"
    predictions = web_browser.search_game_predictions(
        team1, team2, sport="basketball", game_date=date(2025, 11, 24)
    )
    
    for i, result in enumerate(predictions):
        content = result.get('content', '')
        quality = check_content_quality(content, 'predictions')
        
        print(f"\n  Result {i+1} ({result.get('source', 'Unknown')}):")
        print(f"    Content length: {len(content)} chars")
        print(f"    Found keywords: {quality['keyword_count']} ({', '.join(quality['found_keywords'][:5])})")
        print(f"    Has numbers: {quality['has_numbers']} ({quality['number_count']} numbers)")
        print(f"    Has team context: {quality['has_team_context']}")
        print(f"    Is valuable: {'✅ YES' if quality['is_valuable'] else '❌ NO'}")
        print(f"    Content preview:")
        print(f"      {content[:300]}...")
        
        if not quality['is_valuable']:
            print(f"    ⚠️  WARNING: Content may not contain useful predictions!")
    
    # Test 3: Compare raw vs filtered content
    print("\n\n3. COMPARING RAW VS FILTERED CONTENT:")
    print("-" * 80)
    
    # Get a prediction URL and compare
    if predictions:
        test_result = predictions[0]
        url = test_result.get('url', '')
        
        if url:
            print(f"\n  Testing URL: {url}")
            
            # Fetch raw content (old method - no filtering)
            try:
                import requests
                from bs4 import BeautifulSoup
                session = requests.Session()
                session.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                })
                response = session.get(url, timeout=10)
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Remove script/style only (old method)
                for element in soup(["script", "style"]):
                    element.decompose()
                raw_text = soup.get_text()
                raw_text = ' '.join(raw_text.split())  # Clean whitespace
                raw_text = raw_text[:2000]  # Limit to 2000 for comparison
                
                # Get filtered content (new method)
                filtered_content = test_result.get('content', '')
                
                print(f"\n  Raw content (old method):")
                print(f"    Length: {len(raw_text)} chars")
                raw_quality = check_content_quality(raw_text, 'predictions')
                print(f"    Keywords found: {raw_quality['keyword_count']}")
                print(f"    Preview: {raw_text[:200]}...")
                
                print(f"\n  Filtered content (new method):")
                print(f"    Length: {len(filtered_content)} chars")
                filtered_quality = check_content_quality(filtered_content, 'predictions')
                print(f"    Keywords found: {filtered_quality['keyword_count']}")
                print(f"    Preview: {filtered_content[:200]}...")
                
                print(f"\n  Comparison:")
                print(f"    Size reduction: {len(raw_text) - len(filtered_content)} chars ({((len(raw_text) - len(filtered_content)) / len(raw_text) * 100):.1f}%)")
                print(f"    Keywords preserved: {filtered_quality['keyword_count']}/{raw_quality['keyword_count']} ({'✅' if filtered_quality['keyword_count'] >= raw_quality['keyword_count'] * 0.8 else '⚠️'})")
                
                if filtered_quality['keyword_count'] < raw_quality['keyword_count'] * 0.8:
                    print(f"    ⚠️  WARNING: Lost significant keywords in filtering!")
                else:
                    print(f"    ✅ Good: Most keywords preserved despite size reduction")
                    
            except Exception as e:
                print(f"    Error comparing: {e}")
    
    # Summary
    print("\n\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    
    all_stats_valuable = all(check_content_quality(r.get('content', ''), 'advanced_stats')['is_valuable'] for r in stats)
    all_predictions_valuable = all(check_content_quality(r.get('content', ''), 'predictions')['is_valuable'] for r in predictions)
    
    print(f"  Advanced stats valuable: {'✅ YES' if all_stats_valuable else '❌ NO'}")
    print(f"  Predictions valuable: {'✅ YES' if all_predictions_valuable else '❌ NO'}")
    
    if all_stats_valuable and all_predictions_valuable:
        print(f"\n  ✅ OVERALL: Content filtering is preserving valuable information!")
    else:
        print(f"\n  ⚠️  OVERALL: Some content may have lost valuable information. Review needed.")

if __name__ == "__main__":
    test_content_extraction()

