#!/usr/bin/env python3
"""Backfill PredictionModel records from modeler report"""

import sys
import re
from pathlib import Path
from datetime import date, datetime
sys.path.insert(0, str(Path(__file__).parent))

from src.data.storage import Database, PredictionModel, GameModel
from src.utils.logging import get_logger

logger = get_logger("backfill_predictions")


def parse_modeler_report(report_path: Path) -> list:
    """Parse modeler report and extract predictions"""
    predictions = []
    
    with open(report_path, 'r') as f:
        content = f.read()
    
    # Find all game model sections - match more flexibly
    # Pattern: GAME MODEL #N, Game ID: X, then find Predicted Score and Total
    game_sections = re.split(r'GAME MODEL #\d+', content)
    
    for section in game_sections[1:]:  # Skip first empty section
        # Extract game ID
        game_id_match = re.search(r'Game ID:\s*(\d+)', section)
        if not game_id_match:
            continue
        
        game_id = int(game_id_match.group(1))
        
        # Extract predicted scores
        score_match = re.search(r'Predicted Score:\s*Away\s*([\d.]+)\s*-\s*Home\s*([\d.]+)', section)
        if not score_match:
            continue
        
        away_score = float(score_match.group(1))
        home_score = float(score_match.group(2))
        
        # Calculate spread (home - away, negative means home favored)
        spread = home_score - away_score
        
        # Extract total
        total_match = re.search(r'Total:\s*([\d.]+|N/A)', section)
        total = None
        if total_match:
            total_str = total_match.group(1)
            if total_str != "N/A":
                try:
                    total = float(total_str)
                except ValueError:
                    pass
        
        # If total is None, calculate from scores
        if total is None:
            total = home_score + away_score
        
        # Extract win probabilities if available
        prob_pattern = r'Moneyline Probabilities:\s*Away\s*([\d.]+)%\s*/\s*Home\s*([\d.]+)%'
        prob_match = re.search(prob_pattern, section)
        
        win_prob_away = 0.5
        win_prob_home = 0.5
        if prob_match:
            win_prob_away = float(prob_match.group(1)) / 100.0
            win_prob_home = float(prob_match.group(2)) / 100.0
        
        predictions.append({
            'game_id': game_id,
            'spread': spread,
            'total': total,
            'home_score': home_score,
            'away_score': away_score,
            'win_prob_team1': win_prob_home,  # team1 is home
            'win_prob_team2': win_prob_away,  # team2 is away
        })
    
    return predictions


def backfill_predictions(report_path: Path, target_date: date):
    """Backfill PredictionModel records from modeler report"""
    db = Database()
    session = db.get_session()
    
    try:
        # Parse predictions from report
        logger.info(f"Parsing modeler report: {report_path}")
        predictions = parse_modeler_report(report_path)
        logger.info(f"Found {len(predictions)} predictions in report")
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for pred_data in predictions:
            game_id = pred_data['game_id']
            
            # Check if game exists
            game = session.query(GameModel).filter_by(id=game_id).first()
            if not game:
                logger.warning(f"Game {game_id} not found, skipping")
                skipped_count += 1
                continue
            
            # Check if prediction already exists
            existing = session.query(PredictionModel).filter_by(
                game_id=game_id
            ).order_by(PredictionModel.created_at.desc()).first()
            
            if existing:
                # Update existing prediction
                existing.predicted_spread = pred_data['spread']
                existing.predicted_total = pred_data['total']
                existing.win_probability_team1 = pred_data['win_prob_team1']
                existing.win_probability_team2 = pred_data['win_prob_team2']
                existing.confidence_score = 0.5  # Default confidence
                existing.ev_estimate = 0.0  # Default EV
                existing.mispricing_detected = False
                updated_count += 1
                logger.debug(f"Updated prediction for game_id={game_id}")
            else:
                # Create new prediction
                prediction = PredictionModel(
                    game_id=game_id,
                    model_type="modeler",
                    predicted_spread=pred_data['spread'],
                    predicted_total=pred_data['total'],
                    win_probability_team1=pred_data['win_prob_team1'],
                    win_probability_team2=pred_data['win_prob_team2'],
                    ev_estimate=0.0,  # Default EV
                    confidence_score=0.5,  # Default confidence
                    mispricing_detected=False,
                    created_at=datetime.combine(target_date, datetime.min.time())
                )
                session.add(prediction)
                created_count += 1
                logger.debug(f"Created prediction for game_id={game_id}: spread={pred_data['spread']}, total={pred_data['total']}")
        
        session.commit()
        logger.info(f"Backfill complete: {created_count} created, {updated_count} updated, {skipped_count} skipped")
        
        return created_count + updated_count
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error backfilling predictions: {e}", exc_info=True)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill PredictionModel records from modeler report')
    parser.add_argument('--date', type=str, required=True, help='Date in YYYY-MM-DD format')
    parser.add_argument('--report', type=str, help='Path to modeler report (default: data/reports/modeler/modeler_YYYY-MM-DD.txt)')
    
    args = parser.parse_args()
    
    target_date = date.fromisoformat(args.date)
    
    if args.report:
        report_path = Path(args.report)
    else:
        report_path = Path(f"data/reports/modeler/modeler_{target_date.isoformat()}.txt")
    
    if not report_path.exists():
        logger.error(f"Modeler report not found: {report_path}")
        sys.exit(1)
    
    count = backfill_predictions(report_path, target_date)
    print(f"Successfully backfilled {count} predictions")
    sys.exit(0 if count > 0 else 1)

