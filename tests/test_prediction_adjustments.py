"""Tests for prediction adjustment strategies."""

import pytest
from src.utils.prediction_adjustments import (
    adjust_spread,
    adjust_total_with_market,
    clamp_total,
    apply_all_adjustments,
    SPREAD_SHRINK,
    BLOWOUT_START_THRESHOLD,
    BLOWOUT_DAMPEN_FACTOR,
    TOTAL_MODEL_WEIGHT,
    TOTAL_MARKET_WEIGHT,
    TOTAL_CLAMP_LOW,
    TOTAL_CLAMP_HIGH
)


class TestAdjustSpread:
    """Test Strategy 2: Spread shrinkage + blowout dampening."""
    
    def test_small_spread_shrinkage(self):
        """Spreads <= 6 pts should get 95% shrinkage."""
        # +5 pt spread should become +4.75
        result = adjust_spread(5.0)
        assert result == pytest.approx(5.0 * SPREAD_SHRINK, abs=0.01)
        
        # -3 pt spread should become -2.85
        result = adjust_spread(-3.0)
        assert result == pytest.approx(-3.0 * SPREAD_SHRINK, abs=0.01)
    
    def test_blowout_dampening(self):
        """Spreads > 6 pts should get blowout dampening on excess."""
        # +15 pt spread:
        # Base part: 6 * 0.95 = 5.7
        # Excess: 15 - 6 = 9
        # Dampened excess: 9 * 0.65 = 5.85
        # Total: 5.7 + 5.85 = 11.55
        result = adjust_spread(15.0)
        expected = BLOWOUT_START_THRESHOLD * SPREAD_SHRINK + (15 - BLOWOUT_START_THRESHOLD) * BLOWOUT_DAMPEN_FACTOR
        assert result == pytest.approx(expected, abs=0.01)
        
    def test_negative_blowout(self):
        """Negative blowout spreads should be handled correctly."""
        result = adjust_spread(-20.0)
        expected = -(BLOWOUT_START_THRESHOLD * SPREAD_SHRINK + (20 - BLOWOUT_START_THRESHOLD) * BLOWOUT_DAMPEN_FACTOR)
        assert result == pytest.approx(expected, abs=0.01)
    
    def test_zero_spread(self):
        """Zero spread should remain zero."""
        result = adjust_spread(0.0)
        assert result == 0.0
    
    def test_at_threshold(self):
        """Spread at exactly threshold should use simple shrinkage."""
        result = adjust_spread(6.0)
        assert result == pytest.approx(6.0 * SPREAD_SHRINK, abs=0.01)


class TestAdjustTotalWithMarket:
    """Test Strategy 1: Blend with market total."""
    
    def test_blend_with_market(self):
        """Should blend 40% model + 60% market."""
        model_total = 150.0
        market_total = 160.0
        
        result = adjust_total_with_market(model_total, market_total)
        expected = 0.4 * 150 + 0.6 * 160  # 60 + 96 = 156
        assert result == pytest.approx(expected, abs=0.1)
    
    def test_no_market_available(self):
        """Should return model total when no market available."""
        model_total = 155.0
        
        result = adjust_total_with_market(model_total, None)
        assert result == model_total
        
        result = adjust_total_with_market(model_total, 0)
        assert result == model_total
    
    def test_negative_market(self):
        """Should handle invalid market total gracefully."""
        result = adjust_total_with_market(150.0, -10.0)
        assert result == 150.0


class TestClampTotal:
    """Test Strategy 3: Clamp total predictions."""
    
    def test_low_total_clamped(self):
        """Totals below 135 should be clamped to 135."""
        result = clamp_total(120.0)
        assert result == TOTAL_CLAMP_LOW
    
    def test_high_total_clamped(self):
        """Totals above 170 should be clamped to 170."""
        result = clamp_total(185.0)
        assert result == TOTAL_CLAMP_HIGH
    
    def test_normal_total_unchanged(self):
        """Totals within range should be unchanged."""
        result = clamp_total(155.0)
        assert result == 155.0
    
    def test_at_boundaries(self):
        """Totals at boundaries should be unchanged."""
        assert clamp_total(135.0) == 135.0
        assert clamp_total(170.0) == 170.0


