"""Predictive models for game outcomes"""

from typing import Optional
import numpy as np

from src.data.models import GameInsight, TeamStats, BettingLine, BetType


class SimpleLinearModel:
    """Simple linear predictive model"""
    
    def predict_spread(
        self,
        team1_stats: Optional[TeamStats],
        team2_stats: Optional[TeamStats],
        insight: Optional[GameInsight] = None
    ) -> float:
        """
        Predict point spread (team1 - team2)
        
        Returns:
            Predicted spread (positive means team1 wins)
        """
        if not team1_stats or not team2_stats:
            return 0.0
        
        # Simple model: difference in points per game and points allowed
        team1_net = team1_stats.points_per_game - team2_stats.points_allowed_per_game
        team2_net = team2_stats.points_per_game - team1_stats.points_allowed_per_game
        
        predicted_spread = team1_net - team2_net
        
        # Adjust for home court advantage (if applicable)
        # Placeholder: assume +3 for home team
        if insight and hasattr(insight, 'venue'):
            predicted_spread += 3.0
        
        return predicted_spread
    
    def predict_total(
        self,
        team1_stats: Optional[TeamStats],
        team2_stats: Optional[TeamStats]
    ) -> float:
        """
        Predict total points
        
        Returns:
            Predicted total points
        """
        if not team1_stats or not team2_stats:
            return 140.0  # Default
        
        # Average of both teams' points per game
        avg_points = (team1_stats.points_per_game + team2_stats.points_per_game) / 2
        
        # Adjust for pace if available
        if team1_stats.pace and team2_stats.pace:
            avg_pace = (team1_stats.pace + team2_stats.pace) / 2
            # Scale by pace relative to average (assume 70 is average)
            pace_factor = avg_pace / 70.0
            avg_points *= pace_factor
        
        return avg_points
    
    def calculate_win_probability(
        self,
        predicted_spread: float,
        line: float,
        bet_type: BetType
    ) -> float:
        """
        Calculate win probability for a bet
        
        Args:
            predicted_spread: Predicted spread
            line: Betting line
            bet_type: Type of bet
        
        Returns:
            Win probability (0-1)
        """
        if bet_type == BetType.SPREAD:
            # For spread bets, use normal distribution
            # Assume standard deviation of 10 points
            std_dev = 10.0
            margin = predicted_spread - line
            
            # Probability that actual spread > line
            # Using normal CDF approximation
            z_score = margin / std_dev
            win_prob = 0.5 + 0.5 * np.tanh(z_score)  # Approximation
            
            # Clamp to reasonable range
            win_prob = max(0.05, min(0.95, win_prob))
            
        elif bet_type == BetType.TOTAL:
            # For totals, use similar approach
            # This is simplified - would need predicted total
            win_prob = 0.5  # Placeholder
        
        elif bet_type == BetType.MONEYLINE:
            # Convert spread to win probability
            # Using logistic function
            win_prob = 1 / (1 + np.exp(-predicted_spread / 5.0))
            win_prob = max(0.05, min(0.95, win_prob))
        
        else:
            win_prob = 0.5
        
        return win_prob
    
    def calculate_confidence_score(
        self,
        insight: Optional[GameInsight],
        predicted_spread: float
    ) -> float:
        """
        Calculate confidence score for prediction
        
        Returns:
            Confidence score (0-1)
        """
        if not insight:
            return 0.5
        
        # Base confidence from data quality
        confidence = insight.confidence_factors.get('overall', 0.5)
        
        # Adjust for prediction magnitude (larger spreads = more confident)
        spread_magnitude = abs(predicted_spread)
        if spread_magnitude > 10:
            confidence *= 1.1
        elif spread_magnitude < 3:
            confidence *= 0.9
        
        # Clamp to valid range
        confidence = max(0.0, min(1.0, confidence))
        
        return confidence

