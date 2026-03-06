"""Deterministic modeling calculations for the Modeler agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import erf, sqrt, exp
from typing import Any, Dict, List, Optional, Tuple


# Constants tuned to mirror prior prompt-driven behavior
EFF_BASELINE = 109.0
PACE_MIN, PACE_MAX = 62.0, 78.0
MARGIN_SD = 11.0  # standard deviation for margin-based probabilities (used for spread edges)
TOTAL_SD = 15.0   # standard deviation for total probabilities
WIN_PROB_SCALE = 7.5  # Scale factor for sigmoid win probability: 1/(1+exp(-margin/7.5))

# Power conferences (matches agentic modeler logic)
POWER_CONFERENCES = {
    "ACC", "SEC", "B10", "BIG TEN", "BIG 10", "B12", "BIG 12",
    "PAC-12", "PAC12", "PAC", "BE", "BIG EAST",
}
CONFERENCE_ABBREV_MAP = {
    "B10": "BIG TEN", "BIG 10": "BIG TEN",
    "B12": "BIG 12", "BIG12": "BIG 12",
    "PAC-12": "PAC12", "PAC": "PAC12",
    "BE": "BIG EAST", "BIGEAST": "BIG EAST",
    "A10": "A-10", "A-10": "A-10", "ATLANTIC 10": "A-10",
    "MWC": "MOUNTAIN WEST", "MW": "MOUNTAIN WEST",
}


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


def _is_power_conference(conference: str) -> bool:
    """Normalize conference name and check if it is a power conference."""
    if not conference:
        return False
    normalized = conference.upper().strip()
    normalized = CONFERENCE_ABBREV_MAP.get(normalized, normalized)
    return normalized in POWER_CONFERENCES or any(pc in normalized for pc in POWER_CONFERENCES)


def calculate_mismatch_adjustment(
    stats: "GameContext",
    away_conference: Optional[str] = None,
    home_conference: Optional[str] = None,
) -> float:
    """
    Calculate conference quality mismatch adjustment.
    Matches agentic modeler: +5.0 points for power conference vs mid/low-major.
    
    Power conferences: ACC, SEC, Big Ten, Big 12, Pac-12, Big East
    Mid-major examples: AAC, A-10, Mountain West, WCC
    Low-major: Summit, MEAC, SWAC, etc.
    
    Returns positive value favoring home team when home is power conference and away is not,
    or vice versa (negative favors away).
    """
    ac = away_conference if away_conference is not None else (stats.away.conference if hasattr(stats.away, "conference") else None)
    hc = home_conference if home_conference is not None else (stats.home.conference if hasattr(stats.home, "conference") else None)
    if not ac or not hc:
        return 0.0
    away_is_power = _is_power_conference(ac)
    home_is_power = _is_power_conference(hc)
    if home_is_power and not away_is_power:
        return 5.0
    if away_is_power and not home_is_power:
        return -5.0
    return 0.0


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


def _calculate_spread_edge(
    line_value: float,
    is_home: bool,
    margin: float,
    odds: float,
    confidence: float,
) -> Dict[str, Any]:
    """Compute spread edge: probability of covering vs implied probability."""
    effective_line = -line_value if is_home else line_value
    prob_cover = 1.0 - _norm_cdf((effective_line - margin) / MARGIN_SD)
    model_prob = max(0.0, min(1.0, prob_cover))
    imp_prob = implied_probability(odds)
    return {
        "market_type": "SPREAD_HOME" if is_home else "SPREAD_AWAY",
        "market_line": f"{line_value}",
        "model_estimated_probability": model_prob,
        "implied_probability": imp_prob,
        "edge": model_prob - imp_prob,
        "edge_confidence": confidence,
    }


def _calculate_total_edge(
    line_value: float,
    total: float,
    odds: float,
    confidence: float,
) -> List[Dict[str, Any]]:
    """Compute over/under edges for a total line."""
    over_prob = 1.0 - _norm_cdf((line_value - total) / TOTAL_SD)
    under_prob = 1.0 - over_prob
    imp_prob = implied_probability(odds)
    return [
        {
            "market_type": "TOTAL_OVER",
            "market_line": f"{line_value}",
            "model_estimated_probability": max(0.0, min(1.0, over_prob)),
            "implied_probability": imp_prob,
            "edge": over_prob - imp_prob,
            "edge_confidence": confidence,
        },
        {
            "market_type": "TOTAL_UNDER",
            "market_line": f"{line_value}",
            "model_estimated_probability": max(0.0, min(1.0, under_prob)),
            "implied_probability": imp_prob,
            "edge": under_prob - imp_prob,
            "edge_confidence": confidence,
        },
    ]


def _calculate_moneyline_edge(
    team: str,
    away_win_prob: float,
    home_win_prob: float,
    odds: float,
    confidence: float,
) -> Dict[str, Any]:
    """Compute moneyline edge for home or away side."""
    is_home = bool(team and "home" in team.lower())
    model_prob = home_win_prob if is_home else away_win_prob
    market_type = "MONEYLINE_HOME" if is_home else "MONEYLINE_AWAY"
    imp_prob = implied_probability(odds)
    return {
        "market_type": market_type,
        "market_line": "0",
        "model_estimated_probability": model_prob,
        "implied_probability": imp_prob,
        "edge": model_prob - imp_prob,
        "edge_confidence": confidence,
    }


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
    confidence = predicted.get("confidence", 0.3)

    edges: List[Dict[str, Any]] = []
    if not betting_lines:
        return edges

    for line in betting_lines:
        bet_type = line.get("bet_type")
        line_value = line.get("line")
        odds = line.get("odds", -110)
        team = (line.get("team") or "").lower()

        if bet_type == "spread" and line_value is not None:
            is_home = bool(team and "home" in team)
            edges.append(_calculate_spread_edge(line_value, is_home, margin, odds, confidence))
        elif bet_type == "total" and line_value is not None:
            edges.extend(_calculate_total_edge(line_value, total, odds, confidence))
        elif bet_type == "moneyline":
            edges.append(_calculate_moneyline_edge(team, away_win_prob, home_win_prob, odds, confidence))

    return edges


def calculate_tempo_multiplier(final_pace: float) -> float:
    """
    Tempo-Efficiency Multiplier v2: Tiered defense penalty for high-pace games.
    High-tempo games cause non-linear defensive breakdown due to fatigue.
    """
    if final_pace > 74.0:
        return 1.05
    if final_pace > 72.0:
        return 1.03
    if final_pace > 70.0:
        return 1.015
    return 1.0


def calculate_conference_grudge_adjustment(
    stats: "GameContext", final_pace: float
) -> Tuple[bool, float, float]:
    """
    Conference Grudge Modifier v2: detect conference game and return adjustments.
    Returns (is_conference_game, grudge_total_adj, hca_reduction).
    hca_reduction is 1.0 when conference/rivalry applies else 0.0.
    """
    is_conference_game = False
    if stats.away.conference and stats.home.conference:
        if stats.away.conference.lower().strip() == stats.home.conference.lower().strip():
            is_conference_game = True
    grudge_total_adj = 0.0
    hca_reduction = 0.0
    if is_conference_game or stats.is_rivalry:
        hca_reduction = 1.0
        if final_pace > 72.0:
            grudge_total_adj = 4.0
        elif final_pace > 68.0:
            grudge_total_adj = 3.0
        else:
            grudge_total_adj = 2.0
    return is_conference_game, grudge_total_adj, hca_reduction


def apply_discrepancy_shrinkage(
    away_prob: float,
    home_prob: float,
    edge_mag: float,
) -> Tuple[float, float, bool, float]:
    """
    Shrink win probabilities toward 0.5 when edge magnitude is large.
    Returns (away_prob, home_prob, applied, shrink_factor).
    """
    shrink_factor = 1.0
    if edge_mag > 8.0:
        shrink_factor = 0.50
    elif edge_mag > 6.0:
        shrink_factor = 0.75
    else:
        return away_prob, home_prob, False, 1.0
    home_prob_adjusted = 0.5 + (home_prob - 0.5) * shrink_factor
    away_prob_adjusted = 1.0 - home_prob_adjusted
    return (
        max(0.0, min(1.0, away_prob_adjusted)),
        max(0.0, min(1.0, home_prob_adjusted)),
        True,
        shrink_factor,
    )


def build_model_output(
    game_id: Any,
    away_team: Any,
    home_team: Any,
    away_id: Any,
    home_id: Any,
    predictions: Dict[str, Any],
    away_score: float,
    home_score: float,
    market_edges: List[Dict[str, Any]],
    edge_mag: float,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the final game model dictionary from computed values."""
    return {
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
        "meta": meta,
    }


