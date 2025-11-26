#!/usr/bin/env python3
"""Generate LLM-optimized performance report from games, research, picks, and results"""

import sys
import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from src.data.storage import (
    Database,
    GameModel,
    GameInsightModel,
    PredictionModel,
    PickModel,
    BetModel,
    BettingLineModel,
    TeamModel,
    BetType,
    BetResult,
    GameStatus
)
from src.utils.logging import get_logger

logger = get_logger("scripts.llm_performance_report")


def query_games(session: Session, start_date: date, end_date: date) -> List[GameModel]:
    """Query all games in date range"""
    return session.query(GameModel).filter(
        and_(
            GameModel.date >= start_date,
            GameModel.date <= end_date
        )
    ).all()


def query_research(session: Session, game_ids: List[int]) -> Dict[int, GameInsightModel]:
    """Query game insights for given game IDs"""
    insights = session.query(GameInsightModel).filter(
        GameInsightModel.game_id.in_(game_ids)
    ).all()
    return {insight.game_id: insight for insight in insights}


def query_predictions(session: Session, game_ids: List[int], start_date: date, end_date: date) -> Dict[int, PredictionModel]:
    """Query predictions for games in date range"""
    predictions = session.query(PredictionModel).filter(
        and_(
            PredictionModel.game_id.in_(game_ids),
            PredictionModel.prediction_date >= start_date,
            PredictionModel.prediction_date <= end_date
        )
    ).order_by(PredictionModel.prediction_date.desc()).all()
    
    # Get most recent prediction per game
    result = {}
    for pred in predictions:
        if pred.game_id not in result:
            result[pred.game_id] = pred
    return result


def query_picks(session: Session, start_date: date, end_date: date) -> List[PickModel]:
    """Query picks in date range"""
    return session.query(PickModel).filter(
        or_(
            and_(
                PickModel.pick_date.isnot(None),
                PickModel.pick_date >= start_date,
                PickModel.pick_date <= end_date
            ),
            and_(
                PickModel.pick_date.is_(None),
                func.date(PickModel.created_at) >= start_date,
                func.date(PickModel.created_at) <= end_date
            )
        )
    ).all()


def query_bets(session: Session, pick_ids: List[int]) -> Dict[int, BetModel]:
    """Query bets for given pick IDs"""
    bets = session.query(BetModel).filter(
        BetModel.pick_id.in_(pick_ids)
    ).all()
    return {bet.pick_id: bet for bet in bets}


def query_betting_lines(session: Session, game_ids: List[int]) -> Dict[int, List[BettingLineModel]]:
    """Query betting lines for given game IDs"""
    lines = session.query(BettingLineModel).filter(
        BettingLineModel.game_id.in_(game_ids)
    ).all()
    result = defaultdict(list)
    for line in lines:
        result[line.game_id].append(line)
    return dict(result)


def get_team_name(session: Session, team_id: Optional[int]) -> str:
    """Get team name from team ID"""
    if not team_id:
        return ""
    team = session.query(TeamModel).filter_by(id=team_id).first()
    return team.normalized_team_name if team else ""


