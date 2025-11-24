#!/usr/bin/env python3
"""Check what picks exist for game_id 343 on 2025-11-22"""

from datetime import date
from src.data.storage import Database, PickModel, GameModel, BetModel
from sqlalchemy import func

db = Database()
session = db.get_session()

try:
    # Get all picks for game_id 343 on 2025-11-22
    target_date = date(2025, 11, 22)
    
    picks = session.query(PickModel).filter(
        PickModel.game_id == 343,
        func.date(PickModel.created_at) == target_date
    ).all()
    
    print(f"\n{'='*80}")
    print(f"PICKS FOR GAME_ID 343 ON {target_date}")
    print(f"{'='*80}\n")
    
    if not picks:
        print("No picks found!")
    else:
        for i, pick in enumerate(picks, 1):
            game = session.query(GameModel).filter_by(id=pick.game_id).first()
            bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
            
            print(f"PICK #{i}")
            print(f"  ID: {pick.id}")
            print(f"  Game ID: {pick.game_id}")
            print(f"  Bet Type: {pick.bet_type.value}")
            print(f"  Line: {pick.line}")
            print(f"  Odds: {pick.odds}")
            print(f"  Selection Text: {pick.selection_text}")
            print(f"  Rationale: {pick.rationale[:100]}..." if pick.rationale and len(pick.rationale) > 100 else f"  Rationale: {pick.rationale}")
            print(f"  Best Bet: {pick.best_bet}")
            print(f"  Created At: {pick.created_at}")
            
            if game:
                print(f"  Game: {game.team1} vs {game.team2}")
                if game.result:
                    print(f"  Result: {game.result}")
            
            if bet:
                print(f"  Bet Result: {bet.result.value if bet.result else 'None'}")
                print(f"  Profit/Loss: ${bet.profit_loss:.2f}" if bet.profit_loss else "  Profit/Loss: $0.00")
            
            print()
    
    # Also check if there are picks for other dates
    all_picks_343 = session.query(PickModel).filter(
        PickModel.game_id == 343
    ).all()
    
    if len(all_picks_343) > len(picks):
        print(f"\n{'='*80}")
        print(f"ALL PICKS FOR GAME_ID 343 (ALL DATES)")
        print(f"{'='*80}\n")
        for pick in all_picks_343:
            print(f"Date: {pick.created_at.date()}, Bet Type: {pick.bet_type.value}, Line: {pick.line}, Best Bet: {pick.best_bet}, Selection: {pick.selection_text}")
    
finally:
    session.close()

