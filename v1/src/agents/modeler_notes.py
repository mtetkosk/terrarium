"""Model notes: typed context, builder, and formatters for modeler output."""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from src.agents.modeler_engine import (
    GameContext,
    EFF_BASELINE,
    calculate_win_probability,
    WIN_PROB_SCALE,
)


@dataclass
class ModelNotesContext:
    """Typed context for generating model notes. Built from GameContext + model output."""
    # Pace
    base_pace: float
    trend_adj: float
    final_pace: float
    away_adjt: Optional[float]
    home_adjt: Optional[float]
    away_trend_str: str
    home_trend_str: str
    # Margin / context
    base_margin: float
    hca_adj: float
    mismatch_adj: float
    raw_margin: float
    is_neutral: bool
    # Totals / calibration
    market_total: Optional[float]
    raw_total_calc: Optional[float]
    regression_pct: float
    calibrated_total: float
    # Efficiency (optional)
    away_adjo: Optional[float]
    away_adjd: Optional[float]
    home_adjo: Optional[float]
    home_adjd: Optional[float]
    away_pts_100: Optional[float]
    home_pts_100: Optional[float]
    raw_away: Optional[float]
    raw_home: Optional[float]
    # Win probs / confidence
    edge_mag: float
    shrink_factor: float
    discrepancy_shrinkage_applied: bool
    margin: float
    total: float
    home_prob: float
    confidence: float
    away_score: float
    home_score: float


def build_model_notes_context(game_ctx: GameContext, model: Dict[str, Any]) -> ModelNotesContext:
    """Build typed notes context from GameContext and model output. Single place that defines notes schema."""
    preds = model.get("predictions", {})
    meta = model.get("meta", {})
    scores = preds.get("scores", {})
    total = preds.get("total", 0.0)
    margin = preds.get("margin", 0.0)
    home_prob = preds.get("win_probs", {}).get("home", 0.5)
    confidence = preds.get("confidence", 0.3)
    calibrated_total = meta.get("calibrated_total", total)
    away_trend_str = game_ctx.away.pace_trend or "same"
    home_trend_str = game_ctx.home.pace_trend or "same"
    return ModelNotesContext(
        base_pace=meta.get("base_pace", 0.0),
        trend_adj=meta.get("trend_adjustment", 0.0),
        final_pace=meta.get("final_pace", 0.0),
        away_adjt=game_ctx.away.adjt,
        home_adjt=game_ctx.home.adjt,
        away_trend_str=away_trend_str,
        home_trend_str=home_trend_str,
        base_margin=meta.get("base_margin", 0.0),
        hca_adj=meta.get("hca_adjustment", 0.0),
        mismatch_adj=meta.get("mismatch_adjustment", 0.0),
        raw_margin=meta.get("raw_margin", 0.0),
        is_neutral=meta.get("is_neutral_site", False),
        market_total=meta.get("market_total_used"),
        raw_total_calc=meta.get("raw_total"),
        regression_pct=meta.get("total_regression_pct", 0.0),
        calibrated_total=calibrated_total,
        away_adjo=game_ctx.away.adjo,
        away_adjd=game_ctx.away.adjd,
        home_adjo=game_ctx.home.adjo,
        home_adjd=game_ctx.home.adjd,
        away_pts_100=meta.get("away_pts_per_100"),
        home_pts_100=meta.get("home_pts_per_100"),
        raw_away=meta.get("raw_away_score"),
        raw_home=meta.get("raw_home_score"),
        edge_mag=meta.get("edge_magnitude", 0.0),
        shrink_factor=meta.get("shrink_factor", 1.0),
        discrepancy_shrinkage_applied=meta.get("discrepancy_shrinkage_applied", False),
        margin=margin,
        total=total,
        home_prob=home_prob,
        confidence=confidence,
        away_score=scores.get("away", 0.0),
        home_score=scores.get("home", 0.0),
    )


