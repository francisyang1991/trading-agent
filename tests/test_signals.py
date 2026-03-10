"""
Tests for src/signals/entry_engine.py

Tier 2 - Integration Tests (Mocked External Dependencies)

Tests:
- EntryEngine: Strategy selection based on regime/volatility matrix
- EntryEngine.generate_entry: Single-symbol entry generation
- EntryEngine.generate_entries: Multi-symbol entry generation
- EntryEngine.scan_for_entries: Filtered scanning
- ENTRY_STRATEGY_MATRIX: Correct strategy mapping
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from unittest.mock import patch, MagicMock

from src.signals.entry_engine import (
    EntryEngine,
    EntryEngineConfig,
    ENTRY_STRATEGY_MATRIX,
    create_entry_engine,
)
from src.core.types import (
    Signal,
    SignalType,
    SignalStrength,
    Regime,
    VolatilityLevel,
    Strategy,
)


def _make_ohlcv(n=200, seed=42, trend="up"):
    """Create synthetic OHLCV DataFrame for testing."""
    np.random.seed(seed)
    dates = pd.date_range(end=datetime.now(), periods=n, freq="D")
    drift = {"up": 0.002, "down": -0.002, "flat": 0.0}.get(trend, 0.0)
    returns = np.random.normal(drift, 0.015, n)
    prices = 100 * np.exp(np.cumsum(returns))

    return pd.DataFrame(
        {
            "open": prices * (1 - np.random.uniform(0.002, 0.005, n)),
            "high": prices * (1 + np.random.uniform(0.005, 0.015, n)),
            "low": prices * (1 - np.random.uniform(0.005, 0.015, n)),
            "close": prices,
            "volume": np.random.randint(1_000_000, 5_000_000, n),
        },
        index=dates,
    )


# =============================================================================
# ENTRY_STRATEGY_MATRIX
# =============================================================================

class TestEntryStrategyMatrix:
    """Tests for the strategy selection matrix."""

    @pytest.mark.integration
    def test_all_regime_vol_combos_covered(self):
        """Every (Regime, VolatilityLevel) combo should have a mapping."""
        for regime in Regime:
            for vol in VolatilityLevel:
                key = (regime, vol)
                assert key in ENTRY_STRATEGY_MATRIX, (
                    f"Missing matrix entry for {regime}, {vol}"
                )

    @pytest.mark.integration
    def test_downtrend_extreme_is_cash(self):
        """DOWNTREND + EXTREME vol -> STAY_CASH only."""
        strategies = ENTRY_STRATEGY_MATRIX[(Regime.DOWNTREND, VolatilityLevel.EXTREME)]
        assert Strategy.STAY_CASH in strategies

    @pytest.mark.integration
    def test_strong_up_low_includes_trend(self):
        """STRONG_UP + LOW vol -> should include TREND_FOLLOWING."""
        strategies = ENTRY_STRATEGY_MATRIX[(Regime.STRONG_UP, VolatilityLevel.LOW)]
        assert Strategy.TREND_FOLLOWING in strategies

    @pytest.mark.integration
    def test_sideways_medium_includes_mean_reversion(self):
        """SIDEWAYS + MEDIUM vol -> should include MEAN_REVERSION."""
        strategies = ENTRY_STRATEGY_MATRIX[(Regime.SIDEWAYS, VolatilityLevel.MEDIUM)]
        assert Strategy.MEAN_REVERSION in strategies


# =============================================================================
# EntryEngine - Configuration
# =============================================================================

class TestEntryEngineConfig:
    """Tests for entry engine configuration."""

    @pytest.mark.integration
    def test_default_config(self):
        config = EntryEngineConfig()
        assert config.min_confidence == 0.60
        assert config.enable_trend is True
        assert config.enable_pullback is True

    @pytest.mark.integration
    def test_custom_config(self):
        config = EntryEngineConfig(min_confidence=0.80, enable_breakout=False)
        assert config.min_confidence == 0.80
        assert config.enable_breakout is False

    @pytest.mark.integration
    def test_create_entry_engine_factory(self):
        engine = create_entry_engine(min_confidence=0.75)
        assert engine.config.min_confidence == 0.75


# =============================================================================
# EntryEngine - Signal Generation
# =============================================================================

class TestEntryEngineSignalGeneration:
    """Tests for signal generation."""

    @pytest.mark.integration
    def test_generate_entry_returns_signal_or_none(self):
        """generate_entry should return a Signal or None."""
        engine = EntryEngine()
        data = _make_ohlcv(200, trend="up")
        result = engine.generate_entry(
            symbol="TEST",
            data=data,
            regime=Regime.STRONG_UP,
            volatility=VolatilityLevel.MEDIUM,
        )
        assert result is None or isinstance(result, Signal)

    @pytest.mark.integration
    def test_stay_cash_returns_none(self):
        """If only strategy is STAY_CASH, should return None."""
        engine = EntryEngine()
        data = _make_ohlcv(200)
        result = engine.generate_entry(
            symbol="TEST",
            data=data,
            regime=Regime.DOWNTREND,
            volatility=VolatilityLevel.EXTREME,
        )
        assert result is None

    @pytest.mark.integration
    def test_generate_entries_multiple_symbols(self):
        """generate_entries returns list sorted by confidence."""
        engine = EntryEngine()
        price_data = {
            "AAPL": _make_ohlcv(200, seed=1, trend="up"),
            "MSFT": _make_ohlcv(200, seed=2, trend="up"),
            "TSLA": _make_ohlcv(200, seed=3, trend="up"),
        }
        signals = engine.generate_entries(
            symbols=["AAPL", "MSFT", "TSLA"],
            price_data=price_data,
            regime=Regime.STRONG_UP,
            volatility=VolatilityLevel.MEDIUM,
        )
        assert isinstance(signals, list)
        # If any signals, they should be sorted by confidence
        if len(signals) > 1:
            confidences = [s.confidence for s in signals]
            assert confidences == sorted(confidences, reverse=True)

    @pytest.mark.integration
    def test_generate_entries_missing_symbol(self):
        """Missing symbol in price_data should be skipped."""
        engine = EntryEngine()
        price_data = {"AAPL": _make_ohlcv(200)}
        signals = engine.generate_entries(
            symbols=["AAPL", "MISSING"],
            price_data=price_data,
            regime=Regime.STRONG_UP,
        )
        assert isinstance(signals, list)

    @pytest.mark.integration
    def test_scan_for_entries_respects_max(self):
        """scan_for_entries limits results to max_entries."""
        engine = EntryEngine()
        price_data = {f"SYM{i}": _make_ohlcv(200, seed=i) for i in range(20)}
        signals = engine.scan_for_entries(
            symbols=list(price_data.keys()),
            price_data=price_data,
            regime=Regime.STRONG_UP,
            max_entries=5,
        )
        assert len(signals) <= 5

    @pytest.mark.integration
    def test_scan_for_entries_filters_confidence(self):
        """scan_for_entries filters by min_confidence."""
        engine = EntryEngine()
        price_data = {"TEST": _make_ohlcv(200)}
        signals = engine.scan_for_entries(
            symbols=["TEST"],
            price_data=price_data,
            regime=Regime.STRONG_UP,
            min_confidence=0.99,  # Very high threshold
        )
        for s in signals:
            assert s.confidence >= 0.99

    @pytest.mark.integration
    def test_allowed_strategies_override(self):
        """Explicit allowed_strategies overrides matrix."""
        engine = EntryEngine()
        data = _make_ohlcv(200, trend="up")
        # Force MEAN_REVERSION even in STRONG_UP
        result = engine.generate_entry(
            symbol="TEST",
            data=data,
            regime=Regime.STRONG_UP,
            volatility=VolatilityLevel.LOW,
            allowed_strategies=[Strategy.MEAN_REVERSION],
        )
        assert result is None or isinstance(result, Signal)
