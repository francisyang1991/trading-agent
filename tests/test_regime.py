"""
Tests for src/regime/classifier.py

Tier 1 - Unit Tests (Pure Logic, No External Dependencies)

Tests:
- classify_regime: Momentum-based regime classification
- classify_regime_from_prices: Regime from price series
- calculate_momentum: Percentage return calculation
- is_tradeable_regime: Regime tradability check
- get_regime_risk_multiplier: Regime-based risk multiplier
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.regime.classifier import (
    Regime,
    classify_regime,
    classify_regime_from_prices,
    calculate_momentum,
    is_tradeable_regime,
    get_regime_risk_multiplier,
)


# =============================================================================
# classify_regime
# =============================================================================

class TestClassifyRegime:
    """Tests for regime classification from momentum."""

    @pytest.mark.unit
    def test_parabolic(self):
        """>100% momentum -> PARABOLIC."""
        assert classify_regime(150.0) == Regime.PARABOLIC

    @pytest.mark.unit
    def test_parabolic_boundary(self):
        """Exactly 100% -> STRONG_UP (boundary is >100)."""
        assert classify_regime(100.0) == Regime.STRONG_UP

    @pytest.mark.unit
    def test_strong_up(self):
        """50-100% -> STRONG_UP."""
        assert classify_regime(75.0) == Regime.STRONG_UP

    @pytest.mark.unit
    def test_strong_up_boundary(self):
        """Exactly 50% -> MODERATE_UP (boundary is >50)."""
        assert classify_regime(50.0) == Regime.MODERATE_UP

    @pytest.mark.unit
    def test_moderate_up(self):
        """20-50% -> MODERATE_UP."""
        assert classify_regime(35.0) == Regime.MODERATE_UP

    @pytest.mark.unit
    def test_weak_up(self):
        """0-20% -> WEAK_UP."""
        assert classify_regime(10.0) == Regime.WEAK_UP

    @pytest.mark.unit
    def test_sideways(self):
        """-20% to 0% -> SIDEWAYS."""
        assert classify_regime(-10.0) == Regime.SIDEWAYS

    @pytest.mark.unit
    def test_downtrend(self):
        """<-20% -> DOWNTREND."""
        assert classify_regime(-30.0) == Regime.DOWNTREND

    @pytest.mark.unit
    def test_downtrend_boundary(self):
        """Exactly -20% -> DOWNTREND (boundary is > -20 for SIDEWAYS)."""
        assert classify_regime(-20.0) == Regime.DOWNTREND

    @pytest.mark.unit
    def test_zero_momentum(self):
        """Exactly 0% -> SIDEWAYS (boundary is >0 for WEAK_UP)."""
        assert classify_regime(0.0) == Regime.SIDEWAYS

    @pytest.mark.unit
    def test_extreme_positive(self):
        """500% -> PARABOLIC."""
        assert classify_regime(500.0) == Regime.PARABOLIC

    @pytest.mark.unit
    def test_extreme_negative(self):
        """-80% -> DOWNTREND."""
        assert classify_regime(-80.0) == Regime.DOWNTREND


# =============================================================================
# classify_regime_from_prices
# =============================================================================

class TestClassifyRegimeFromPrices:
    """Tests for regime classification from price series."""

    @pytest.mark.unit
    def test_uptrending_prices(self):
        """Strong uptrend in prices -> should be a bullish regime."""
        n = 200
        # Create strong uptrend: 100 -> 200 (+100%)
        prices = pd.Series(np.linspace(100, 200, n))
        regime = classify_regime_from_prices(prices, lookback_days=126)
        assert regime in (Regime.PARABOLIC, Regime.STRONG_UP, Regime.MODERATE_UP)

    @pytest.mark.unit
    def test_downtrending_prices(self):
        """Strong downtrend in prices."""
        n = 200
        prices = pd.Series(np.linspace(100, 60, n))  # -40%
        regime = classify_regime_from_prices(prices, lookback_days=126)
        assert regime == Regime.DOWNTREND

    @pytest.mark.unit
    def test_flat_prices(self):
        """Flat prices -> SIDEWAYS."""
        n = 200
        prices = pd.Series(np.full(n, 100.0))
        regime = classify_regime_from_prices(prices, lookback_days=126)
        assert regime == Regime.SIDEWAYS

    @pytest.mark.unit
    def test_insufficient_data_uses_available(self):
        """Not enough data for full lookback -> use available data."""
        prices = pd.Series([100.0, 150.0])  # +50%
        regime = classify_regime_from_prices(prices, lookback_days=126)
        assert regime == Regime.MODERATE_UP or regime == Regime.STRONG_UP

    @pytest.mark.unit
    def test_single_point_returns_sideways(self):
        """Single data point -> SIDEWAYS default."""
        prices = pd.Series([100.0])
        regime = classify_regime_from_prices(prices, lookback_days=126)
        assert regime == Regime.SIDEWAYS


# =============================================================================
# calculate_momentum
# =============================================================================

class TestCalculateMomentum:
    """Tests for momentum calculation."""

    @pytest.mark.unit
    def test_basic_momentum(self):
        """100 -> 150 over 10 days -> 50%."""
        prices = pd.Series([100.0] * 10 + [150.0])
        momentum = calculate_momentum(prices, days=10)
        assert momentum == pytest.approx(50.0, abs=0.1)

    @pytest.mark.unit
    def test_negative_momentum(self):
        """100 -> 80 -> -20%."""
        prices = pd.Series([100.0] * 10 + [80.0])
        momentum = calculate_momentum(prices, days=10)
        assert momentum == pytest.approx(-20.0, abs=0.1)

    @pytest.mark.unit
    def test_insufficient_data(self):
        """Not enough data -> 0.0."""
        prices = pd.Series([100.0, 110.0])
        momentum = calculate_momentum(prices, days=10)
        assert momentum == 0.0

    @pytest.mark.unit
    def test_zero_momentum(self):
        """Same price -> 0%."""
        prices = pd.Series([100.0] * 20)
        momentum = calculate_momentum(prices, days=10)
        assert momentum == pytest.approx(0.0, abs=0.01)


# =============================================================================
# is_tradeable_regime / get_regime_risk_multiplier
# =============================================================================

class TestRegimeHelpers:
    """Tests for regime helper functions."""

    @pytest.mark.unit
    def test_tradeable_regimes(self):
        """All regimes except DOWNTREND should be tradeable."""
        for regime in Regime:
            if regime == Regime.DOWNTREND:
                assert is_tradeable_regime(regime) is False
            else:
                assert is_tradeable_regime(regime) is True

    @pytest.mark.unit
    def test_downtrend_not_tradeable(self):
        assert is_tradeable_regime(Regime.DOWNTREND) is False

    @pytest.mark.unit
    def test_strong_up_full_multiplier(self):
        """STRONG_UP should have the highest multiplier (1.0)."""
        assert get_regime_risk_multiplier(Regime.STRONG_UP) == 1.0

    @pytest.mark.unit
    def test_downtrend_zero_multiplier(self):
        """DOWNTREND should have 0.0 multiplier."""
        assert get_regime_risk_multiplier(Regime.DOWNTREND) == 0.0

    @pytest.mark.unit
    def test_sideways_reduced_multiplier(self):
        """SIDEWAYS should have reduced multiplier."""
        mult = get_regime_risk_multiplier(Regime.SIDEWAYS)
        assert 0.0 < mult < 1.0

    @pytest.mark.unit
    def test_parabolic_reduced_multiplier(self):
        """PARABOLIC should have reduced multiplier (risky)."""
        mult = get_regime_risk_multiplier(Regime.PARABOLIC)
        assert 0.0 < mult < 1.0

    @pytest.mark.unit
    def test_all_multipliers_in_range(self):
        """All multipliers should be between 0.0 and 1.0."""
        for regime in Regime:
            mult = get_regime_risk_multiplier(regime)
            assert 0.0 <= mult <= 1.0, f"{regime}: {mult}"