@dataclass
class TeamContext:
    """Typed team context with name, id, and advanced stats. Replaces TeamAdvancedStats."""
    name: str
    team_id: Optional[Any]
    adjo: float
    adjd: float
    adjt: float
    pace_trend: Optional[str] = None  # "faster" | "slower" | None
    conference: Optional[str] = None

    @classmethod
    def from_dict(
        cls,
        name: str,
        team_id: Optional[Any],
        adv: Dict[str, Any],
        recent: Optional[Dict[str, Any]] = None,
    ) -> "TeamContext":
        """Extract team context from researcher adv dict. Handles key aliasing (adjo/adj_o/adj_offense etc.)."""
        if not isinstance(adv, dict):
            raise ValueError(f"adv must be a dict for {name}")
        adjo = adv.get("adjo") or adv.get("adj_o") or adv.get("adj_offense")
        adjd = adv.get("adjd") or adv.get("adj_d") or adv.get("adj_defense")
        adjt = adv.get("adjt") or adv.get("adj_t") or adv.get("adj_tempo")
        if any(v is None for v in (adjo, adjd, adjt)):
            raise ValueError(f"Missing required AdjO/AdjD/AdjT for {name}")
        pace_trend = (recent or {}).get("pace_trend") if isinstance(recent, dict) else None
        conference = adv.get("conference")
        return cls(
            name=name,
            team_id=team_id,
            adjo=float(adjo),
            adjd=float(adjd),
            adjt=float(adjt),
            pace_trend=pace_trend,
            conference=conference,
        )


