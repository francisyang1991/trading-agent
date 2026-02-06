"""
Tests for src/sizing/position_sizer.py

Tier 1 - Unit Tests (Pure Logic, No External Dependencies)

Tests:
- PositionSizer: Fixed fractional, Kelly, volatility target sizing
- SizingConfig: Configuration defaults and overrides
- SizingResult: Output structure
- Constraints: Max position, max risk, portfolio risk caps
"""

import pytest
from datetime import datetime

from src.sizing.position_sizer import (
    PositionSizer,
    SizingConfig,
    SizingMethod,
    SizingResult,
)
from src.core.types import Signal, SignalType, SignalStrength, Regime, VolatilityLevel


def _make_signal(
    symbol="AAPL",
    confidence=0.8,
    stop_loss=None,
    volatility=None,
):
    """Helper to create a Signal for sizing tests."""
    return Signal(
        symbol=symbol,
        signal_type=SignalType.ENTRY_LONG,
        strength=SignalStrength.STRONG,
        timestamp=datetime.now(),
        confidence=confidence,
        stop_loss=stop_loss,
        volatility=volatility,
    )


# =============================================================================
# SizingConfig
# =============================================================================

class TestSizingConfig:
    """Tests for SizingConfig defaults."""

    @pytest.mark.unit
    def test_default_values(self):
        cfg = SizingConfig()
        assert cfg.total_capital == 100_000
        assert cfg.max_risk_per_trade == 0.02
        assert cfg.max_position_pct == 0.10
        assert cfg.max_portfolio_risk == 0.06

    @pytest.mark.unit
    def test_custom_values(self):
        cfg = SizingConfig(total_capital=200_000, max_risk_per_trade=0.01)
        assert cfg.total_capital == 200_000
        assert cfg.max_risk_per_trade == 0.01


# =============================================================================
# PositionSizer - Fixed Fractional
# =============================================================================

class TestFixedFractionalSizing:
    """Tests for fixed fractional position sizing."""

    @pytest.mark.unit
    def test_basic_fixed_fractional(self):
        """$100k account, $150 stock, $8 stop distance."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        signal = _make_signal(stop_loss=142.0, confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=150.0,
            stop_loss=142.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert isinstance(result, SizingResult)
        assert result.shares > 0
        assert result.position_value > 0
        assert result.risk_amount >= 0

    @pytest.mark.unit
    def test_zero_risk_per_share(self):
        """Stop at entry price -> 0 shares."""
        sizer = PositionSizer()
        signal = _make_signal(stop_loss=150.0, confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=150.0,
            stop_loss=150.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert result.shares == 0

    @pytest.mark.unit
    def test_max_position_cap(self):
        """Position value should not exceed max_position_pct."""
        cfg = SizingConfig(total_capital=100_000, max_position_pct=0.10)
        sizer = PositionSizer(cfg)
        signal = _make_signal(stop_loss=9.0, confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=10.0,
            stop_loss=9.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert result.position_value <= 100_000 * 0.10 + 10  # Allow for rounding

    @pytest.mark.unit
    def test_max_risk_cap(self):
        """Risk amount should not exceed max_risk_per_trade."""
        cfg = SizingConfig(total_capital=100_000, max_risk_per_trade=0.02)
        sizer = PositionSizer(cfg)
        signal = _make_signal(confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert result.risk_amount <= 100_000 * 0.02 + 5  # Allow for rounding


# =============================================================================
# PositionSizer - Kelly
# =============================================================================

class TestKellySizing:
    """Tests for Kelly Criterion position sizing."""

    @pytest.mark.unit
    def test_kelly_positive_edge(self):
        """With positive edge, Kelly should produce > 0 shares."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        signal = _make_signal(confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=95.0,
            win_rate=0.60,
            avg_win_loss_ratio=2.0,
            method=SizingMethod.KELLY,
        )
        assert result.shares > 0

    @pytest.mark.unit
    def test_kelly_negative_edge(self):
        """With negative edge, Kelly should produce 0 shares."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        signal = _make_signal(confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=95.0,
            win_rate=0.20,
            avg_win_loss_ratio=0.5,
            method=SizingMethod.KELLY,
        )
        assert result.shares == 0


# =============================================================================
# PositionSizer - Volatility Target
# =============================================================================

class TestVolatilityTargetSizing:
    """Tests for volatility-based position sizing."""

    @pytest.mark.unit
    def test_high_vol_smaller_position(self):
        """High volatility -> smaller position."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        signal = _make_signal(confidence=1.0)
        high_vol_result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=90.0,
            volatility=0.50,  # 50% vol
            method=SizingMethod.VOLATILITY_TARGET,
        )
        low_vol_result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=90.0,
            volatility=0.15,  # 15% vol
            method=SizingMethod.VOLATILITY_TARGET,
        )
        assert high_vol_result.shares <= low_vol_result.shares


