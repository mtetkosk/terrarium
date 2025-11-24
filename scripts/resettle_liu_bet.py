#!/usr/bin/env python3
"""Re-settle the Long Island University bet (pick 1040) with corrected logic"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date
from src.data.storage import Database
from src.agents.results_processor import ResultsProcessor

def main():
    db = Database()
    processor = ResultsProcessor(db=db)
    
    # Re-settle bets for 2025-11-22 (the date of the Long Island bet)
    target_date = date(2025, 11, 22)
    
    print(f"Re-settling bets for {target_date}...")
    result = processor.process(target_date=target_date, force_refresh=True)
    
    print(f"\nResults:")
    print(f"  Total picks: {result.get('total_picks', 0)}")
    print(f"  Wins: {result.get('wins', 0)}")
    print(f"  Losses: {result.get('losses', 0)}")
    print(f"  Pushes: {result.get('pushes', 0)}")
    
    # Check the specific bets
    session = db.get_session()
    try:
        from src.data.storage import BetModel, PickModel
        picks_to_check = [1040, 1032]  # Long Island and Nicholls
        for pick_id in picks_to_check:
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            if bet:
                pick = session.query(PickModel).filter_by(id=pick_id).first()
                team_name = "Long Island University" if pick_id == 1040 else "Nicholls Colonels"
                print(f"\nPick {pick_id} ({team_name}):")
                print(f"  Result: {bet.result}")
                print(f"  Payout: ${bet.payout:.2f}")
                print(f"  Profit/Loss: ${bet.profit_loss:.2f}")
                if pick:
                    print(f"  Line: {pick.line}")
                    print(f"  Selection: {pick.selection_text or pick.rationale[:100]}")
            else:
                print(f"\nPick {pick_id} not found in bets table")
    finally:
        session.close()

if __name__ == "__main__":
    main()