def _format_pace_notes(ctx: ModelNotesContext) -> str:
    """Format PACE section of model notes."""
    base_pace = ctx.base_pace
    trend_adj = ctx.trend_adj
    final_pace = ctx.final_pace
    away_adjt = ctx.away_adjt
    home_adjt = ctx.home_adjt
    away_trend_str = ctx.away_trend_str
    home_trend_str = ctx.home_trend_str
    if away_adjt and home_adjt:
        slower = min(away_adjt, home_adjt)
        faster = max(away_adjt, home_adjt)
        pace_desc = f"Base pace=(Slower_AdjT {slower:.1f} * 0.65) + (Faster_AdjT {faster:.1f} * 0.35)={base_pace:.1f}"
    else:
        pace_desc = f"Base pace={base_pace:.1f}"
    trend_desc = f"Pace trends both '{away_trend_str if away_trend_str == home_trend_str else 'mixed'}'"
    trend_desc += f" => {trend_adj:+.1f}" if trend_adj != 0.0 else " => +0.0"
    return f"PACE: {pace_desc}. {trend_desc}. Final pace={final_pace:.1f}."


def _format_efficiency_notes(ctx: ModelNotesContext) -> List[str]:
    """Format EFFICIENCY BASELINE and PTS/100 sections."""
    lines = [f"EFFICIENCY BASELINE: eff_baseline={EFF_BASELINE:.1f} per protocol."]
    away_pts_100 = ctx.away_pts_100
    home_pts_100 = ctx.home_pts_100
    away_adjo = ctx.away_adjo
    away_adjd = ctx.away_adjd
    home_adjo = ctx.home_adjo
    home_adjd = ctx.home_adjd
    if all(x is not None for x in (away_pts_100, home_pts_100, away_adjo, away_adjd, home_adjo, home_adjd)):
        pts = f"away_pts_per_100=(Away_AdjO {away_adjo:.1f} * Home_AdjD {home_adjd:.1f})/{EFF_BASELINE:.1f}={away_pts_100:.1f}. "
        pts += f"home_pts_per_100=(Home_AdjO {home_adjo:.1f} * Away_AdjD {away_adjd:.1f})/{EFF_BASELINE:.1f}={home_pts_100:.1f}."
    else:
        pts = "PTS/100: Missing advanced stats (fallback used)."
    lines.append(f"PTS/100: {pts}")
    return lines


def _format_context_notes(ctx: ModelNotesContext) -> str:
    """Format CONTEXT section (HCA, injuries, mismatch)."""
    hca_adj = ctx.hca_adj
    is_neutral = ctx.is_neutral
    mismatch_adj = ctx.mismatch_adj
    hca_desc = f"HCA {hca_adj:+.1f}" if not is_neutral else "HCA 0.0 (neutral site)"
    mismatch_desc = f"Mismatch {mismatch_adj:+.1f}" if mismatch_adj != 0.0 else "Mismatch 0.0"
    return f"CONTEXT: {hca_desc}. Injuries none. {mismatch_desc}."


def _format_raw_notes(ctx: ModelNotesContext) -> str:
    """Format RAW section."""
    raw_away = ctx.raw_away
    raw_home = ctx.raw_home
    raw_total_calc = ctx.raw_total_calc
    away_pts_100 = ctx.away_pts_100
    home_pts_100 = ctx.home_pts_100
    final_pace = ctx.final_pace
    hca_adj = ctx.hca_adj
    mismatch_adj = ctx.mismatch_adj
    raw_margin = ctx.raw_margin
    base_margin = ctx.base_margin
    if raw_away and raw_home and raw_total_calc and away_pts_100 is not None and home_pts_100 is not None:
        raw_desc = f"raw_away=({away_pts_100:.1f}/100)*{final_pace:.1f}={raw_away:.1f}. "
        raw_desc += f"raw_home=({home_pts_100:.1f}/100)*{final_pace:.1f}={raw_home:.1f}. "
        raw_desc += f"raw_total={raw_total_calc:.1f}. "
        raw_desc += f"raw_margin=({raw_home:.1f}-{raw_away:.1f})+{hca_adj:.1f}+{mismatch_adj:.1f}={raw_margin:.1f}."
    else:
        raw_desc = f"raw_margin={raw_margin:.1f} (base {base_margin:.1f} + HCA {hca_adj:.1f} + mismatch {mismatch_adj:.1f})."
    return f"RAW: {raw_desc}"