def calculate_performance_metrics(
    picks: List[PickModel],
    bets: Dict[int, BetModel],
    session: Session
) -> Dict[str, Any]:
    """Calculate overall performance metrics"""
    total_picks = len(picks)
    settled_picks = 0
    wins = 0
    losses = 0
    pushes = 0
    total_wagered = 0.0
    total_profit = 0.0
    total_expected_ev = 0.0
    
    # Performance by bet type
    bet_type_stats = defaultdict(lambda: {
        "picks": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "wagered": 0.0,
        "profit": 0.0,
        "expected_ev": 0.0
    })
    
    # Confidence tier analysis
    confidence_tiers = defaultdict(lambda: {
        "picks": 0,
        "wins": 0,
        "losses": 0,
        "wagered": 0.0,
        "profit": 0.0,
        "expected_ev": 0.0
    })
    
    for pick in picks:
        if not pick.id:
            continue
            
        bet = bets.get(pick.id)
        stake = pick.stake_amount or 0.0
        ev = pick.expected_value or 0.0
        
        total_wagered += stake
        total_expected_ev += ev * stake if stake > 0 else 0.0
        
        # Bet type stats
        bet_type = pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type)
        bet_type_stats[bet_type]["picks"] += 1
        bet_type_stats[bet_type]["wagered"] += stake
        bet_type_stats[bet_type]["expected_ev"] += ev * stake if stake > 0 else 0.0
        
        # Confidence tier (0-3: low, 4-6: med, 7-10: high)
        conf_score = pick.confidence_score or 5
        if conf_score <= 3:
            tier = "low"
        elif conf_score <= 6:
            tier = "med"
        else:
            tier = "high"
        
        confidence_tiers[tier]["picks"] += 1
        confidence_tiers[tier]["wagered"] += stake
        confidence_tiers[tier]["expected_ev"] += ev * stake if stake > 0 else 0.0
        
        if bet:
            if bet.result != BetResult.PENDING:
                settled_picks += 1
                
                if bet.result == BetResult.WIN:
                    wins += 1
                    total_profit += bet.profit_loss
                    bet_type_stats[bet_type]["wins"] += 1
                    bet_type_stats[bet_type]["profit"] += bet.profit_loss
                    confidence_tiers[tier]["wins"] += 1
                    confidence_tiers[tier]["profit"] += bet.profit_loss
                elif bet.result == BetResult.LOSS:
                    losses += 1
                    total_profit += bet.profit_loss
                    bet_type_stats[bet_type]["losses"] += 1
                    bet_type_stats[bet_type]["profit"] += bet.profit_loss
                    confidence_tiers[tier]["losses"] += 1
                    confidence_tiers[tier]["profit"] += bet.profit_loss
                elif bet.result == BetResult.PUSH:
                    pushes += 1
                    bet_type_stats[bet_type]["pushes"] += 1
                    confidence_tiers[tier]["pushes"] = confidence_tiers[tier].get("pushes", 0) + 1
    
    # Calculate rates
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0
    roi = (total_profit / total_wagered * 100) if total_wagered > 0 else 0.0
    ev_efficiency = (total_profit / total_expected_ev) if total_expected_ev != 0 else 0.0
    
    # Calculate bet type rates
    for bet_type in bet_type_stats:
        stats = bet_type_stats[bet_type]
        settled = stats["wins"] + stats["losses"]
        stats["win_rate"] = (stats["wins"] / settled * 100) if settled > 0 else 0.0
        stats["roi"] = (stats["profit"] / stats["wagered"] * 100) if stats["wagered"] > 0 else 0.0
    
    # Calculate confidence tier rates
    for tier in confidence_tiers:
        stats = confidence_tiers[tier]
        settled = stats["wins"] + stats["losses"]
        stats["win_rate"] = (stats["wins"] / settled * 100) if settled > 0 else 0.0
        stats["roi"] = (stats["profit"] / stats["wagered"] * 100) if stats["wagered"] > 0 else 0.0
    
    return {
        "total_picks": total_picks,
        "settled_picks": settled_picks,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(win_rate, 2),
        "roi": round(roi, 2),
        "total_wagered": round(total_wagered, 2),
        "total_profit": round(total_profit, 2),
        "total_expected_ev": round(total_expected_ev, 2),
        "ev_efficiency": round(ev_efficiency, 3),
        "bet_type_stats": dict(bet_type_stats),
        "confidence_tiers": dict(confidence_tiers)
    }


