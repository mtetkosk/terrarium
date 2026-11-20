#!/usr/bin/env python3
"""Test script to verify UMass Lowell bet settlement and re-settle if needed"""

from datetime import date
from src.agents.results_processor import ResultsProcessor
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import Database, PickModel, BetModel, GameModel
from sqlalchemy import func

def test_umass_lowell_settlement():
    """Test the exact UMass Lowell scenario"""
    db = Database()
    processor = ResultsProcessor(db=db)
    
    session = db.get_session()
    try:
        # Find the actual spread pick from the database (there might be multiple picks for the same game)
        pick = session.query(PickModel).filter(
            PickModel.game_id == 316,
            func.date(PickModel.created_at) == date(2025, 11, 22),
            PickModel.bet_type == BetType.SPREAD
        ).first()
        
        # If not found, try by pick ID 1050 (the specific spread bet mentioned)
        if not pick:
            pick = session.query(PickModel).filter_by(id=1050).first()
        
        if not pick:
            print("Pick not found in database")
            return
        
        print(f"\n=== PICK DETAILS ===")
        print(f"Pick ID: {pick.id}")
        print(f"Game ID: {pick.game_id}")
        print(f"Bet Type: {pick.bet_type}")
        print(f"Line: {pick.line}")
        print(f"Rationale: {pick.rationale}")
        print(f"Selection Text: {pick.selection_text}")
        
        # Get game details
        game = session.query(GameModel).filter_by(id=316).first()
        if not game:
            print("Game not found")
            return
        
        print(f"\n=== GAME DETAILS ===")
        print(f"Team1 (Home): {game.team1}")
        print(f"Team2 (Away): {game.team2}")
        print(f"Game Result: {game.result}")
        
        # Get current bet result
        bet = session.query(BetModel).filter_by(pick_id=pick.id).first()
        if bet:
            print(f"\n=== CURRENT BET RESULT ===")
            print(f"Result: {bet.result}")
            print(f"Payout: {bet.payout}")
            print(f"Profit/Loss: {bet.profit_loss}")
        
        # Now test the settlement logic manually
        print(f"\n=== TESTING SETTLEMENT LOGIC ===")
        if game.result:
            result_data = game.result
            home_score = result_data.get('home_score') or result_data.get('homeScore')
            away_score = result_data.get('away_score') or result_data.get('awayScore')
            
            if home_score is not None and away_score is not None:
                home_score = int(float(home_score))
                away_score = int(float(away_score))
                
                print(f"Home Score: {home_score}")
                print(f"Away Score: {away_score}")
                print(f"Margin (home - away): {home_score - away_score}")
                print(f"Margin (away - home): {away_score - home_score}")
                
                # Test the determination logic
                pick_rationale = pick.selection_text if pick.selection_text else pick.rationale
                bet_result = processor._determine_bet_result_from_attrs(
                    pick_game_id=316,
                    pick_bet_type=pick.bet_type,
                    pick_line=pick.line,
                    pick_rationale=pick_rationale,
                    game_result={
                        "game_id": 316,
                        "team1": game.team1,
                        "team2": game.team2,
                        "status": "final",
                        "result": game.result
                    },
                    session=session
                )
                
                print(f"\n=== SETTLEMENT RESULT ===")
                print(f"Determined Result: {bet_result}")
                print(f"Expected: WIN (away lost by 2, line is +7.5, so -2 > -7.5 = WIN)")
                
                margin = away_score - home_score
                line_negated = -pick.line
                check_result = margin > line_negated
                
                print(f"\n=== CALCULATION BREAKDOWN ===")
                print(f"Line: {pick.line}")
                print(f"Line negated (-line): {line_negated}")
                print(f"Margin (away - home): {margin}")
                print(f"Check: {margin} > {line_negated} = {check_result}")
                
                if bet_result != BetResult.WIN:
                    print(f"\n❌ ERROR: Expected WIN but got {bet_result}")
                    print(f"This suggests the settlement logic needs to be re-run")
                    
                    # Re-settle the bet
                    print(f"\n=== RE-SETTLING BET ===")
                    games_with_results = {
                        316: {
                            "game_id": 316,
                            "team1": game.team1,
                            "team2": game.team2,
                            "status": "final",
                            "result": game.result
                        }
                    }
                    pick_date = date(2025, 11, 22)
                    # Store bet id before settlement (bet may be detached after settlement)
                    bet_id = bet.id
                    settled_count = processor._settle_bets([pick], games_with_results, session, pick_date)
                    print(f"Re-settled {settled_count} bet(s)")
                    
                    # Re-query to get updated bet (bet may be detached from session)
                    session.commit()  # Ensure settlement changes are committed
                    updated_bet = session.query(BetModel).filter_by(id=bet_id).first()
                    if updated_bet:
                        print(f"New Result: {updated_bet.result}")
                        print(f"New Payout: {updated_bet.payout}")
                        print(f"New Profit/Loss: {updated_bet.profit_loss}")
                else:
                    print(f"\n✅ CORRECT: Result is WIN")
        
    finally:
        session.close()

if __name__ == "__main__":
    test_umass_lowell_settlement()