def _format_total_calibration_notes(ctx: ModelNotesContext) -> Optional[str]:
    """Format TOTAL CALIBRATION section."""
    market_total = ctx.market_total
    raw_total_calc = ctx.raw_total_calc
    regression_pct = ctx.regression_pct
    calibrated_total = ctx.calibrated_total
    if market_total is None:
        return None
    if raw_total_calc is None:
        return f"TOTAL CALIBRATION: Market total={market_total:.1f}."
    total_diff = raw_total_calc - market_total
    reg_pct_str = f"{int(regression_pct * 100)}%"
    if raw_total_calc > 155.0:
        range_desc = "Raw total >155"
    elif 140.0 <= raw_total_calc <= 155.0:
        range_desc = "Raw total in standard range"
    else:
        range_desc = "Raw total <140"
    return f"TOTAL CALIBRATION: Market total={market_total:.1f}. total_diff={raw_total_calc:.1f}-{market_total:.1f}={total_diff:.1f}. {range_desc} => {reg_pct_str} regression: calibrated_total={raw_total_calc:.1f}-{regression_pct:.2f}*({total_diff:.1f})={calibrated_total:.1f}."


def _format_blowout_notes(ctx: ModelNotesContext) -> str:
    """Format BLOWOUT/GARBAGE TIME section."""
    raw_margin = ctx.raw_margin
    total = ctx.total
    if abs(raw_margin) > 22:
        return f"BLOWOUT: |raw_margin|={abs(raw_margin):.1f}>22 => garbage_time_adj=-4.0 => final total={total:.1f}."
    return f"BLOWOUT: |raw_margin|={abs(raw_margin):.1f} not >22 => no garbage-time adjustment."


def _format_win_probs_notes(ctx: ModelNotesContext) -> str:
    """Format WIN PROBS section."""
    margin = ctx.margin
    home_prob = ctx.home_prob
    edge_mag = ctx.edge_mag
    shrink_factor = ctx.shrink_factor
    shrinkage_applied = ctx.discrepancy_shrinkage_applied
    _, raw_home_prob = calculate_win_probability(margin)
    shrink_info = ""
    if shrinkage_applied and shrink_factor < 1.0:
        shrink_info = f" Discrepancy shrinkage: edge_mag={edge_mag:.1f} >{'8' if edge_mag > 8.0 else '6'} => shrink_factor={shrink_factor:.2f}."
    else:
        shrink_info = " No discrepancy shrinkage applied."
    return f"WIN PROBS: p_home_raw=1/(1+exp(-{margin:.1f}/{WIN_PROB_SCALE}))={raw_home_prob:.3f}.{shrink_info} p_home={home_prob:.3f}."


def _format_confidence_notes(ctx: ModelNotesContext) -> str:
    """Format CONFIDENCE section."""
    raw_margin = ctx.raw_margin
    edge_mag = ctx.edge_mag
    confidence = ctx.confidence
    away_adjo = ctx.away_adjo
    away_adjd = ctx.away_adjd
    away_adjt = ctx.away_adjt
    home_adjo = ctx.home_adjo
    home_adjd = ctx.home_adjd
    home_adjt = ctx.home_adjt
    data_quality = "good" if all((away_adjo, away_adjd, away_adjt, home_adjo, home_adjd, home_adjt)) else "limited"
    conf_desc = "Blowout tier caps at 0.60" if abs(raw_margin) > 20 else "Standard tier"
    return f"CONFIDENCE: {conf_desc}; data quality {data_quality}. Base 0.45 + (edge_mag {edge_mag:.1f}/40.0) = {confidence:.2f}."


def _format_final_line(ctx: ModelNotesContext) -> str:
    """Format FINAL score line."""
    return f"FINAL: final_home={ctx.total:.1f}/2+{ctx.margin:.1f}/2={ctx.home_score:.1f}; final_away={ctx.away_score:.1f}."


def format_model_notes(ctx: ModelNotesContext) -> str:
    """Build full model notes string from context. Single entry point for all section formatters."""
    notes_lines = [
        _format_pace_notes(ctx),
        *_format_efficiency_notes(ctx),
        _format_context_notes(ctx),
        _format_raw_notes(ctx),
    ]
    total_cal_line = _format_total_calibration_notes(ctx)
    if total_cal_line:
        notes_lines.append(total_cal_line)
    notes_lines.extend([
        _format_blowout_notes(ctx),
        _format_final_line(ctx),
        _format_win_probs_notes(ctx),
        _format_confidence_notes(ctx),
    ])
    return "\n".join(notes_lines)