class TestApplyAllAdjustments:
    """Test combined adjustment application."""
    
    def test_all_adjustments_applied(self):
        """All adjustments should be applied in sequence."""
        spread, total = apply_all_adjustments(
            predicted_spread=15.0,
            predicted_total=180.0,  # High, should be blended and clamped
            market_total=165.0
        )
        
        # Spread: blowout dampening applied
        expected_spread = BLOWOUT_START_THRESHOLD * SPREAD_SHRINK + (15 - BLOWOUT_START_THRESHOLD) * BLOWOUT_DAMPEN_FACTOR
        assert spread == pytest.approx(expected_spread, abs=0.01)
        
        # Total: blended then clamped
        blended = 0.4 * 180 + 0.6 * 165  # 72 + 99 = 171
        # Then clamped to 170
        assert total == TOTAL_CLAMP_HIGH
    
    def test_selective_adjustments(self):
        """Can disable individual adjustments."""
        # Disable spread adjustment
        spread, total = apply_all_adjustments(
            predicted_spread=15.0,
            predicted_total=155.0,
            market_total=160.0,
            apply_spread_adjustment=False
        )
        assert spread == 15.0  # Unchanged
        
        # Disable total blend
        spread, total = apply_all_adjustments(
            predicted_spread=5.0,
            predicted_total=155.0,
            market_total=160.0,
            apply_total_blend=False
        )
        assert total == 155.0  # Only model, no blend
        
        # Disable clamp
        spread, total = apply_all_adjustments(
            predicted_spread=5.0,
            predicted_total=180.0,
            market_total=None,
            apply_total_clamp=False
        )
        assert total == 180.0  # Not clamped
    
    def test_none_total_handled(self):
        """None total should be preserved."""
        spread, total = apply_all_adjustments(
            predicted_spread=10.0,
            predicted_total=None,
            market_total=150.0
        )
        assert total is None


class TestRealWorldScenarios:
    """Test realistic prediction scenarios."""
    
    def test_typical_game(self):
        """Test a typical game with moderate predictions."""
        # Model predicts home team by 7, total of 152
        # Market has total at 155
        spread, total = apply_all_adjustments(
            predicted_spread=7.0,
            predicted_total=152.0,
            market_total=155.0
        )
        
        # Spread: 6 * 0.95 + 1 * 0.65 = 5.7 + 0.65 = 6.35
        assert spread == pytest.approx(6.35, abs=0.1)
        
        # Total: 0.4 * 152 + 0.6 * 155 = 60.8 + 93 = 153.8
        assert total == pytest.approx(153.8, abs=0.2)
    
    def test_blowout_prediction(self):
        """Test a blowout prediction that gets heavily dampened."""
        # Model predicts home team by 25
        spread, total = apply_all_adjustments(
            predicted_spread=25.0,
            predicted_total=165.0,
            market_total=160.0
        )
        
        # Spread: 6 * 0.95 + 19 * 0.65 = 5.7 + 12.35 = 18.05
        assert spread == pytest.approx(18.05, abs=0.1)
    
    def test_low_scoring_game(self):
        """Test a low-scoring game prediction that gets clamped."""
        spread, total = apply_all_adjustments(
            predicted_spread=-3.0,
            predicted_total=125.0,  # Very low
            market_total=130.0
        )
        
        # Total blended: 0.4 * 125 + 0.6 * 130 = 50 + 78 = 128
        # Then clamped to 135
        assert total == TOTAL_CLAMP_LOW
    
    def test_high_scoring_game(self):
        """Test a high-scoring game prediction that gets clamped."""
        spread, total = apply_all_adjustments(
            predicted_spread=5.0,
            predicted_total=185.0,  # Very high
            market_total=175.0
        )
        
        # Total blended: 0.4 * 185 + 0.6 * 175 = 74 + 105 = 179
        # Then clamped to 170
        assert total == TOTAL_CLAMP_HIGH
