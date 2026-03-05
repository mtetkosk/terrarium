"""Shared utilities for American odds handling."""


def american_odds_to_profit_multiplier(odds: int) -> float:
    """
    Convert American odds to profit multiplier (profit per unit staked on a win).
    
    Examples:
        -110 -> 100/110 ≈ 0.909 (risk 1.10 to win 1.00, so profit per unit staked ≈ 0.909)
        +150 -> 150/100 = 1.50 (risk 1.00 to win 1.50)
    
    Args:
        odds: American odds (negative for favorites, positive for underdogs).
        
    Returns:
        Profit per unit staked if the bet wins. For a loss, profit = -stake (multiplier not used).
    """
    if odds == 0:
        return 0.0
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)
