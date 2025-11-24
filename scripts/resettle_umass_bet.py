#!/usr/bin/env python3
"""Re-settle the UMass Lowell bet (pick ID 1050) with the corrected logic"""

from datetime import date
from src.agents.results_processor import ResultsProcessor
from src.data.storage import Database, PickModel, BetModel, GameModel
from src.data.models import BetResult
from sqlalchemy import func

def resettle_bet():
    """Re-settle the UMass Lowell bet"""
    db = Database()
    processor = ResultsProcessor(db=db)
    
    session = db.get_session()
    try:
        # Get the pick
        pick = session.query(PickModel).filter_by(id=1050).first()
        if not pick:
            print("Pick 1050 not found")
            return
        
        # Get the game
        game = session.query(GameModel).filter_by(id=316).first()
        if not game:
            print("Game 316 not found")
            return
        
        # Get current bet
        bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
        if bet:
            print(f"Current result: {bet.result}")
            print(f"Current payout: {bet.payout}")
            print(f"Current profit/loss: {bet.profit_loss}")
        else:
            print("No bet found, will create one")
        
        # Prepare game result
        games_with_results = {
            316: {
                "game_id": 316,
                "team1": game.team1,
                "team2": game.team2,
                "status": "final",
                "result": game.result
            }
        }
        
        # Re-settle
        pick_date = date(2025, 11, 22)
        settled_count = processor._settle_bets([pick], games_with_results, session, pick_date)
        
        print(f"\nRe-settled {settled_count} bet(s)")
        
        # Check new result - need to query fresh
        session.commit()  # Commit the changes
        bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
        if bet:
            print(f"New result: {bet.result}")
            print(f"New payout: {bet.payout}")
            print(f"New profit/loss: {bet.profit_loss}")
        
        if pick.bet.result == BetResult.WIN:
            print("\n✅ SUCCESS: Bet is now correctly marked as WIN")
        else:
            print(f"\n❌ ERROR: Bet is still {pick.bet.result}, expected WIN")
        
    finally:
        session.close()

if __name__ == "__main__":
    resettle_bet()

