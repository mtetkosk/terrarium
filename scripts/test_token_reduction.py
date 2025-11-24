#!/usr/bin/env python3
"""Test token reduction from scraping optimizations"""

import json
import sys
from pathlib import Path
from datetime import date

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.web_browser import get_web_browser
from src.data.models import Game, GameStatus
from src.agents.researcher.researcher import Researcher
from src.data.storage import Database

def estimate_tokens(text) -> int:
    """Rough token estimation (4 chars per token for English)"""
    if isinstance(text, int):
        return text // 4
    return len(str(text)) // 4

def test_token_usage():
    """Test actual token usage with optimizations"""
    print("=" * 80)
    print("TESTING TOKEN REDUCTION FROM SCRAPING OPTIMIZATIONS")
    print("=" * 80)
    
    # Initialize components
    db = Database()
    researcher = Researcher(db=db)
    web_browser = get_web_browser()
    
    # Create a test game (Duke vs UNC)
    test_game = Game(
        id=1,
        team1="Duke",
        team2="North Carolina",
        date=date(2025, 11, 24),
        venue="Cameron Indoor Stadium",
        status=GameStatus.SCHEDULED
    )
    
    print(f"\nTest game: {test_game.team2} @ {test_game.team1} on {test_game.date}")
    print("-" * 80)
    
    # Simulate what happens in _process_batch
    print("\n1. SIMULATING SCRAPING FOR ONE GAME:")
    print("-" * 80)
    
    total_chars = 0
    tool_results = {}
    
    # Simulate advanced stats searches (2 teams)
    print("\n  Advanced Stats Searches:")
    for team in [test_game.team1, test_game.team2]:
        print(f"    Searching for: {team}")
        stats = web_browser.search_advanced_stats(team, sport="basketball", target_date=test_game.date)
        for i, result in enumerate(stats):
            content_len = len(result.get('content', ''))
            total_chars += content_len
            print(f"      Result {i+1}: {content_len:,} chars")
            tool_results[f"advanced_stats_{team}_{i}"] = result
    
    # Simulate game predictions search
    print(f"\n  Game Predictions Search:")
    print(f"    Searching for: {test_game.team1} vs {test_game.team2}")
    predictions = web_browser.search_game_predictions(
        test_game.team1, 
        test_game.team2, 
        sport="basketball", 
        game_date=test_game.date
    )
    for i, result in enumerate(predictions):
        content_len = len(result.get('content', ''))
        total_chars += content_len
        print(f"      Result {i+1}: {content_len:,} chars")
        tool_results[f"predictions_{i}"] = result
    
    print(f"\n  Total scraped content: {total_chars:,} chars (~{estimate_tokens(total_chars):,} tokens)")
    
    # Now simulate what gets sent to LLM (with truncation)
    print("\n2. SIMULATING TRUNCATION (as in researcher.py):")
    print("-" * 80)
    
    truncated_chars = 0
    MAX_TOOL_RESULT_SIZE = 8000  # New limit
    
    for key, result in tool_results.items():
        result_str = json.dumps(result)
        if len(result_str) > MAX_TOOL_RESULT_SIZE:
            # Apply truncation logic
            if isinstance(result, list):
                # Keep top 5 items, max 2000 chars each
                truncated = []
                for item in result[:5]:
                    if isinstance(item, dict) and 'content' in item:
                        content = item.get('content', '')
                        if len(content) > 2000:
                            content = content[:2000] + "... [truncated]"
                            item = item.copy()
                            item['content'] = content
                    truncated.append(item)
                result_str = json.dumps(truncated)
            elif isinstance(result, dict):
                truncated_result = {}
                for k, v in result.items():
                    if isinstance(v, str):
                        max_len = 2000 if k == 'content' else 1000
                        if len(v) > max_len:
                            truncated_result[k] = v[:max_len] + "... [truncated]"
                        else:
                            truncated_result[k] = v
                    else:
                        truncated_result[k] = v
                result_str = json.dumps(truncated_result)
            else:
                result_str = result_str[:MAX_TOOL_RESULT_SIZE] + '... [truncated]'
        
        truncated_chars += len(result_str)
    
    print(f"  After truncation: {truncated_chars:,} chars (~{estimate_tokens(truncated_chars):,} tokens)")
    if total_chars > 0:
        print(f"  Reduction: {total_chars - truncated_chars:,} chars ({((total_chars - truncated_chars) / total_chars * 100):.1f}%)")
    
    # Estimate for 5 games (batch size)
    print("\n3. ESTIMATING FOR FULL BATCH (5 games):")
    print("-" * 80)
    
    # Per game: 2 advanced stats + 1 predictions
    per_game_chars = truncated_chars
    batch_chars = per_game_chars * 5
    
    # Add system prompt, user prompt, and input_data
    from src.prompts import RESEARCHER_PROMPT, RESEARCHER_BATCH_PROMPT
    system_prompt_chars = len(RESEARCHER_PROMPT)
    user_prompt_chars = len(RESEARCHER_BATCH_PROMPT.format(num_games=5))
    
    # Input data (games info) - minimal
    input_data = {
        "games": [{
            "game_id": "1",
            "teams": {"away": test_game.team2, "home": test_game.team1},
            "date": test_game.date.isoformat(),
            "venue": test_game.venue,
            "status": test_game.status.value
        }] * 5
    }
    input_data_chars = len(json.dumps(input_data))
    
    total_prompt_chars = system_prompt_chars + user_prompt_chars + input_data_chars + batch_chars
    total_prompt_tokens = estimate_tokens(total_prompt_chars)
    
    print(f"  System prompt: {system_prompt_chars:,} chars (~{estimate_tokens(system_prompt_chars):,} tokens)")
    print(f"  User prompt: {user_prompt_chars:,} chars (~{estimate_tokens(user_prompt_chars):,} tokens)")
    print(f"  Input data: {input_data_chars:,} chars (~{estimate_tokens(input_data_chars):,} tokens)")
    print(f"  Tool results (5 games): {batch_chars:,} chars (~{estimate_tokens(batch_chars):,} tokens)")
    print(f"  TOTAL PROMPT: {total_prompt_chars:,} chars (~{total_prompt_tokens:,} tokens)")
    
    print("\n" + "=" * 80)
    print("COMPARISON:")
    print("=" * 80)
    print(f"  Before optimizations (estimated): ~25,975 tokens")
    print(f"  After optimizations (estimated):  ~{total_prompt_tokens:,} tokens")
    print(f"  Reduction: ~{25975 - total_prompt_tokens:,} tokens ({((25975 - total_prompt_tokens) / 25975 * 100):.1f}%)")
    
    if total_prompt_tokens < 20000:
        print(f"\n✅ SUCCESS! Prompt size is now below 20K tokens (no warning needed)")
    else:
        print(f"\n⚠️  Still above 20K tokens, but reduced by {((25975 - total_prompt_tokens) / 25975 * 100):.1f}%")

if __name__ == "__main__":
    test_token_usage()