def _parse_market_spread_static(teams: Dict[str, Any], market: Dict[str, Any]) -> Optional[float]:
    """
    Parse market spread string into home spread (home minus points).
    Example: "Duke -7.5" when Duke is home -> returns -7.5.
    """
    spread_val = market.get("spread")
    if spread_val is None:
        return None
    if isinstance(spread_val, (int, float)):
        return float(spread_val)
    if not isinstance(spread_val, str):
        return None
    home_name = str(teams.get("home", "")).lower()
    away_name = str(teams.get("away", "")).lower()
    num_match = re.search(r"[-+]?\d+(\.\d+)?", spread_val)
    if not num_match:
        return None
    try:
        line_val = float(num_match.group())
    except ValueError:
        return None
    lower = spread_val.lower()
    if home_name and home_name in lower:
        return line_val
    if away_name and away_name in lower:
        return -line_val
    return line_val


@dataclass
class GameContext:
    """Typed game context. Replaces GameStats; single parse boundary from researcher output."""
    game_id: str
    away: TeamContext
    home: TeamContext
    market_total: Optional[float] = None
    market_spread_home: Optional[float] = None
    is_neutral_site: bool = False
    is_rivalry: bool = False

    @classmethod
    def from_researcher_output(cls, game_data: Dict[str, Any]) -> Optional["GameContext"]:
        """Build GameContext from researcher output dict. Returns None if required stats missing."""
        game_id = game_data.get("game_id")
        if game_id is not None:
            game_id = str(game_id)
        else:
            game_id = "unknown"
        teams = game_data.get("teams", {}) or {}
        away_name = teams.get("away", "")
        home_name = teams.get("home", "")
        away_id = teams.get("away_id")
        home_id = teams.get("home_id")

        adv = game_data.get("adv", {})
        away_adv = adv.get("away", {}) if isinstance(adv, dict) else {}
        home_adv = adv.get("home", {}) if isinstance(adv, dict) else {}
        if not away_adv or not home_adv:
            legacy = game_data.get("advanced_stats", {})
            away_adv = away_adv or legacy.get("team2", {}) or {}
            home_adv = home_adv or legacy.get("team1", {}) or {}

        recent = game_data.get("recent", {}) or {}
        away_recent = (recent.get("away") or {}) if isinstance(recent, dict) else {}
        home_recent = (recent.get("home") or {}) if isinstance(recent, dict) else {}

        try:
            away_ctx = TeamContext.from_dict(away_name, away_id, away_adv, away_recent)
            home_ctx = TeamContext.from_dict(home_name, home_id, home_adv, home_recent)
        except ValueError:
            return None

        market_total = None
        market = game_data.get("market", {})
        if isinstance(market, dict):
            total_val = market.get("total")
            if isinstance(total_val, (int, float)):
                market_total = float(total_val)

        market_spread_home = _parse_market_spread_static(teams, market if isinstance(market, dict) else {})

        is_neutral = False
        is_rivalry = False
        context = game_data.get("context", [])
        if isinstance(context, list):
            for ctx_item in context:
                if isinstance(ctx_item, str):
                    lower = ctx_item.lower()
                    if "neutral site" in lower:
                        is_neutral = True
                    if "rivalry" in lower:
                        is_rivalry = True

        return cls(
            game_id=game_id,
            away=away_ctx,
            home=home_ctx,
            market_total=market_total,
            market_spread_home=market_spread_home,
            is_neutral_site=is_neutral,
            is_rivalry=is_rivalry,
        )

    @property
    def has_advanced_stats(self) -> bool:
        return True