def calculate_model_accuracy(
    games: List[GameModel],
    predictions: Dict[int, PredictionModel],
    session: Session
) -> Dict[str, Any]:
    """Calculate model accuracy metrics"""
    spread_errors = []
    total_errors = []
    win_prob_calibration = defaultdict(lambda: {"predicted": 0, "actual": 0})
    
    for game in games:
        if not game.result or game.status != GameStatus.FINAL:
            continue
        
        pred = predictions.get(game.id)
        if not pred:
            continue
        
        result = game.result
        home_score = result.get("home_score") or result.get("homeScore")
        away_score = result.get("away_score") or result.get("awayScore")
        
        if home_score is None or away_score is None:
            continue
        
        try:
            home_score = float(home_score)
            away_score = float(away_score)
        except (ValueError, TypeError):
            continue
        
        actual_spread = home_score - away_score
        predicted_spread = pred.predicted_spread
        spread_error = abs(actual_spread - predicted_spread)
        spread_errors.append(spread_error)
        
        if pred.predicted_total:
            actual_total = home_score + away_score
            total_error = abs(actual_total - pred.predicted_total)
            total_errors.append(total_error)
        
        # Win probability calibration
        # Determine which team won
        if home_score > away_score:
            actual_winner = "team1"
            actual_prob = 1.0
        elif away_score > home_score:
            actual_winner = "team2"
            actual_prob = 0.0
        else:
            continue  # Skip ties
        
        # Bin predicted probability
        pred_prob = pred.win_probability_team1 if actual_winner == "team1" else pred.win_probability_team2
        bin_key = int(pred_prob * 10) / 10.0  # Round to 0.1 bins
        win_prob_calibration[bin_key]["predicted"] += 1
        if actual_prob > 0.5:
            win_prob_calibration[bin_key]["actual"] += 1
    
    spread_mae = sum(spread_errors) / len(spread_errors) if spread_errors else 0.0
    total_mae = sum(total_errors) / len(total_errors) if total_errors else 0.0
    
    # Calculate calibration
    calibration_data = {}
    for bin_key in sorted(win_prob_calibration.keys()):
        data = win_prob_calibration[bin_key]
        if data["predicted"] > 0:
            calibration_data[f"{bin_key:.1f}"] = {
                "predicted": data["predicted"],
                "actual_rate": round(data["actual"] / data["predicted"], 3)
            }
    
    return {
        "spread_mae": round(spread_mae, 2),
        "total_mae": round(total_mae, 2),
        "spread_samples": len(spread_errors),
        "total_samples": len(total_errors),
        "win_prob_calibration": calibration_data
    }


def build_key_games(
    games: List[GameModel],
    picks: List[PickModel],
    bets: Dict[int, BetModel],
    predictions: Dict[int, PredictionModel],
    research: Dict[int, GameInsightModel],
    session: Session,
    max_games: int = 50
) -> List[Dict[str, Any]]:
    """Build list of key games with picks and results"""
    key_games = []
    
    # Create game lookup
    game_lookup = {game.id: game for game in games}
    
    # Create picks by game
    picks_by_game = defaultdict(list)
    for pick in picks:
        if pick.game_id:
            picks_by_game[pick.game_id].append(pick)
    
    for game_id, game_picks in picks_by_game.items():
        game = game_lookup.get(game_id)
        if not game:
            continue
        
        # Get team names
        team1_name = get_team_name(session, game.team1_id)
        team2_name = get_team_name(session, game.team2_id)
        
        # Get prediction
        pred = predictions.get(game_id)
        
        # Get research
        insight = research.get(game_id)
        
        # Process picks for this game
        game_pick_data = []
        total_ev = 0.0
        total_pnl = 0.0
        
        for pick in game_picks:
            bet = bets.get(pick.id) if pick.id else None
            ev = pick.expected_value or 0.0
            stake = pick.stake_amount or 0.0
            total_ev += ev * stake if stake > 0 else 0.0
            
            if bet:
                total_pnl += bet.profit_loss
            
            # Truncate rationale
            rationale = (pick.rationale or "")[:200] if pick.rationale else ""
            
            pick_data = {
                "id": pick.id,
                "bt": pick.bet_type.value if hasattr(pick.bet_type, 'value') else str(pick.bet_type),
                "line": pick.line,
                "odds": pick.odds,
                "stake": round(stake, 2),
                "ev": round(ev, 3),
                "conf": pick.confidence_score or 5,
                "r": rationale
            }
            
            if bet:
                pick_data["result"] = bet.result.value if hasattr(bet.result, 'value') else str(bet.result)
                pick_data["pnl"] = round(bet.profit_loss, 2)
            
            game_pick_data.append(pick_data)
        
        # Get game result
        result_data = None
        if game.result and game.status == GameStatus.FINAL:
            result_data = {
                "hs": game.result.get("home_score") or game.result.get("homeScore"),
                "as": game.result.get("away_score") or game.result.get("awayScore")
            }
        
        game_data = {
            "gid": game_id,
            "d": game.date.isoformat(),
            "t": [team1_name, team2_name],
            "picks": game_pick_data,
            "ev": round(total_ev, 2),
            "pnl": round(total_pnl, 2)
        }
        
        if pred:
            game_data["pred"] = {
                "sp": round(pred.predicted_spread, 1),
                "tot": round(pred.predicted_total, 1) if pred.predicted_total else None,
                "wp1": round(pred.win_probability_team1, 3),
                "wp2": round(pred.win_probability_team2, 3),
                "ev": round(pred.ev_estimate, 3) if pred.ev_estimate else None,
                "conf": round(pred.confidence_score, 3)
            }
        
        if result_data:
            game_data["res"] = result_data
        
        if insight:
            # Include key research data (token-efficient)
            game_data["resrch"] = {
                "inj": len(insight.injuries) if insight.injuries else 0,
                "rival": insight.rivalry,
                "r1": insight.rest_days_team1,
                "r2": insight.rest_days_team2
            }
        
        key_games.append(game_data)
    
    # Sort by absolute P&L (biggest wins/losses first), then by EV
    key_games.sort(key=lambda x: (abs(x.get("pnl", 0)), abs(x.get("ev", 0))), reverse=True)
    
    return key_games[:max_games]


