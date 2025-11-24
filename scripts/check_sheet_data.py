#!/usr/bin/env python3
"""Quick script to check if data exists for Google Sheets export"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from src.data.storage import Database, PickModel, PredictionModel, GameModel
from datetime import date
from sqlalchemy import func

db = Database()
session = db.get_session()

# Check game 310
print("=" * 60)
print("Checking Game 310")
print("=" * 60)
game = session.query(GameModel).filter_by(id=310).first()
if game:
    print(f"Game found: {game.team1} vs {game.team2} on {game.date}")
    print(f"Result: {game.result}")
    if game.result:
        print(f"  home_score: {game.result.get('home_score')}")
        print(f"  away_score: {game.result.get('away_score')}")
else:
    print("Game 310 not found")

# Check predictions for game 310
predictions = session.query(PredictionModel).filter_by(game_id=310).all()
print(f"\nPredictions for game 310: {len(predictions)} found")
for p in predictions:
    print(f"  - ID: {p.id}, spread: {p.predicted_spread}, total: {p.predicted_total}, created: {p.created_at}")

# Check picks for game 310
picks = session.query(PickModel).filter_by(game_id=310).all()
print(f"\nPicks for game 310: {len(picks)} found")
for p in picks:
    print(f"  - pick_id: {p.id}, bet_type: {p.bet_type.value}, line: {p.line}, created: {p.created_at}")

# Check game 347
print("\n" + "=" * 60)
print("Checking Game 347")
print("=" * 60)
game = session.query(GameModel).filter_by(id=347).first()
if game:
    print(f"Game found: {game.team1} vs {game.team2} on {game.date}")
    print(f"Result: {game.result}")
    if game.result:
        print(f"  home_score: {game.result.get('home_score')}")
        print(f"  away_score: {game.result.get('away_score')}")
else:
    print("Game 347 not found")

# Check predictions for game 347
predictions = session.query(PredictionModel).filter_by(game_id=347).all()
print(f"\nPredictions for game 347: {len(predictions)} found")
for p in predictions:
    print(f"  - ID: {p.id}, spread: {p.predicted_spread}, total: {p.predicted_total}, created: {p.created_at}")

session.close()

