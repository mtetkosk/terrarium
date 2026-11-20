
import sys
import json
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path.cwd()))

from src.agents.modeler import Modeler
from src.data.storage import Database, GameModel, TeamModel
from src.data.models import Game
from src.utils.llm import get_llm_client

# Corrected stats for Albany (approximate but much closer to reality)
# AdjD rank 336 implies AdjD ~115-116.
ALBANY_CORRECTED_STATS = {
    "adjo": 100.8,
    "adjd": 115.4,
    "adjt": 68.0,
    "net": -14.56,
    "kp_rank": 336,
    "torvik_rank": 330,  # approximate
    "conference": "AE",
    "wins": 3,
    "losses": 7
}

def reproject_albany():
    print("Initializing reprocessing...")
    db = Database()
    session = db.get_session()
    
    try:
        # Find the game
        albany = session.query(TeamModel).filter(TeamModel.normalized_team_name.ilike('%Albany%')).first()
        if not albany:
            print("Albany not found!")
            return
            
        game = session.query(GameModel).filter(
            (GameModel.team1_id == albany.id) | (GameModel.team2_id == albany.id),
            GameModel.date == date(2025, 12, 10)
        ).first()
        
        if not game:
            print("Game not found!")
            return
            
        t1 = session.query(TeamModel).filter_by(id=game.team1_id).first()
        t2 = session.query(TeamModel).filter_by(id=game.team2_id).first()
        
        print(f"Found game: {t2.normalized_team_name} @ {t1.normalized_team_name}")
        
        # Determine which team is Albany
        albany_is_team1 = (t1.id == albany.id)
        
        # Prepare researcher output structure manually
        # We need to fetch the existing insight to get Yale's stats
        insight = game.insights
        if not insight:
            print("No existing insights found, cannot reproject without Yale stats.")
            return
            
        yale_stats = insight.team1_stats if not albany_is_team1 else insight.team2_stats
        
        # Update Albany's stats in the insight object (in memory only for now)
        if albany_is_team1:
            team1_stats_dict = ALBANY_CORRECTED_STATS
            team2_stats_dict = yale_stats
        else:
            team1_stats_dict = yale_stats
            team2_stats_dict = ALBANY_CORRECTED_STATS
            
        # Construct game data for Modeler
        game_data = {
            "game_id": str(game.id),
            "teams": {
                "away": t2.normalized_team_name,
                "home": t1.normalized_team_name
            },
            "date": str(game.date),
            "adv": {
                "away": team2_stats_dict,
                "home": team1_stats_dict,
                "data_unavailable": False
            },
            # Dummy data for other fields required by prompt but less critical for raw projection
            "market": {
                "spread": f"{t1.normalized_team_name} -18.5" 
            }, 
            "injuries": [],
            "recent": {
                "away": {"rec": "3-7"},
                "home": {"rec": "unknown"}
            }
        }
        
        researcher_output = {
            "games": [game_data]
        }
        
        betting_lines = [] # Modeler can run without explicit lines object if market is in game_data
        
        # Run Modeler
        print("Running Modeler with corrected stats...")
        modeler = Modeler(db)
        
        # Force refresh to bypass cache
        result = modeler.process(
            researcher_output=researcher_output, 
            betting_lines=None, 
            target_date=date(2025, 12, 10),
            force_refresh=True
        )
        
        # Extract prediction
        models = result.get("game_models", [])
        if not models:
            print("No models returned!")
            return
            
        model = models[0]
        preds = model.get("predictions", {})
        spread = preds.get("spread", {})
        
        print("\n" + "="*50)
        print("NEW PROJECTION (ALBANY CORRECTED)")
        print("="*50)
        print(f"Projected Spread: {spread.get('projected_margin')}")
        print(f"Confidence: {spread.get('model_confidence')}")
        print(f"Reasoning: {model.get('model_notes')}")
        
    finally:
        session.close()
        db.close()

if __name__ == "__main__":
    reproject_albany()




