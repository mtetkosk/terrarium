"""Deterministic modeling calculations for the Modeler agent."""

from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt, exp
from typing import Dict, Any, List, Optional, Tuple


# Constants tuned to mirror prior prompt-driven behavior
EFF_BASELINE = 109.0
PACE_MIN, PACE_MAX = 62.0, 78.0
MARGIN_SD = 11.0  # standard deviation for margin-based probabilities (used for spread edges)
TOTAL_SD = 15.0   # standard deviation for total probabilities
WIN_PROB_SCALE = 7.5  # Scale factor for sigmoid win probability: 1/(1+exp(-margin/7.5))


def _norm_cdf(x: float) -> float:
    """Standard normal CDF. Used for market edge calculations (spread/total probabilities)."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def calculate_pace(
    away_adjt: float,
    home_adjt: float,
    away_pace_trend: Optional[str] = None,
    home_pace_trend: Optional[str] = None,
) -> Tuple[float, float, float]:
    """
    Pace suppression model: (Slower * 0.65) + (Faster * 0.35) with trend adjustments.

    Returns:
        base_pace, trend_adjustment, final_pace
    """
    slower = min(away_adjt, home_adjt)
    faster = max(away_adjt, home_adjt)
    base_pace = (slower * 0.65) + (faster * 0.35)

    trend_adj = 0.0
    if away_pace_trend and away_pace_trend.lower() == "faster":
        trend_adj += 0.8
    elif away_pace_trend and away_pace_trend.lower() == "slower":
        trend_adj -= 0.8

    if home_pace_trend and home_pace_trend.lower() == "faster":
        trend_adj += 0.8
    elif home_pace_trend and home_pace_trend.lower() == "slower":
        trend_adj -= 0.8

    final_pace = max(PACE_MIN, min(PACE_MAX, base_pace + trend_adj))
    return base_pace, trend_adj, final_pace


def calculate_points_per_100(adj_o: float, opp_adj_d: float, eff_baseline: float = EFF_BASELINE) -> float:
    """Multiplicative efficiency formula."""
    return (adj_o * opp_adj_d) / eff_baseline


def calculate_raw_scores(away_pts_100: float, home_pts_100: float, pace: float) -> Tuple[float, float]:
    """Raw scores from points per 100 and pace."""
    raw_away = (away_pts_100 / 100.0) * pace
    raw_home = (home_pts_100 / 100.0) * pace
    return raw_away, raw_home


def apply_margin_dampening(margin: float, threshold: float = 18.0, factor: float = 0.4) -> Tuple[float, bool]:
    """
    Anti-blowout bias: dampen margins greater than threshold.

    Returns:
        dampened_margin, applied_flag
    """
    if abs(margin) <= threshold:
        return margin, False
    excess = abs(margin) - threshold
    dampened = threshold + (excess * factor)
    return dampened * (1 if margin >= 0 else -1), True


def calibrate_total(raw_total: float, market_total: float, pace: float = 68.0) -> Tuple[float, float]:
    """
    Conditional regression toward market total with pace-awareness.
    
    Updated calibration v3 (2026-01-13) based on performance analysis:
    - Total MAE of 14.55 indicates severe under-prediction, especially in high-pace games
    - High totals (>165) now use minimal regression + over-adjustment to combat under-bias
    - Pace > 72 adds additional scoring adjustment for "track meet" games
    
    Returns:
        calibrated_total, regression_percentage
    """
    total_diff = raw_total - market_total
    
    # Conditional regression based on total size:
    # Very high totals (>165): Market is usually right about high-scoring games
    # - Use 15% regression + 3pt over-adjustment to combat systematic under-prediction
    if raw_total > 165.0:
        regression = 0.15  # Minimal regression - trust the model's high total signal
        over_adj = 3.0  # Add points to combat under-prediction bias
    elif raw_total > 155.0:
        regression = 0.20  # Reduced from 0.275 to address continued under-bias
        over_adj = 1.5  # Slight over-adjustment for high totals
    elif 145.0 <= raw_total <= 155.0:
        regression = 0.15  # Reduced from 0.175
        over_adj = 0.0
    elif 140.0 <= raw_total < 145.0:
        regression = 0.20
        over_adj = 0.0
    else:
        # Low totals - regress more toward market
        regression = 0.35
        over_adj = 0.0
    
    # High-pace games (>72 possessions) tend to score more than models predict
    # Add additional scoring adjustment for "track meet" matchups
    pace_adj = 0.0
    if pace > 74.0:
        pace_adj = 2.5  # Very high pace = more scoring
    elif pace > 72.0:
        pace_adj = 1.5  # High pace adjustment
    elif pace > 70.0:
        pace_adj = 0.5  # Slight above-average pace adjustment
    
    calibrated = raw_total - (regression * total_diff) + over_adj + pace_adj
    return calibrated, regression


def apply_garbage_time_adjustment(total: float, margin: float, threshold: float = 22.0) -> Tuple[float, bool]:
    """
    Garbage time adjustment v2: Reduced from -6.0 to -4.0 based on performance data.
    
    Analysis shows:
    - Model under-predicts totals systematically, especially in blowouts
    - Blowout games (BYU vs ASU: predicted 159.5, actual 180) often go OVER
    - Garbage time scoring is real but was being over-penalized
    
    Changes:
    - Raised threshold from 20 to 22 (only apply to true blowouts)
    - Reduced adjustment from -6.0 to -4.0
    """
    if abs(margin) > threshold:
        return total - 4.0, True
    return total, False


def calculate_final_scores(calibrated_total: float, margin: float) -> Tuple[float, float]:
    """Derive final home/away scores from total and margin."""
    home_score = (calibrated_total / 2.0) + (margin / 2.0)
    away_score = (calibrated_total / 2.0) - (margin / 2.0)
    return round(away_score, 1), round(home_score, 1)


def calculate_win_probability(margin: float, scale: float = WIN_PROB_SCALE) -> Tuple[float, float]:
    """
    Convert margin to win probabilities using sigmoid/logistic function.
    Matches agentic modeler: p_home = 1/(1+exp(-margin/7.5))
    """
    home_prob = 1.0 / (1.0 + exp(-margin / scale))
    away_prob = 1.0 - home_prob
    return max(0.0, min(1.0, away_prob)), max(0.0, min(1.0, home_prob))


def calculate_hca_adjustment(is_neutral_site: bool) -> float:
    """
    Calculate home court advantage margin adjustment.
    Matches agentic modeler: ~3.2-3.5 points (using 3.2 as seen in examples).
    Returns 0 for neutral site games.
    """
    if is_neutral_site:
        return 0.0
    return 3.2  # Home court advantage (matches agentic modeler output)


# Injury adjustment removed - injuries are not reliably detected by researcher


def calculate_mismatch_adjustment(stats: "GameStats", away_conference: Optional[str], home_conference: Optional[str]) -> float:
    """
    Calculate conference quality mismatch adjustment.
    Matches agentic modeler: +5.0 points for power conference vs mid/low-major.
    
    Power conferences: ACC, SEC, Big Ten, Big 12, Pac-12, Big East
    Mid-major examples: AAC, A-10, Mountain West, WCC
    Low-major: Summit, MEAC, SWAC, etc.
    
    Returns positive value favoring home team when home is power conference and away is not,
    or vice versa (negative favors away).
    """
    if not away_conference or not home_conference:
        return 0.0
    
    # Power conferences (matches agentic modeler logic)
    power_conferences = {
        "ACC", "SEC", "B10", "BIG TEN", "BIG 10", "B12", "BIG 12", 
        "PAC-12", "PAC12", "PAC", "BE", "BIG EAST"
    }
    
    # Conference abbreviations and variations
    conf_map = {
        "B10": "BIG TEN", "BIG 10": "BIG TEN",
        "B12": "BIG 12", "BIG12": "BIG 12",
        "PAC-12": "PAC12", "PAC": "PAC12",
        "BE": "BIG EAST", "BIGEAST": "BIG EAST",
        "A10": "A-10", "A-10": "A-10", "ATLANTIC 10": "A-10",
        "MWC": "MOUNTAIN WEST", "MW": "MOUNTAIN WEST",
    }
    
    # Normalize conference names
    away_conf_upper = away_conference.upper().strip()
    home_conf_upper = home_conference.upper().strip()
    
    # Apply mapping
    away_conf_upper = conf_map.get(away_conf_upper, away_conf_upper)
    home_conf_upper = conf_map.get(home_conf_upper, home_conf_upper)
    
    # Check if power conferences
    away_is_power = away_conf_upper in power_conferences or any(pc in away_conf_upper for pc in power_conferences)
    home_is_power = home_conf_upper in power_conferences or any(pc in home_conf_upper for pc in power_conferences)
    
    # Apply mismatch: +5.0 if home is power and away is not, -5.0 if away is power and home is not
    # Both power or both non-power = 0.0
    if home_is_power and not away_is_power:
        return 5.0  # Home team advantage from conference quality
    elif away_is_power and not home_is_power:
        return -5.0  # Away team advantage from conference quality
    
    return 0.0  # No mismatch (both power or both non-power)


def calculate_confidence(has_adv_stats: bool, edge_magnitude: float, is_blowout: bool = False) -> float:
    """
    Determine confidence based on data quality, edge size, and blowout status.
    Matches agentic modeler: blowout tier caps at 0.60.
    """
    if not has_adv_stats:
        return 0.0
    
    # Blowout tier caps at 0.60 (per agentic modeler output)
    if is_blowout:
        return 0.60
    
    # Standard tier: base confidence with edge magnitude lift
    base = 0.45 + min(edge_magnitude, 12.0) / 40.0  # modest lift from edge size
    
    # Adjust for large market/model gaps (reduces confidence)
    if edge_magnitude > 6.0:
        base -= 0.05  # Slight reduction for large discrepancies
    
    return max(0.3, min(0.9, base))


def implied_probability(odds: float) -> float:
    """Convert American odds to implied probability."""
    if odds == 0:
        return 0.0
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return (-odds) / ((-odds) + 100.0)


def calculate_market_edges(predicted: Dict[str, Any], betting_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Calculate edges for spread, total, and moneyline markets.

    predicted structure expects:
        - margin
        - total
        - win_probs: {'away': prob, 'home': prob}
    """
    margin = predicted.get("margin")
    total = predicted.get("total")
    win_probs = predicted.get("win_probs", {})
    away_win_prob = win_probs.get("away", 0.5)
    home_win_prob = win_probs.get("home", 0.5)

    edges: List[Dict[str, Any]] = []
    if not betting_lines:
        return edges

    # Helper for spread probability: probability favorite covers
    def spread_cover_prob(line: float, is_home: bool) -> float:
        # Convert market line into expected margin threshold
        effective_line = -line if is_home else line  # home line negative means home favored
        # Probability that (model_margin - line) > 0
        return 1.0 - _norm_cdf((effective_line - margin) / MARGIN_SD)

    # Helper for total probability: probability of over
    def total_over_prob(line: float) -> float:
        return 1.0 - _norm_cdf((line - total) / TOTAL_SD)

    for line in betting_lines:
        bet_type = line.get("bet_type")
        line_value = line.get("line")
        odds = line.get("odds", -110)
        team = (line.get("team") or "").lower()

        if bet_type == "spread" and line_value is not None:
            is_home = False
            if team and "home" in team:
                is_home = True
            # Fallback: infer from sign (negative implies favorite; assume favorite is home if sign <0)
            prob_cover = spread_cover_prob(line_value, is_home=is_home)
            model_prob = max(0.0, min(1.0, prob_cover))
            imp_prob = implied_probability(odds)
            edges.append(
                {
                    "market_type": "SPREAD_HOME" if is_home else "SPREAD_AWAY",
                    "market_line": f"{line_value}",
                    "model_estimated_probability": model_prob,
                    "implied_probability": imp_prob,
                    "edge": model_prob - imp_prob,
                    "edge_confidence": predicted.get("confidence", 0.3),
                }
            )

        elif bet_type == "total" and line_value is not None:
            over_prob = total_over_prob(line_value)
            under_prob = 1.0 - over_prob
            imp_prob = implied_probability(odds)
            edges.extend(
                [
                    {
                        "market_type": "TOTAL_OVER",
                        "market_line": f"{line_value}",
                        "model_estimated_probability": max(0.0, min(1.0, over_prob)),
                        "implied_probability": imp_prob,
                        "edge": over_prob - imp_prob,
                        "edge_confidence": predicted.get("confidence", 0.3),
                    },
                    {
                        "market_type": "TOTAL_UNDER",
                        "market_line": f"{line_value}",
                        "model_estimated_probability": max(0.0, min(1.0, under_prob)),
                        "implied_probability": imp_prob,
                        "edge": under_prob - imp_prob,
                        "edge_confidence": predicted.get("confidence", 0.3),
                    },
                ]
            )

        elif bet_type == "moneyline":
            if team and "home" in team:
                model_prob = home_win_prob
                market_type = "MONEYLINE_HOME"
            else:
                model_prob = away_win_prob
                market_type = "MONEYLINE_AWAY"
            imp_prob = implied_probability(odds)
            edges.append(
                {
                    "market_type": market_type,
                    "market_line": "0",
                    "model_estimated_probability": model_prob,
                    "implied_probability": imp_prob,
                    "edge": model_prob - imp_prob,
                    "edge_confidence": predicted.get("confidence", 0.3),
                }
            )

    return edges


