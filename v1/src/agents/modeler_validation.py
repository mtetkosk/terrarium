"""Modeler validation: score/team consistency checks for model output."""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from src.agents.modeler_engine import GameContext
from src.utils.logging import get_logger

logger = get_logger("agents.modeler_validation")


@dataclass
class ValidationResult:
    """Result of a single score/team consistency check."""
    valid: bool
    warning: Optional[str] = None
    details: Optional[str] = None


def _validate_team_id_match(
    input_teams: Dict[str, Any],
    model_teams: Dict[str, Any],
    game_id: str,
) -> ValidationResult:
    """Check that input and output team IDs match."""
    for side in ("away", "home"):
        input_id = input_teams.get(f"{side}_id")
        output_id = model_teams.get(f"{side}_id")
        if input_id is not None and output_id is not None and input_id != output_id:
            msg = (
                f"TEAM ID MISMATCH: Input {side}_id={input_id} but output {side}_id={output_id}. "
                "This indicates a team assignment error."
            )
            logger.error(f"Game {game_id}: {msg}")
            return ValidationResult(valid=False, warning=msg)
    return ValidationResult(valid=True)


def _validate_margin_consistency(
    away_score: float,
    home_score: float,
    margin: float,
    game_id: str,
) -> ValidationResult:
    """Check margin = home_score - away_score."""
    calculated = home_score - away_score
    if abs(calculated - margin) <= 0.5:
        return ValidationResult(valid=True)
    msg = (
        f"MARGIN MISMATCH: margin={margin} but home({home_score})-away({away_score})={calculated:.1f}. "
        "This may indicate a score inversion bug."
    )
    logger.error(f"Game {game_id}: {msg}")
    return ValidationResult(valid=False, warning=msg)


def _validate_market_direction(
    margin: float,
    market_spread: str,
    input_teams: Dict[str, Any],
    game_id: str,
) -> ValidationResult:
    """Compare model margin direction with market spread. Adds details if mismatch (never sets valid=False)."""
    away_team = input_teams.get("away", "").lower()
    home_team = input_teams.get("home", "").lower()
    spread_lower = market_spread.lower()
    market_home_favored = None
    if "+" in spread_lower:
        if home_team and home_team in spread_lower.split("+")[0]:
            market_home_favored = False
        elif away_team and away_team in spread_lower.split("+")[0]:
            market_home_favored = True
    elif "-" in spread_lower and not spread_lower.startswith("-"):
        parts = spread_lower.split("-")
        if len(parts) >= 2:
            team_part = parts[0].strip()
            if home_team and home_team in team_part:
                market_home_favored = True
            elif away_team and away_team in team_part:
                market_home_favored = False
    if market_home_favored is None or abs(margin) <= 8:
        return ValidationResult(valid=True)
    model_home_favored = margin > 0
    if market_home_favored == model_home_favored:
        return ValidationResult(valid=True)
    details = (
        f"DIRECTIONAL MISMATCH (POSSIBLE INVERSION): "
        f"Market ({market_spread}) suggests {'home' if market_home_favored else 'away'} favored, "
        f"but model margin={margin:.1f} suggests {'home' if model_home_favored else 'away'} favored. "
        f"Team IDs: away={input_teams.get('away_id')} ({away_team}), home={input_teams.get('home_id')} ({home_team}). "
        "VERIFY team assignments are correct!"
    )
    return ValidationResult(valid=True, details=details)


def _validate_win_prob_consistency(
    margin: float,
    win_probs: Dict[str, float],
    input_teams: Dict[str, Any],
    game_id: str,
) -> ValidationResult:
    """Check win_probs consistent with margin."""
    away_prob = win_probs.get("away", 0)
    home_prob = win_probs.get("home", 0)
    if margin > 2 and away_prob > home_prob:
        msg = (
            f"WIN PROB INCONSISTENCY: margin={margin:.1f} (home wins) but "
            f"away_prob={away_prob:.2f} > home_prob={home_prob:.2f}. "
            f"Possible score inversion. Team IDs: away={input_teams.get('away_id')}, home={input_teams.get('home_id')}"
        )
        logger.error(f"Game {game_id}: {msg}")
        return ValidationResult(valid=False, warning=msg)
    if margin < -2 and home_prob > away_prob:
        msg = (
            f"WIN PROB INCONSISTENCY: margin={margin:.1f} (away wins) but "
            f"home_prob={home_prob:.2f} > away_prob={away_prob:.2f}. "
            f"Possible score inversion. Team IDs: away={input_teams.get('away_id')}, home={input_teams.get('home_id')}"
        )
        logger.error(f"Game {game_id}: {msg}")
        return ValidationResult(valid=False, warning=msg)
    return ValidationResult(valid=True)


def _run_validators(
    input_teams: Dict[str, Any],
    predictions: Dict[str, Any],
    scores: Dict[str, Any],
    margin: Any,
    model_teams: Dict[str, Any],
    game_id: str,
    game_data: Optional[Dict[str, Any]],
) -> List[Optional[ValidationResult]]:
    """Run all validators; returns a list of ValidationResult or None (skip)."""
    away_score = scores.get("away")
    home_score = scores.get("home")
    results: List[Optional[ValidationResult]] = []
    results.append(_validate_team_id_match(input_teams, model_teams, game_id))
    if away_score is not None and home_score is not None:
        results.append(_validate_margin_consistency(away_score, home_score, margin, game_id))
    else:
        results.append(None)
    if game_data:
        market = game_data.get("market", {})
        market_spread = market.get("spread")
        if market_spread and isinstance(market_spread, str):
            results.append(_validate_market_direction(margin, market_spread, input_teams, game_id))
        else:
            results.append(None)
    else:
        results.append(None)
    win_probs = predictions.get("win_probs", {})
    if win_probs:
        results.append(_validate_win_prob_consistency(margin, win_probs, input_teams, game_id))
    else:
        results.append(None)
    return results


def validate_score_team_consistency(
    model: Dict[str, Any],
    game_context: GameContext,
    game_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Validate that model scores are correctly assigned to teams using team IDs.
    Detects potential score inversion bugs. Returns dict with valid, warning, corrected, details.
    """
    result = {"valid": True, "warning": None, "corrected": False, "details": ""}
    input_teams = {
        "away": game_context.away.name,
        "home": game_context.home.name,
        "away_id": game_context.away.team_id,
        "home_id": game_context.home.team_id,
    }
    try:
        predictions = model.get("predictions", {})
        scores = predictions.get("scores", {})
        margin = predictions.get("margin")
        model_teams = model.get("teams", {})
        game_id = model.get("game_id", "unknown")
        if not scores or margin is None:
            return result
        away_score = scores.get("away")
        home_score = scores.get("home")
        if away_score is None or home_score is None:
            return result

        for r in _run_validators(
            input_teams, predictions, scores, margin, model_teams, game_id, game_data
        ):
            if r is None:
                continue
            if not r.valid and r.warning:
                result["valid"] = False
                result["warning"] = result["warning"] or r.warning
            if r.details:
                result["details"] = r.details
                logger.warning(f"Game {game_id}: {r.details}")
    except Exception as e:
        logger.error(f"Error validating score consistency for game {model.get('game_id', 'unknown')}: {e}")
    return result