def generate_insights(
    performance: Dict[str, Any],
    model_accuracy: Dict[str, Any],
    key_games: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Generate insights and recommendations"""
    insights = {
        "best_conditions": [],
        "worst_conditions": [],
        "recommendations": []
    }
    
    # Analyze bet type performance
    bet_type_stats = performance.get("bet_type_stats", {})
    best_bet_type = None
    worst_bet_type = None
    best_wr = -1
    worst_wr = 101
    
    for bt, stats in bet_type_stats.items():
        wr = stats.get("win_rate", 0)
        if wr > best_wr and stats.get("picks", 0) >= 5:  # Minimum sample size
            best_wr = wr
            best_bet_type = bt
        if wr < worst_wr and stats.get("picks", 0) >= 5:
            worst_wr = wr
            worst_bet_type = bt
    
    if best_bet_type:
        insights["best_conditions"].append(f"{best_bet_type.upper()} bets: {best_wr:.1f}% win rate")
    if worst_bet_type:
        insights["worst_conditions"].append(f"{worst_bet_type.upper()} bets: {worst_wr:.1f}% win rate")
    
    # Analyze confidence tiers
    conf_tiers = performance.get("confidence_tiers", {})
    for tier, stats in conf_tiers.items():
        wr = stats.get("win_rate", 0)
        picks = stats.get("picks", 0)
        if picks >= 5:
            if wr >= 55:
                insights["best_conditions"].append(f"{tier.upper()} confidence ({tier}): {wr:.1f}% win rate")
            elif wr < 45:
                insights["worst_conditions"].append(f"{tier.upper()} confidence ({tier}): {wr:.1f}% win rate")
    
    # EV efficiency analysis
    ev_eff = performance.get("ev_efficiency", 0)
    if ev_eff < 0.5:
        insights["recommendations"].append("EV efficiency < 0.5: Model may be overestimating edge. Consider raising EV threshold.")
    elif ev_eff > 1.5:
        insights["recommendations"].append("EV efficiency > 1.5: Model may be underestimating edge. Consider lowering EV threshold.")
    
    # Model accuracy recommendations
    spread_mae = model_accuracy.get("spread_mae", 0)
    if spread_mae > 10:
        insights["recommendations"].append(f"Spread MAE ({spread_mae:.1f}) is high. Review model calibration.")
    
    # Win rate recommendations
    win_rate = performance.get("win_rate", 0)
    if win_rate < 50:
        insights["recommendations"].append(f"Win rate ({win_rate:.1f}%) below break-even. Review selection criteria.")
    
    # ROI recommendations
    roi = performance.get("roi", 0)
    if roi < -10:
        insights["recommendations"].append(f"Negative ROI ({roi:.1f}%). Consider pausing or adjusting strategy.")
    
    return insights


def build_report(
    start_date: date,
    end_date: date,
    games: List[GameModel],
    research: Dict[int, GameInsightModel],
    predictions: Dict[int, PredictionModel],
    picks: List[PickModel],
    bets: Dict[int, BetModel],
    session: Session
) -> Dict[str, Any]:
    """Build the complete report structure"""
    
    # Calculate metrics
    performance = calculate_performance_metrics(picks, bets, session)
    model_accuracy = calculate_model_accuracy(games, predictions, session)
    
    # Get game IDs
    game_ids = [g.id for g in games if g.id]
    
    # Count games with picks and results
    games_with_picks = len(set(pick.game_id for pick in picks if pick.game_id))
    games_with_results = len([g for g in games if g.result and g.status == GameStatus.FINAL])
    
    # Build key games
    key_games = build_key_games(games, picks, bets, predictions, research, session)
    
    # Generate insights
    insights = generate_insights(performance, model_accuracy, key_games)
    
    # Build report
    report = {
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "summary": {
            "tg": len(games),
            "gwp": games_with_picks,
            "gwr": games_with_results,
            "tp": performance["total_picks"],
            "sp": performance["settled_picks"],
            "wr": performance["win_rate"],
            "roi": performance["roi"],
            "tw": performance["total_wagered"],
            "tpr": performance["total_profit"],
            "ee": performance["ev_efficiency"]
        },
        "pbt": {  # performance_by_bet_type
            k: {
                "p": v["picks"],
                "w": v["wins"],
                "l": v["losses"],
                "wr": v["win_rate"],
                "roi": v["roi"]
            }
            for k, v in performance["bet_type_stats"].items()
        },
        "ma": {  # model_accuracy
            "smae": model_accuracy["spread_mae"],
            "tmae": model_accuracy["total_mae"],
            "ss": model_accuracy["spread_samples"],
            "ts": model_accuracy["total_samples"],
            "wpc": model_accuracy["win_prob_calibration"]
        },
        "eva": {  # ev_analysis
            "tev": performance["total_expected_ev"],
            "trv": performance["total_profit"],
            "ee": performance["ev_efficiency"],
            "ct": {  # by_confidence_tier
                k: {
                    "p": v["picks"],
                    "w": v["wins"],
                    "l": v["losses"],
                    "wr": v["win_rate"],
                    "roi": v["roi"]
                }
                for k, v in performance["confidence_tiers"].items()
            }
        },
        "kg": key_games,  # key_games
        "ins": insights  # insights
    }
    
    return report


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Generate LLM-optimized performance report"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file path (default: data/reports/llm_performance_report_START_to_END.json)"
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print report to stdout instead of file"
    )
    
    args = parser.parse_args()
    
    # Parse dates
    try:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date format: {e}")
        return 1
    
    if start_date > end_date:
        logger.error("Start date must be before end date")
        return 1
    
    logger.info(f"Generating performance report for {start_date} to {end_date}")
    
    # Initialize database
    db = Database()
    session = db.get_session()
    
    try:
        # Query data
        logger.info("Querying games...")
        games = query_games(session, start_date, end_date)
        game_ids = [g.id for g in games if g.id]
        logger.info(f"Found {len(games)} games")
        
        logger.info("Querying research...")
        research = query_research(session, game_ids)
        logger.info(f"Found {len(research)} research insights")
        
        logger.info("Querying predictions...")
        predictions = query_predictions(session, game_ids, start_date, end_date)
        logger.info(f"Found {len(predictions)} predictions")
        
        logger.info("Querying picks...")
        picks = query_picks(session, start_date, end_date)
        pick_ids = [p.id for p in picks if p.id]
        logger.info(f"Found {len(picks)} picks")
        
        logger.info("Querying bets...")
        bets = query_bets(session, pick_ids)
        logger.info(f"Found {len(bets)} bets")
        
        # Build report
        logger.info("Building report...")
        report = build_report(
            start_date, end_date, games, research, predictions, picks, bets, session
        )
        
        # Output
        if args.stdout:
            print(json.dumps(report, indent=2))
        else:
            if args.output:
                output_path = Path(args.output)
            else:
                output_path = Path("data/reports") / f"llm_performance_report_{start_date}_{end_date}.json"
            
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w') as f:
                json.dump(report, f, indent=2)
            
            logger.info(f"Report saved to {output_path}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)
        return 1
    finally:
        session.close()
        db.close()


if __name__ == "__main__":
    exit(main())