def calculate_game_model(
    ctx: "GameContext",
    betting_lines: List[Dict[str, Any]],
    has_adv_stats: bool = True,
) -> Dict[str, Any]:
    """Run deterministic modeling for a single game. Team names/IDs come from ctx."""
    game_id = ctx.game_id
    away_team = ctx.away.name
    home_team = ctx.home.name
    away_id = ctx.away.team_id
    home_id = ctx.home.team_id

    base_pace, trend_adj, final_pace = calculate_pace(
        ctx.away.adjt,
        ctx.home.adjt,
        ctx.away.pace_trend,
        ctx.home.pace_trend,
    )
    away_pts_100 = calculate_points_per_100(ctx.away.adjo, ctx.home.adjd)
    home_pts_100 = calculate_points_per_100(ctx.home.adjo, ctx.away.adjd)

    tempo_multiplier = calculate_tempo_multiplier(final_pace)
    if tempo_multiplier > 1.0:
        away_pts_100 *= tempo_multiplier
        home_pts_100 *= tempo_multiplier

    raw_away, raw_home = calculate_raw_scores(away_pts_100, home_pts_100, final_pace)
    base_margin = raw_home - raw_away
    hca_adj = calculate_hca_adjustment(ctx.is_neutral_site)
    mismatch_adj = calculate_mismatch_adjustment(ctx)
    is_conference_game, grudge_total_adj, hca_reduction = calculate_conference_grudge_adjustment(ctx, final_pace)
    hca_adj -= hca_reduction
    raw_margin = base_margin + hca_adj + mismatch_adj

    dampened_margin, damp_applied = apply_margin_dampening(raw_margin)
    raw_total = raw_home + raw_away + grudge_total_adj
    market_total = ctx.market_total if ctx.market_total is not None else raw_total
    calibrated_total, regression_pct = calibrate_total(raw_total, market_total, final_pace)
    calibrated_total, garbage_time_applied = apply_garbage_time_adjustment(calibrated_total, dampened_margin)

    away_score, home_score = calculate_final_scores(calibrated_total, dampened_margin)
    margin = home_score - away_score
    total = away_score + home_score
    away_prob, home_prob = calculate_win_probability(margin)

    spread_diff = abs(margin - (-ctx.market_spread_home)) if ctx.market_spread_home is not None else 0.0
    total_diff = abs(total - market_total) if market_total is not None else 0.0
    edge_mag = max(spread_diff, total_diff)
    away_prob, home_prob, discrepancy_shrinkage_applied, shrink_factor = apply_discrepancy_shrinkage(
        away_prob, home_prob, edge_mag
    )

    margin = round(margin, 2)
    away_prob = round(away_prob, 2)
    home_prob = round(home_prob, 2)
    confidence = calculate_confidence(has_adv_stats, edge_mag, is_blowout=(abs(dampened_margin) > 20))

    predictions = {
        "scores": {"away": away_score, "home": home_score},
        "margin": margin,
        "total": total,
        "win_probs": {"away": away_prob, "home": home_prob},
        "confidence": confidence,
    }
    market_edges = calculate_market_edges(predictions, betting_lines)

    meta = {
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
        "market_spread_home": ctx.market_spread_home,
        "is_neutral_site": ctx.is_neutral_site,
        "is_conference_game": is_conference_game,
        "is_rivalry": ctx.is_rivalry,
        "tempo_multiplier": tempo_multiplier,
        "grudge_total_adj": grudge_total_adj,
        "discrepancy_shrinkage_applied": discrepancy_shrinkage_applied,
        "total_regression_pct": regression_pct,
        "edge_magnitude": edge_mag,
        "shrink_factor": shrink_factor if discrepancy_shrinkage_applied else 1.0,
    }
    return build_model_output(
        game_id, away_team, home_team, away_id, home_id,
        predictions, away_score, home_score, market_edges, edge_mag, meta,
    )

