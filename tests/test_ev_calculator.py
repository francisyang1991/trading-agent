"""
Tests for src/strategy/ev_calculator.py

Tier 1 - Unit Tests (Pure Logic, No External Dependencies)

Tests:
- calculate_ev: Expected Value computation
- calculate_kelly: Kelly Criterion position sizing
- calculate_half_kelly: Half-Kelly conservative sizing
- estimate_distribution: Forward return distribution from price data
- calculate_risk_reward: Risk/reward ratio
- calculate_position_size: Position sizing from risk parameters
- is_positive_ev / meets_minimum_criteria: Threshold helpers
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.strategy.ev_calculator import (
    calculate_ev,
    calculate_kelly,
    calculate_half_kelly,
    estimate_distribution,
    calculate_risk_reward,
    calculate_position_size,
    is_positive_ev,
    meets_minimum_criteria,
)


# =============================================================================
# calculate_ev
# =============================================================================

class TestCalculateEV:
    """Tests for Expected Value calculation."""

    @pytest.mark.unit
    def test_positive_ev(self):
        """55% win rate with 5% avg win and 3% avg loss -> positive EV."""
        ev = calculate_ev(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
        # EV = 0.55*5.0 - 0.45*3.0 = 2.75 - 1.35 = 1.40
        assert ev == pytest.approx(1.40, abs=0.01)

    @pytest.mark.unit
    def test_negative_ev(self):
        """40% win rate with equal win/loss -> negative EV."""
        ev = calculate_ev(win_rate=0.40, avg_win=5.0, avg_loss=5.0)
        # EV = 0.40*5.0 - 0.60*5.0 = 2.0 - 3.0 = -1.0
        assert ev == pytest.approx(-1.0, abs=0.01)

    @pytest.mark.unit
    def test_breakeven_ev(self):
        """50% win rate with equal win/loss -> zero EV."""
        ev = calculate_ev(win_rate=0.50, avg_win=5.0, avg_loss=5.0)
        assert ev == pytest.approx(0.0, abs=0.01)

    @pytest.mark.unit
    def test_perfect_win_rate(self):
        """100% win rate -> EV equals avg_win."""
        ev = calculate_ev(win_rate=1.0, avg_win=10.0, avg_loss=5.0)
        assert ev == pytest.approx(10.0, abs=0.01)

    @pytest.mark.unit
    def test_zero_win_rate(self):
        """0% win rate -> EV equals negative avg_loss."""
        ev = calculate_ev(win_rate=0.0, avg_win=10.0, avg_loss=5.0)
        assert ev == pytest.approx(-5.0, abs=0.01)

    @pytest.mark.unit
    def test_zero_avg_loss(self):
        """Zero average loss -> EV is just win contribution."""
        ev = calculate_ev(win_rate=0.6, avg_win=5.0, avg_loss=0.0)
        assert ev == pytest.approx(3.0, abs=0.01)


# =============================================================================
# calculate_kelly
# =============================================================================

class TestCalculateKelly:
    """Tests for Kelly Criterion calculation."""

    @pytest.mark.unit
    def test_kelly_basic(self):
        """Standard case: 55% WR, 5/3 W/L ratio."""
        kelly = calculate_kelly(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
        # b = 5/3, q = 0.45
        # kelly = (0.55 * 5/3 - 0.45) / (5/3) = (0.9167 - 0.45) / 1.6667 = 0.28
        # Capped at 0.25
        assert kelly == pytest.approx(0.25, abs=0.01)

    @pytest.mark.unit
    def test_kelly_negative_edge(self):
        """Negative edge should return 0."""
        kelly = calculate_kelly(win_rate=0.30, avg_win=2.0, avg_loss=3.0)
        assert kelly == 0.0

    @pytest.mark.unit
    def test_kelly_zero_avg_loss(self):
        """Zero avg_loss should return 0 (division safety)."""
        kelly = calculate_kelly(win_rate=0.55, avg_win=5.0, avg_loss=0.0)
        assert kelly == 0.0

    @pytest.mark.unit
    def test_kelly_capped_at_25_pct(self):
        """Kelly should never exceed 25%."""
        kelly = calculate_kelly(win_rate=0.90, avg_win=10.0, avg_loss=1.0)
        assert kelly <= 0.25

    @pytest.mark.unit
    def test_kelly_floor_at_zero(self):
        """Kelly should never go below 0."""
        kelly = calculate_kelly(win_rate=0.10, avg_win=1.0, avg_loss=10.0)
        assert kelly >= 0.0


# =============================================================================
# calculate_half_kelly
# =============================================================================

class TestCalculateHalfKelly:
    """Tests for Half-Kelly sizing."""

    @pytest.mark.unit
    def test_half_kelly_is_half(self):
        """Half-Kelly should be exactly half of full Kelly."""
        full = calculate_kelly(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
        half = calculate_half_kelly(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
        assert half == pytest.approx(full / 2, abs=0.001)

    @pytest.mark.unit
    def test_half_kelly_max(self):
        """Half-Kelly max is 12.5%."""
        half = calculate_half_kelly(win_rate=0.90, avg_win=10.0, avg_loss=1.0)
        assert half <= 0.125


# =============================================================================
# estimate_distribution
# =============================================================================

class TestEstimateDistribution:
    """Tests for distribution estimation from historical data."""

    @pytest.mark.unit
    def test_basic_distribution(self):
        """Estimate distribution from trending data with enough samples."""
        n = 200
        dates = pd.date_range(end=datetime.now(), periods=n, freq="D")
        np.random.seed(42)
        prices = 100 * np.exp(np.cumsum(np.random.normal(0.002, 0.015, n)))
        df = pd.DataFrame({"Close": prices}, index=dates)

        dist = estimate_distribution(df, forward_days=5)

        assert "win_rate" in dist
        assert "avg_win" in dist
        assert "avg_loss" in dist
        assert 0.0 <= dist["win_rate"] <= 1.0
        assert dist["avg_win"] >= 0.0
        assert dist["avg_loss"] >= 0.0

    @pytest.mark.unit
    def test_insufficient_data(self):
        """Less than 50 data points returns neutral defaults."""
        dates = pd.date_range(end=datetime.now(), periods=30, freq="D")
        prices = np.linspace(100, 110, 30)
        df = pd.DataFrame({"Close": prices}, index=dates)

        dist = estimate_distribution(df, forward_days=5)

        assert dist["win_rate"] == 0.5
        assert dist["avg_win"] == 5.0
        assert dist["avg_loss"] == 5.0

    @pytest.mark.unit
    def test_lowercase_close_column(self):
        """Should handle lowercase 'close' column."""
        n = 100
        dates = pd.date_range(end=datetime.now(), periods=n, freq="D")
        np.random.seed(42)
        prices = 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, n)))
        df = pd.DataFrame({"close": prices}, index=dates)

        dist = estimate_distribution(df, forward_days=5, close_col="close")
        assert "win_rate" in dist


# =============================================================================
# calculate_risk_reward
# =============================================================================

class TestCalculateRiskReward:
    """Tests for risk/reward ratio calculation."""

    @pytest.mark.unit
    def test_basic_rr(self):
        """Entry=100, Stop=95, Target=110 -> R:R = 2.0."""
        rr = calculate_risk_reward(entry=100, stop_loss=95, target=110)
        assert rr == pytest.approx(2.0, abs=0.01)

    @pytest.mark.unit
    def test_1_to_1_rr(self):
        """Equal risk and reward -> 1:1."""
        rr = calculate_risk_reward(entry=100, stop_loss=95, target=105)
        assert rr == pytest.approx(1.0, abs=0.01)

    @pytest.mark.unit
    def test_zero_risk(self):
        """Entry == stop_loss -> 0 risk -> return 0."""
        rr = calculate_risk_reward(entry=100, stop_loss=100, target=110)
        assert rr == 0.0

    @pytest.mark.unit
    def test_negative_risk(self):
        """Stop above entry (invalid) -> return 0."""
        rr = calculate_risk_reward(entry=100, stop_loss=105, target=110)
        assert rr == 0.0

    @pytest.mark.unit
    def test_high_rr(self):
        """Tight stop, wide target -> high R:R."""
        rr = calculate_risk_reward(entry=100, stop_loss=99, target=110)
        assert rr == pytest.approx(10.0, abs=0.01)


# =============================================================================
# calculate_position_size
# =============================================================================

class TestCalculatePositionSize:
    """Tests for position sizing."""

    @pytest.mark.unit
    def test_basic_position_size(self):
        """$100k account, $50 stock, $2.50 stop distance, 2% risk."""
        shares, value = calculate_position_size(
            account_value=100_000,
            entry=50.0,
            stop_loss=47.5,
            risk_per_trade_pct=0.02,
        )
        # Max risk = $2,000, risk per share = $2.50
        # Shares from risk = 800
        # Max position = $15,000 -> 300 shares
        # min(800, 300) = 300
        assert shares == 300
        assert value == pytest.approx(15_000.0, abs=1.0)

    @pytest.mark.unit
    def test_position_size_zero_risk(self):
        """Stop at entry -> zero shares."""
        shares, value = calculate_position_size(
            account_value=100_000, entry=50.0, stop_loss=50.0
        )
        assert shares == 0
        assert value == 0.0

    @pytest.mark.unit
    def test_position_size_stop_above_entry(self):
        """Invalid stop above entry -> zero."""
        shares, value = calculate_position_size(
            account_value=100_000, entry=50.0, stop_loss=55.0
        )
        assert shares == 0
        assert value == 0.0

    @pytest.mark.unit
    def test_position_size_respects_max_position(self):
        """Position should not exceed max_position_pct."""
        shares, value = calculate_position_size(
            account_value=100_000,
            entry=10.0,
            stop_loss=9.0,
            risk_per_trade_pct=0.10,  # 10% risk, very large
            max_position_pct=0.15,
        )
        assert value <= 100_000 * 0.15 + 10  # Allow rounding


# =============================================================================
# is_positive_ev / meets_minimum_criteria
# =============================================================================

class TestHelpers:
    """Tests for helper functions."""

    @pytest.mark.unit
    def test_is_positive_ev_true(self):
        assert is_positive_ev(0.55, 5.0, 3.0) is True

    @pytest.mark.unit
    def test_is_positive_ev_false(self):
        assert is_positive_ev(0.30, 2.0, 5.0) is False

    @pytest.mark.unit
    def test_meets_minimum_criteria_pass(self):
        assert meets_minimum_criteria(ev=1.0, risk_reward=2.0) is True

    @pytest.mark.unit
    def test_meets_minimum_criteria_fail_ev(self):
        assert meets_minimum_criteria(ev=0.3, risk_reward=2.0) is False

    @pytest.mark.unit
    def test_meets_minimum_criteria_fail_rr(self):
        assert meets_minimum_criteria(ev=1.0, risk_reward=1.0) is False

    @pytest.mark.unit
    def test_meets_minimum_criteria_custom_thresholds(self):
        assert meets_minimum_criteria(
            ev=0.3, risk_reward=1.2, ev_threshold=0.2, rr_threshold=1.0
        ) is True