@dataclass
class TeamAdvancedStats:
    adjo: float
    adjd: float
    adjt: float
    pace_trend: Optional[str] = None  # "faster" | "slower" | None
    conference: Optional[str] = None  # Conference name/abbreviation


@dataclass
class GameStats:
    away: TeamAdvancedStats
    home: TeamAdvancedStats
    market_total: Optional[float] = None
    market_spread_home: Optional[float] = None  # spread line expressed as home - pts
    is_neutral_site: bool = False  # True if game is at neutral site
    is_rivalry: bool = False  # True if game is a rivalry matchup


def calculate_game_model(
    game_data: Dict[str, Any],
    stats: GameStats,
    betting_lines: List[Dict[str, Any]],
    has_adv_stats: bool = True,
) -> Dict[str, Any]:
    """Run deterministic modeling for a single game."""
    away_team = game_data.get("teams", {}).get("away")
    home_team = game_data.get("teams", {}).get("home")
    away_id = game_data.get("teams", {}).get("away_id")
    home_id = game_data.get("teams", {}).get("home_id")
    game_id = game_data.get("game_id")

    base_pace, trend_adj, final_pace = calculate_pace(
        stats.away.adjt,
        stats.home.adjt,
        stats.away.pace_trend,
        stats.home.pace_trend,
    )

    away_pts_100 = calculate_points_per_100(stats.away.adjo, stats.home.adjd)
    home_pts_100 = calculate_points_per_100(stats.home.adjo, stats.away.adjd)
    
    # Tempo-Efficiency Multiplier v2: Tiered defense penalty for high-pace games
    # High-tempo games cause non-linear defensive breakdown due to fatigue
    # This addresses systematic under-prediction of totals in fast-paced games
    tempo_multiplier = 1.0
    if final_pace > 74.0:
        # Very high pace (75+ possessions): significant defensive breakdown
        tempo_multiplier = 1.05
    elif final_pace > 72.0:
        # High pace (72-74 possessions): moderate defensive breakdown  
        tempo_multiplier = 1.03
    elif final_pace > 70.0:
        # Above-average pace (70-72 possessions): slight defensive penalty
        tempo_multiplier = 1.015
    
    if tempo_multiplier > 1.0:
        away_pts_100 *= tempo_multiplier
        home_pts_100 *= tempo_multiplier
    
    raw_away, raw_home = calculate_raw_scores(away_pts_100, home_pts_100, final_pace)

    # Apply margin adjustments per MODELER_PROMPT v5.11:
    # raw_margin = (raw_home - raw_away) + (hca_margin_adj + mismatch_margin_adj)
    # Note: injury adjustment removed - injuries not reliably detected
    base_margin = raw_home - raw_away
    hca_adj = calculate_hca_adjustment(stats.is_neutral_site)
    mismatch_adj = calculate_mismatch_adjustment(stats, stats.away.conference, stats.home.conference)
    
    # Conference Grudge Modifier v2: Conference games/rivalries are grittier but often higher scoring
    # Data shows model under-predicts conference game totals by ~3-5 points on average
    # If Rivalry OR Conference game: reduce HCA by 1.0, increase total based on tempo
    is_conference_game = False
    if stats.away.conference and stats.home.conference:
        # Check if conferences match (ignoring case/spaces)
        if stats.away.conference.lower().strip() == stats.home.conference.lower().strip():
            is_conference_game = True
            
    grudge_total_adj = 0.0
    if is_conference_game or stats.is_rivalry:
        hca_adj -= 1.0
        # Conference games have more late-game fouling and tighter intensity
        # Higher pace conference games see even more scoring inflation
        if final_pace > 72.0:
            grudge_total_adj = 4.0  # High-pace conference game = significant scoring boost
        elif final_pace > 68.0:
            grudge_total_adj = 3.0  # Average-pace conference game
        else:
            grudge_total_adj = 2.0  # Slow-pace conference game
        
    raw_margin = base_margin + hca_adj + mismatch_adj
    
    dampened_margin, damp_applied = apply_margin_dampening(raw_margin)

    raw_total = raw_home + raw_away + grudge_total_adj
    market_total = stats.market_total if stats.market_total is not None else raw_total
    calibrated_total, regression_pct = calibrate_total(raw_total, market_total, final_pace)
    calibrated_total, garbage_time_applied = apply_garbage_time_adjustment(calibrated_total, dampened_margin)

    away_score, home_score = calculate_final_scores(calibrated_total, dampened_margin)
    margin = home_score - away_score
    total = away_score + home_score

    away_prob, home_prob = calculate_win_probability(margin)
    # Edge magnitude for confidence: difference vs market spread/total when available
    spread_diff = 0.0
    if stats.market_spread_home is not None:
        spread_diff = abs(margin - (-stats.market_spread_home))  # market spread given as home -pts
    total_diff = abs(total - market_total) if market_total is not None else 0.0
    edge_mag = max(spread_diff, total_diff)
    
    # Step 9: DISCREPANCY SHRINKAGE - Variable shrink factors based on edge_mag
    # Matches agentic modeler: 50% shrink when edge_mag > 8, 25% when > 6
    discrepancy_shrinkage_applied = False
    shrink_factor = 1.0  # No shrinkage by default
    
    if edge_mag > 8.0:
        shrink_factor = 0.50  # Shrink by 50% (keep 50% of distance from 0.5)
        discrepancy_shrinkage_applied = True
    elif edge_mag > 6.0:
        shrink_factor = 0.75  # Shrink by 25% (keep 75% of distance from 0.5)
        discrepancy_shrinkage_applied = True
    
    if discrepancy_shrinkage_applied:
        # Shrink probabilities toward 0.5 (neutral)
        # home_prob_adjusted = 0.5 + (home_prob - 0.5) * shrink_factor
        home_prob_adjusted = 0.5 + (home_prob - 0.5) * shrink_factor
        away_prob_adjusted = 1.0 - home_prob_adjusted
        away_prob = max(0.0, min(1.0, away_prob_adjusted))
        home_prob = max(0.0, min(1.0, home_prob_adjusted))
    
    # Round margin and probabilities to 2 decimal places
    margin = round(margin, 2)
    away_prob = round(away_prob, 2)
    home_prob = round(home_prob, 2)
    
    # Confidence calculation with blowout tier cap at 0.60
    confidence = calculate_confidence(has_adv_stats, edge_mag, is_blowout=(abs(dampened_margin) > 20))

    predictions = {
        "scores": {"away": away_score, "home": home_score},
        "margin": margin,
        "total": total,
        "win_probs": {"away": away_prob, "home": home_prob},
        "confidence": confidence,
    }

    market_edges = calculate_market_edges(predictions, betting_lines)

    model = {
        "game_id": str(game_id) if game_id is not None else None,
        "teams": {
            "away": away_team,
            "home": home_team,
            "away_id": away_id,
            "home_id": home_id,
        },
        "predictions": predictions,
        "predicted_score": {"away_score": away_score, "home_score": home_score},
        "market_edges": market_edges,
        "market_analysis": {
            "discrepancy_note": "",
            "edge_magnitude": edge_mag,
        },
        "ev_estimate": 0.0,
        "meta": {
            "base_pace": base_pace,
            "trend_adjustment": trend_adj,
            "final_pace": final_pace,
            "base_margin": base_margin,
            "hca_adjustment": hca_adj,
            "mismatch_adjustment": mismatch_adj,
            "raw_margin": raw_margin,
            "raw_total": raw_total,
            "calibrated_total": calibrated_total,
            "raw_away_score": raw_away,
            "raw_home_score": raw_home,
            "away_pts_per_100": away_pts_100,
            "home_pts_per_100": home_pts_100,
            "dampening_applied": damp_applied,
            "garbage_time_applied": garbage_time_applied,
            "market_total_used": market_total,
            "market_spread_home": stats.market_spread_home,
            "is_neutral_site": stats.is_neutral_site,
            "is_conference_game": is_conference_game,
            "is_rivalry": stats.is_rivalry,
            "tempo_multiplier": tempo_multiplier,
            "grudge_total_adj": grudge_total_adj,
            "discrepancy_shrinkage_applied": discrepancy_shrinkage_applied,
            "total_regression_pct": regression_pct,
            "edge_magnitude": edge_mag,
            "shrink_factor": shrink_factor if discrepancy_shrinkage_applied else 1.0,
        },
    }

    return model