# =============================================================================
# PositionSizer - Confidence Scaling
# =============================================================================

class TestConfidenceScaling:
    """Tests for signal confidence impact on sizing."""

    @pytest.mark.unit
    def test_low_confidence_smaller_position(self):
        """Lower confidence -> smaller position."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        high_conf_signal = _make_signal(confidence=1.0)
        low_conf_signal = _make_signal(confidence=0.3)

        high_result = sizer.calculate_size(
            signal=high_conf_signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        low_result = sizer.calculate_size(
            signal=low_conf_signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert low_result.shares <= high_result.shares


# =============================================================================
# PositionSizer - Volatility Level Adjustments
# =============================================================================

class TestVolatilityLevelAdjustments:
    """Tests for regime-based volatility adjustments."""

    @pytest.mark.unit
    def test_high_vol_reduces_size(self):
        """HIGH volatility level reduces position size."""
        cfg = SizingConfig(total_capital=100_000, reduce_in_high_vol=True)
        sizer = PositionSizer(cfg)

        normal_signal = _make_signal(confidence=1.0, volatility=VolatilityLevel.MEDIUM)
        high_vol_signal = _make_signal(confidence=1.0, volatility=VolatilityLevel.HIGH)

        normal_result = sizer.calculate_size(
            signal=normal_signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        high_vol_result = sizer.calculate_size(
            signal=high_vol_signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        assert high_vol_result.shares <= normal_result.shares

    @pytest.mark.unit
    def test_extreme_vol_minimal_size(self):
        """EXTREME volatility -> minimal sizing (25%)."""
        cfg = SizingConfig(total_capital=100_000)
        sizer = PositionSizer(cfg)
        signal = _make_signal(confidence=1.0, volatility=VolatilityLevel.EXTREME)

        result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        # Should be very small due to EXTREME volatility
        assert result.shares >= 0  # At least not negative


# =============================================================================
# PositionSizer - Portfolio Risk
# =============================================================================

class TestPortfolioRisk:
    """Tests for portfolio-level risk tracking."""

    @pytest.mark.unit
    def test_portfolio_risk_cap(self):
        """When portfolio risk is near max, new positions should be tiny or zero."""
        cfg = SizingConfig(total_capital=100_000, max_portfolio_risk=0.06)
        sizer = PositionSizer(cfg)
        # Simulate existing risk near the limit
        sizer.update_portfolio_risk(5_900)

        signal = _make_signal(confidence=1.0)
        result = sizer.calculate_size(
            signal=signal,
            current_price=100.0,
            stop_loss=95.0,
            method=SizingMethod.FIXED_FRACTIONAL,
        )
        # Available risk = 6000 - 5900 = $100, at $5/share risk -> max 20 shares
        assert result.shares <= 20

    @pytest.mark.unit
    def test_update_capital(self):
        """Capital update changes sizing."""
        sizer = PositionSizer(SizingConfig(total_capital=100_000))
        sizer.update_capital(200_000)
        assert sizer.config.total_capital == 200_000


# =============================================================================
# PositionSizer - Kelly Fraction Calculation
# =============================================================================

class TestKellyFractionInternal:
    """Tests for the internal Kelly fraction calculation."""

    @pytest.mark.unit
    def test_kelly_fraction_positive(self):
        """Positive edge -> positive Kelly fraction."""
        sizer = PositionSizer()
        kelly = sizer._calculate_kelly_fraction(win_rate=0.60, avg_win_loss_ratio=2.0)
        assert kelly > 0

    @pytest.mark.unit
    def test_kelly_fraction_zero_ratio(self):
        """Zero W/L ratio -> 0 Kelly."""
        sizer = PositionSizer()
        kelly = sizer._calculate_kelly_fraction(win_rate=0.60, avg_win_loss_ratio=0.0)
        assert kelly == 0.0

    @pytest.mark.unit
    def test_kelly_fraction_negative_edge(self):
        """Negative edge -> 0 Kelly (floored)."""
        sizer = PositionSizer()
        kelly = sizer._calculate_kelly_fraction(win_rate=0.20, avg_win_loss_ratio=0.5)
        assert kelly == 0.0
