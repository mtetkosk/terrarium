"""Kelly criterion calculator"""

from typing import Optional


def calculate_kelly_stake(
    win_probability: float,
    odds: int,
    bankroll: float,
    fraction: float = 0.25
) -> float:
    """
    Calculate optimal stake using fractional Kelly criterion
    
    Args:
        win_probability: Probability of winning (0-1)
        odds: American odds (e.g., -110, +150)
        bankroll: Current bankroll
        fraction: Kelly fraction (0.25 = quarter Kelly)
    
    Returns:
        Optimal stake amount
    """
    # Convert American odds to decimal odds
    if odds > 0:
        decimal_odds = (odds / 100) + 1
    else:
        decimal_odds = (100 / abs(odds)) + 1
    
    # Calculate Kelly percentage
    # Kelly% = (p * b - q) / b
    # where p = win probability, q = loss probability, b = decimal odds - 1
    b = decimal_odds - 1
    q = 1 - win_probability
    
    if b <= 0:
        return 0.0
    
    kelly_percentage = (win_probability * b - q) / b
    
    # Apply fractional Kelly
    kelly_percentage *= fraction
    
    # Ensure non-negative
    kelly_percentage = max(0.0, kelly_percentage)
    
    # Calculate stake
    stake = bankroll * kelly_percentage
    
    return stake


def calculate_ev(win_probability: float, odds: int, stake: float) -> float:
    """
    Calculate expected value of a bet
    
    Args:
        win_probability: Probability of winning (0-1)
        odds: American odds
        stake: Bet stake
    
    Returns:
        Expected value
    """
    # Convert American odds to payout multiplier
    if odds > 0:
        payout_multiplier = (odds / 100) + 1
    else:
        payout_multiplier = (100 / abs(odds)) + 1
    
    # EV = (win_prob * payout) - (loss_prob * stake)
    ev = (win_probability * stake * payout_multiplier) - ((1 - win_probability) * stake)
    
    return ev

