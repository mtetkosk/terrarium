"""
Analyze betting algorithm performance over time.

For each day from November 16, 2025 through January 2, 2026:
- Calculate MAE for spread and total predictions
- Calculate pick accuracy for high+medium confidence picks
- Test for normal distribution of residuals
- Generate visualizations and assessment
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional
import argparse
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from sqlalchemy import func, and_, or_

from src.data.storage import Database, GameModel, PredictionModel, PickModel, BetModel, TeamModel
from src.data.models import BetResult, BetType
from src.utils.config import config

# Set style for better-looking plots
sns.set_style("whitegrid")
plt.rcParams['figure.figsize'] = (14, 8)


def get_confidence_bucket(confidence: float, confidence_score: Optional[int] = None) -> str:
    """
    Map confidence to bucket: high, medium, or low.
    
    Uses confidence_score (1-10) if available, otherwise confidence (0.0-1.0).
    HIGH: >= 0.6 (or >= 6/10)
    MEDIUM: >= 0.4 (or >= 4/10)
    LOW: < 0.4 (or < 4/10)
    """
    if confidence_score is not None:
        # Use 1-10 scale
        if confidence_score >= 6:
            return "high"
        elif confidence_score >= 4:
            return "medium"
        else:
            return "low"
    else:
        # Use 0.0-1.0 scale
        if confidence >= 0.6:
            return "high"
        elif confidence >= 0.4:
            return "medium"
        else:
            return "low"


def calculate_actual_spread(game: GameModel, result: Dict) -> Optional[float]:
    """Calculate actual spread from game result."""
    if not result:
        return None
    
    home_score = result.get("home_score")
    away_score = result.get("away_score")
    
    if home_score is None or away_score is None:
        return None
    
    # Convert to float/int
    try:
        home_score = float(home_score) if home_score is not None else None
        away_score = float(away_score) if away_score is not None else None
    except (ValueError, TypeError):
        return None
    
    if home_score is None or away_score is None:
        return None
    
    # Need to determine which team is home/away
    # Check if we have team IDs in result
    home_team_id = result.get("home_team_id")
    away_team_id = result.get("away_team_id")
    
    if home_team_id and away_team_id:
        # Match team IDs to determine home/away
        if game.team1_id == home_team_id:
            # team1 is home, team2 is away
            actual_spread = home_score - away_score
        elif game.team1_id == away_team_id:
            # team1 is away, team2 is home
            actual_spread = away_score - home_score
        else:
            # Can't match, try team names as fallback
            home_team = result.get("home_team", "").lower()
            away_team = result.get("away_team", "").lower()
            team1_name = game.team1_ref.normalized_team_name.lower() if game.team1_ref else ""
            team2_name = game.team2_ref.normalized_team_name.lower() if game.team2_ref else ""
            
            if home_team in team1_name or team1_name in home_team:
                actual_spread = home_score - away_score
            elif home_team in team2_name or team2_name in home_team:
                actual_spread = away_score - home_score
            else:
                return None
    else:
        # Fallback to team name matching
        home_team = result.get("home_team", "").lower()
        away_team = result.get("away_team", "").lower()
        team1_name = game.team1_ref.normalized_team_name.lower() if game.team1_ref else ""
        team2_name = game.team2_ref.normalized_team_name.lower() if game.team2_ref else ""
        
        if home_team in team1_name or team1_name in home_team:
            actual_spread = home_score - away_score
        elif home_team in team2_name or team2_name in home_team:
            actual_spread = away_score - home_score
        else:
            return None
    
    return actual_spread


def calculate_actual_total(result: Dict) -> Optional[float]:
    """Calculate actual total from game result."""
    if not result:
        return None
    
    home_score = result.get("home_score")
    away_score = result.get("away_score")
    
    if home_score is None or away_score is None:
        return None
    
    # Convert to float/int
    try:
        home_score = float(home_score) if home_score is not None else None
        away_score = float(away_score) if away_score is not None else None
    except (ValueError, TypeError):
        return None
    
    if home_score is None or away_score is None:
        return None
    
    return home_score + away_score


def analyze_daily_performance(target_date: date, db: Database) -> Dict:
    """Analyze performance for a single day."""
    session = db.get_session()
    
    try:
        # Get games for this date
        games = session.query(GameModel).filter(
            GameModel.date == target_date
        ).all()
        
        if not games:
            return {
                'date': target_date,
                'spread_mae': None,
                'total_mae': None,
                'pick_accuracy': None,
                'spread_residuals': [],
                'total_residuals': [],
                'num_predictions': 0,
                'num_picks': 0,
                'num_high_medium_picks': 0,
                'num_correct_picks': 0
            }
        
        game_ids = [g.id for g in games]
        
        # Get predictions for these games
        predictions = session.query(PredictionModel).filter(
            PredictionModel.game_id.in_(game_ids),
            PredictionModel.prediction_date == target_date
        ).all()
        
        # Get picks for these games
        picks = session.query(PickModel).filter(
            PickModel.game_id.in_(game_ids),
            or_(
                PickModel.pick_date == target_date,
                func.date(PickModel.created_at) == target_date
            )
        ).all()
        
        # Get bets for these picks
        pick_ids = [p.id for p in picks if p.id]
        bets = {}
        if pick_ids:
            bet_list = session.query(BetModel).filter(
                BetModel.pick_id.in_(pick_ids)
            ).all()
            bets = {bet.pick_id: bet for bet in bet_list}
        
        # Create game lookup
        games_dict = {g.id: g for g in games}
        
        # Calculate spread and total MAE
        spread_errors = []
        spread_residuals = []
        total_errors = []
        total_residuals = []
        
        for pred in predictions:
            game = games_dict.get(pred.game_id)
            if not game or not game.result:
                continue
            
            result = game.result if isinstance(game.result, dict) else json.loads(game.result) if isinstance(game.result, str) else {}
            
            # Calculate actual spread
            actual_spread = calculate_actual_spread(game, result)
            if actual_spread is not None and pred.predicted_spread is not None:
                spread_error = abs(pred.predicted_spread - actual_spread)
                spread_residual = pred.predicted_spread - actual_spread
                spread_errors.append(spread_error)
                spread_residuals.append(spread_residual)
            
            # Calculate actual total
            actual_total = calculate_actual_total(result)
            if actual_total is not None and pred.predicted_total is not None:
                total_error = abs(pred.predicted_total - actual_total)
                total_residual = pred.predicted_total - actual_total
                total_errors.append(total_error)
                total_residuals.append(total_residual)
        
        spread_mae = np.mean(spread_errors) if spread_errors else None
        total_mae = np.mean(total_errors) if total_errors else None
        
        # Calculate pick accuracy for high+medium confidence picks
        high_medium_picks = []
        correct_picks = 0
        
        for pick in picks:
            # Determine confidence bucket
            conf_bucket = get_confidence_bucket(
                pick.confidence if pick.confidence else 0.0,
                pick.confidence_score
            )
            
            # Only count high and medium confidence picks
            if conf_bucket in ['high', 'medium']:
                high_medium_picks.append(pick)
                bet = bets.get(pick.id)
                if bet and bet.result == BetResult.WIN:
                    correct_picks += 1
        
        pick_accuracy = (correct_picks / len(high_medium_picks) * 100) if high_medium_picks else None
        
        return {
            'date': target_date,
            'spread_mae': spread_mae,
            'total_mae': total_mae,
            'pick_accuracy': pick_accuracy,
            'spread_residuals': spread_residuals,
            'total_residuals': total_residuals,
            'num_predictions': len(predictions),
            'num_picks': len(picks),
            'num_high_medium_picks': len(high_medium_picks),
            'num_correct_picks': correct_picks
        }
    
    finally:
        session.close()


def test_normality(residuals: List[float], name: str) -> Dict:
    """Test if residuals follow a normal distribution."""
    if len(residuals) < 3:
        return {
            'name': name,
            'n': len(residuals),
            'shapiro_statistic': None,
            'shapiro_pvalue': None,
            'is_normal': None,
            'mean': np.mean(residuals) if residuals else None,
            'std': np.std(residuals, ddof=1) if len(residuals) > 1 else None
        }
    
    # Shapiro-Wilk test (good for small to medium samples)
    shapiro_stat, shapiro_p = stats.shapiro(residuals)
    
    # Also try Kolmogorov-Smirnov test
    ks_stat, ks_p = stats.kstest(residuals, 'norm', args=(np.mean(residuals), np.std(residuals, ddof=1)))
    
    # Consider normal if p-value > 0.05
    is_normal = shapiro_p > 0.05
    
    return {
        'name': name,
        'n': len(residuals),
        'shapiro_statistic': shapiro_stat,
        'shapiro_pvalue': shapiro_p,
        'ks_statistic': ks_stat,
        'ks_pvalue': ks_p,
        'is_normal': is_normal,
        'mean': np.mean(residuals),
        'std': np.std(residuals, ddof=1),
        'skewness': stats.skew(residuals),
        'kurtosis': stats.kurtosis(residuals)
    }


def main(start_date: Optional[date] = None, end_date: Optional[date] = None, days_back: Optional[int] = None):
    """Main analysis function.
    
    Args:
        start_date: Start date for analysis (default: 2025-11-19)
        end_date: End date for analysis (default: today)
        days_back: Number of days to look back if start_date not provided (default: None, uses 2025-11-19)
    """
    print("=" * 80)
    print("BETTING ALGORITHM PERFORMANCE ANALYSIS")
    print("=" * 80)
    
    # Initialize database
    db = Database()
    
    # Date range
    if end_date is None:
        end_date = date.today()
    
    if start_date is None:
        if days_back is not None:
            start_date = end_date - timedelta(days=days_back)
        else:
            # Default: start from 2025-11-19
            start_date = date(2025, 11, 19)
    
    print(f"Date Range: {start_date} - {end_date}")
    print()
    
    # Collect daily data
    daily_data = []
    all_spread_residuals = []
    all_total_residuals = []
    
    current_date = start_date
    while current_date <= end_date:
        print(f"Analyzing {current_date}...", end=" ")
        daily_result = analyze_daily_performance(current_date, db)
        daily_data.append(daily_result)
        
        if daily_result['spread_residuals']:
            all_spread_residuals.extend(daily_result['spread_residuals'])
        if daily_result['total_residuals']:
            all_total_residuals.extend(daily_result['total_residuals'])
        
        print(f"Predictions: {daily_result['num_predictions']}, "
              f"Picks: {daily_result['num_picks']} "
              f"(High+Med: {daily_result['num_high_medium_picks']})")
        
        current_date += timedelta(days=1)
    
    print()
    print("=" * 80)
    print("CALCULATING STATISTICS")
    print("=" * 80)
    
    # Convert to DataFrame for easier analysis
    df = pd.DataFrame(daily_data)
    
    # Filter out days with no data
    df_valid = df[df['num_predictions'] > 0].copy()
    
    print(f"\nDays with data: {len(df_valid)} out of {len(df)}")
    print(f"Total predictions analyzed: {df_valid['num_predictions'].sum()}")
    print(f"Total picks analyzed: {df_valid['num_picks'].sum()}")
    print(f"Total high+medium picks: {df_valid['num_high_medium_picks'].sum()}")
    
    # Overall statistics
    print("\n" + "-" * 80)
    print("OVERALL PERFORMANCE METRICS")
    print("-" * 80)
    
    overall_spread_mae = df_valid['spread_mae'].mean()
    overall_total_mae = df_valid['total_mae'].mean()
    overall_pick_accuracy = df_valid['pick_accuracy'].mean()
    
    print(f"Average Spread MAE: {overall_spread_mae:.2f} points")
    print(f"Average Total MAE: {overall_total_mae:.2f} points")
    print(f"Average Pick Accuracy (High+Medium): {overall_pick_accuracy:.2f}%")
    
    # Test for normal distribution
    print("\n" + "-" * 80)
    print("NORMALITY TESTS FOR RESIDUALS")
    print("-" * 80)
    
    spread_normality = test_normality(all_spread_residuals, "Spread Residuals")
    total_normality = test_normality(all_total_residuals, "Total Residuals")
    
    for test_result in [spread_normality, total_normality]:
        print(f"\n{test_result['name']}:")
        print(f"  Sample size: {test_result['n']}")
        if test_result['n'] >= 3:
            print(f"  Mean: {test_result['mean']:.3f}")
            print(f"  Std Dev: {test_result['std']:.3f}")
            print(f"  Skewness: {test_result['skewness']:.3f}")
            print(f"  Kurtosis: {test_result['kurtosis']:.3f}")
            print(f"  Shapiro-Wilk test:")
            print(f"    Statistic: {test_result['shapiro_statistic']:.4f}")
            print(f"    P-value: {test_result['shapiro_pvalue']:.4f}")
            print(f"    Normal distribution: {'YES' if test_result['is_normal'] else 'NO'} (p > 0.05)")
            print(f"  Kolmogorov-Smirnov test:")
            print(f"    Statistic: {test_result['ks_statistic']:.4f}")
            print(f"    P-value: {test_result['ks_pvalue']:.4f}")
        else:
            print(f"  Insufficient data for normality test")
    
    # Create visualizations
    print("\n" + "-" * 80)
    print("GENERATING VISUALIZATIONS")
    print("-" * 80)
    
    # Create output directory
    output_dir = Path("data/reports/performance_analysis")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. MAE over time
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    
    ax1 = axes[0]
    df_valid_mae = df_valid[df_valid['spread_mae'].notna()].copy()
    ax1.plot(df_valid_mae['date'], df_valid_mae['spread_mae'], marker='o', label='Spread MAE', linewidth=2)
    ax1.axhline(y=overall_spread_mae, color='r', linestyle='--', label=f'Average: {overall_spread_mae:.2f}')
    ax1.set_xlabel('Date', fontsize=12)
    ax1.set_ylabel('MAE (points)', fontsize=12)
    ax1.set_title('Spread Prediction MAE Over Time', fontsize=14, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.tick_params(axis='x', rotation=45)
    
    ax2 = axes[1]
    df_valid_total = df_valid[df_valid['total_mae'].notna()].copy()
    ax2.plot(df_valid_total['date'], df_valid_total['total_mae'], marker='o', label='Total MAE', color='green', linewidth=2)
    ax2.axhline(y=overall_total_mae, color='r', linestyle='--', label=f'Average: {overall_total_mae:.2f}')
    ax2.set_xlabel('Date', fontsize=12)
    ax2.set_ylabel('MAE (points)', fontsize=12)
    ax2.set_title('Total Prediction MAE Over Time', fontsize=14, fontweight='bold')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'mae_over_time.png', dpi=300, bbox_inches='tight')
    print(f"Saved: {output_dir / 'mae_over_time.png'}")
    
    # 2. Pick accuracy over time
    fig, ax = plt.subplots(figsize=(14, 6))
    df_valid_acc = df_valid[df_valid['pick_accuracy'].notna()].copy()
    ax.plot(df_valid_acc['date'], df_valid_acc['pick_accuracy'], marker='o', label='Pick Accuracy', linewidth=2, color='purple')
    ax.axhline(y=overall_pick_accuracy, color='r', linestyle='--', label=f'Average: {overall_pick_accuracy:.2f}%')
    ax.axhline(y=50, color='gray', linestyle=':', label='50% (baseline)', alpha=0.5)
    ax.set_xlabel('Date', fontsize=12)
    ax.set_ylabel('Accuracy (%)', fontsize=12)
    ax.set_title('Pick Accuracy Over Time (High+Medium Confidence Picks)', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.tick_params(axis='x', rotation=45)
    plt.tight_layout()
    plt.savefig(output_dir / 'pick_accuracy_over_time.png', dpi=300, bbox_inches='tight')
    print(f"Saved: {output_dir / 'pick_accuracy_over_time.png'}")
    
    # 3. Residual distributions
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Spread residuals
    if all_spread_residuals:
        ax1 = axes[0, 0]
        ax1.hist(all_spread_residuals, bins=30, edgecolor='black', alpha=0.7)
        ax1.axvline(x=0, color='r', linestyle='--', linewidth=2)
        ax1.set_xlabel('Residual (Predicted - Actual)', fontsize=11)
        ax1.set_ylabel('Frequency', fontsize=11)
        ax1.set_title('Spread Residuals Distribution', fontsize=12, fontweight='bold')
        ax1.grid(True, alpha=0.3)
        
        ax2 = axes[0, 1]
        stats.probplot(all_spread_residuals, dist="norm", plot=ax2)
        ax2.set_title('Spread Residuals Q-Q Plot', fontsize=12, fontweight='bold')
        ax2.grid(True, alpha=0.3)
    
    # Total residuals
    if all_total_residuals:
        ax3 = axes[1, 0]
        ax3.hist(all_total_residuals, bins=30, edgecolor='black', alpha=0.7, color='green')
        ax3.axvline(x=0, color='r', linestyle='--', linewidth=2)
        ax3.set_xlabel('Residual (Predicted - Actual)', fontsize=11)
        ax3.set_ylabel('Frequency', fontsize=11)
        ax3.set_title('Total Residuals Distribution', fontsize=12, fontweight='bold')
        ax3.grid(True, alpha=0.3)
        
        ax4 = axes[1, 1]
        stats.probplot(all_total_residuals, dist="norm", plot=ax4)
        ax4.set_title('Total Residuals Q-Q Plot', fontsize=12, fontweight='bold')
        ax4.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'residual_distributions.png', dpi=300, bbox_inches='tight')
    print(f"Saved: {output_dir / 'residual_distributions.png'}")
    
    # 4. Combined performance dashboard
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))
    
    # Spread MAE
    ax1 = axes[0]
    ax1.plot(df_valid_mae['date'], df_valid_mae['spread_mae'], marker='o', linewidth=2, label='Spread MAE')
    ax1.axhline(y=overall_spread_mae, color='r', linestyle='--', alpha=0.7)
    ax1.set_ylabel('MAE (points)', fontsize=11)
    ax1.set_title('Spread MAE', fontsize=12, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.tick_params(axis='x', rotation=45)
    
    # Total MAE
    ax2 = axes[1]
    ax2.plot(df_valid_total['date'], df_valid_total['total_mae'], marker='o', linewidth=2, color='green', label='Total MAE')
    ax2.axhline(y=overall_total_mae, color='r', linestyle='--', alpha=0.7)
    ax2.set_ylabel('MAE (points)', fontsize=11)
    ax2.set_title('Total MAE', fontsize=12, fontweight='bold')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.tick_params(axis='x', rotation=45)
    
    # Pick Accuracy
    ax3 = axes[2]
    ax3.plot(df_valid_acc['date'], df_valid_acc['pick_accuracy'], marker='o', linewidth=2, color='purple', label='Pick Accuracy')
    ax3.axhline(y=overall_pick_accuracy, color='r', linestyle='--', alpha=0.7)
    ax3.axhline(y=50, color='gray', linestyle=':', alpha=0.5)
    ax3.set_xlabel('Date', fontsize=11)
    ax3.set_ylabel('Accuracy (%)', fontsize=11)
    ax3.set_title('Pick Accuracy (High+Medium Confidence)', fontsize=12, fontweight='bold')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    plt.savefig(output_dir / 'performance_dashboard.png', dpi=300, bbox_inches='tight')
    print(f"Saved: {output_dir / 'performance_dashboard.png'}")
    
    # Generate assessment report
    print("\n" + "=" * 80)
    print("PERFORMANCE ASSESSMENT")
    print("=" * 80)
    
    report_lines = [
        "# BETTING ALGORITHM PERFORMANCE ASSESSMENT",
        f"Analysis Period: November 16, 2025 - January 2, 2026",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## EXECUTIVE SUMMARY",
        "",
        f"**Overall Performance:**",
        f"- Average Spread MAE: {overall_spread_mae:.2f} points",
        f"- Average Total MAE: {overall_total_mae:.2f} points",
        f"- Average Pick Accuracy (High+Medium Confidence): {overall_pick_accuracy:.2f}%",
        "",
        "## DETAILED METRICS",
        "",
        f"**Days Analyzed:** {len(df_valid)} out of {len(df)} total days",
        f"**Total Predictions:** {df_valid['num_predictions'].sum()}",
        f"**Total Picks:** {df_valid['num_picks'].sum()}",
        f"**High+Medium Confidence Picks:** {df_valid['num_high_medium_picks'].sum()}",
        "",
        "## RESIDUAL ANALYSIS",
        "",
        f"**Spread Residuals:**",
        f"- Sample Size: {spread_normality['n']}",
    ]
    
    # Add spread residual details
    if spread_normality['n'] >= 3:
        report_lines.extend([
            f"- Mean: {spread_normality['mean']:.3f}",
            f"- Standard Deviation: {spread_normality['std']:.3f}",
            f"- Skewness: {spread_normality['skewness']:.3f}",
            f"- Kurtosis: {spread_normality['kurtosis']:.3f}",
            f"- Shapiro-Wilk Test: p-value = {spread_normality['shapiro_pvalue']:.4f}",
            f"- Normal Distribution: {'YES' if spread_normality['is_normal'] else 'NO'}",
        ])
    else:
        report_lines.append("- Insufficient data for analysis")
    
    report_lines.extend([
        "",
        f"**Total Residuals:**",
        f"- Sample Size: {total_normality['n']}",
    ])
    
    # Add total residual details
    if total_normality['n'] >= 3:
        report_lines.extend([
            f"- Mean: {total_normality['mean']:.3f}",
            f"- Standard Deviation: {total_normality['std']:.3f}",
            f"- Skewness: {total_normality['skewness']:.3f}",
            f"- Kurtosis: {total_normality['kurtosis']:.3f}",
            f"- Shapiro-Wilk Test: p-value = {total_normality['shapiro_pvalue']:.4f}",
            f"- Normal Distribution: {'YES' if total_normality['is_normal'] else 'NO'}",
        ])
    else:
        report_lines.append("- Insufficient data for analysis")
    
    report_lines.extend([
        "",
        "## KEY FINDINGS",
        "",
    ])
    
    # Add findings based on results
    findings = []
    
    if overall_pick_accuracy > 50:
        findings.append(f"✅ Pick accuracy ({overall_pick_accuracy:.2f}%) exceeds baseline (50%), indicating positive edge")
    else:
        findings.append(f"⚠️ Pick accuracy ({overall_pick_accuracy:.2f}%) is below baseline (50%), needs improvement")
    
    if spread_normality.get('is_normal'):
        findings.append(f"✅ Spread residuals follow a normal distribution (p={spread_normality['shapiro_pvalue']:.4f})")
    elif spread_normality.get('n', 0) >= 3:
        findings.append(f"⚠️ Spread residuals do NOT follow a normal distribution (p={spread_normality['shapiro_pvalue']:.4f})")
    
    if total_normality.get('is_normal'):
        findings.append(f"✅ Total residuals follow a normal distribution (p={total_normality['shapiro_pvalue']:.4f})")
    elif total_normality.get('n', 0) >= 3:
        findings.append(f"⚠️ Total residuals do NOT follow a normal distribution (p={total_normality['shapiro_pvalue']:.4f})")
    
    if spread_normality.get('mean', 0) != 0:
        bias = spread_normality['mean']
        findings.append(f"{'✅' if abs(bias) < 1 else '⚠️'} Spread prediction bias: {bias:+.2f} points ({'unbiased' if abs(bias) < 1 else 'biased'})")
    
    if total_normality.get('mean', 0) != 0:
        bias = total_normality['mean']
        findings.append(f"{'✅' if abs(bias) < 2 else '⚠️'} Total prediction bias: {bias:+.2f} points ({'unbiased' if abs(bias) < 2 else 'biased'})")
    
    report_lines.extend([f"- {f}" for f in findings])
    report_lines.append("")
    report_lines.append("## RECOMMENDATIONS")
    report_lines.append("")
    
    recommendations = []
    if overall_pick_accuracy <= 50:
        recommendations.append("Focus on improving pick selection criteria, especially for high-confidence picks")
    if not spread_normality.get('is_normal') and spread_normality.get('n', 0) >= 3:
        recommendations.append("Investigate sources of non-normality in spread residuals (may indicate systematic bias)")
    if not total_normality.get('is_normal') and total_normality.get('n', 0) >= 3:
        recommendations.append("Investigate sources of non-normality in total residuals (may indicate systematic bias)")
    if abs(spread_normality.get('mean', 0)) > 1:
        recommendations.append("Address systematic bias in spread predictions")
    if abs(total_normality.get('mean', 0)) > 2:
        recommendations.append("Address systematic bias in total predictions")
    
    if not recommendations:
        recommendations.append("Algorithm performance is within acceptable parameters. Continue monitoring.")
    
    report_lines.extend([f"- {r}" for r in recommendations])
    
    # Write report
    report_path = output_dir / 'performance_assessment.md'
    with open(report_path, 'w') as f:
        f.write('\n'.join(report_lines))
    
    print(f"\nSaved: {report_path}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for finding in findings:
        print(f"  {finding}")
    
    print(f"\nAll visualizations and report saved to: {output_dir}")
    print("=" * 80)
    
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Analyze betting algorithm performance')
    parser.add_argument('--start-date', type=str, help='Start date (YYYY-MM-DD). Default: 2025-11-19')
    parser.add_argument('--end-date', type=str, help='End date (YYYY-MM-DD). Default: today')
    parser.add_argument('--days-back', type=int, help='Days to look back if start-date not provided (overrides default 2025-11-19)')
    
    args = parser.parse_args()
    
    start_date = None
    if args.start_date:
        start_date = date.fromisoformat(args.start_date)
    
    end_date = None
    if args.end_date:
        end_date = date.fromisoformat(args.end_date)
    
    main(start_date=start_date, end_date=end_date, days_back=args.days_back)

