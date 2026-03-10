"""
Tests for src/risk/ modules

Tier 1 - Unit Tests (Pure Logic, No External Dependencies)

Tests:
- CircuitBreaker: Trigger levels, cooldowns, reset
- DrawdownMonitor: Daily/weekly/total drawdown limits, recovery mode
- PositionLimits: Per-position risk checks
- PortfolioLimits: Portfolio-level concentration checks
- RiskManager: Combined risk checks (integration of sub-components)
"""

import pytest
from datetime import datetime, timedelta

from src.risk.circuit_breaker import CircuitBreaker, CircuitBreakerLevel, CircuitBreakerConfig
from src.risk.drawdown import DrawdownMonitor
from src.risk.position_limits import PositionLimits
from src.risk.portfolio_limits import PortfolioLimits


# =============================================================================
# CircuitBreaker
# =============================================================================

class TestCircuitBreaker:
    """Tests for the circuit breaker emergency stop system."""

    @pytest.mark.unit
    def test_initial_state(self):
        """Circuit breaker starts untriggered."""
        cb = CircuitBreaker()
        assert cb.is_triggered() is False
        assert cb.level == CircuitBreakerLevel.NONE

    @pytest.mark.unit
    def test_level_1_trigger(self):
        """1.5% loss triggers Level 1 (warning)."""
        cb = CircuitBreaker()
        level = cb.check(loss_pct=0.016)
        assert level == CircuitBreakerLevel.LEVEL_1
        assert cb.is_triggered() is True

    @pytest.mark.unit
    def test_level_2_trigger(self):
        """2.5% loss triggers Level 2 (reduce)."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.03)
        assert cb.level == CircuitBreakerLevel.LEVEL_2

    @pytest.mark.unit
    def test_level_3_trigger(self):
        """3.5% loss triggers Level 3 (halt)."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.04)
        assert cb.level == CircuitBreakerLevel.LEVEL_3
        assert cb.should_halt_trading() is True

    @pytest.mark.unit
    def test_level_4_trigger(self):
        """5% loss triggers Level 4 (close all)."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.06)
        assert cb.level == CircuitBreakerLevel.LEVEL_4
        assert cb.should_close_all() is True

    @pytest.mark.unit
    def test_no_trigger_below_threshold(self):
        """Loss below 1.5% does not trigger."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.01)
        assert cb.level == CircuitBreakerLevel.NONE

    @pytest.mark.unit
    def test_level_only_escalates(self):
        """Level can only go up, not down, without reset."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.04)  # Level 3
        cb.check(loss_pct=0.02)  # Would be Level 1, but shouldn't downgrade
        assert cb.level == CircuitBreakerLevel.LEVEL_3

    @pytest.mark.unit
    def test_reset(self):
        """Reset clears the circuit breaker."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.06)
        assert cb.is_triggered() is True
        cb.reset()
        assert cb.is_triggered() is False
        assert cb.level == CircuitBreakerLevel.NONE

    @pytest.mark.unit
    def test_position_scale_by_level(self):
        """Position scale factors correspond to levels."""
        cb = CircuitBreaker()
        assert cb.get_position_scale() == 1.0  # NONE

        cb.check(loss_pct=0.016)
        assert cb.get_position_scale() == 0.75  # LEVEL_1

    @pytest.mark.unit
    def test_get_status(self):
        """Status dict has expected keys."""
        cb = CircuitBreaker()
        status = cb.get_status()
        assert "triggered" in status
        assert "level" in status
        assert "level_name" in status
        assert status["triggered"] is False

    @pytest.mark.unit
    def test_trigger_history(self):
        """Trigger history records events."""
        cb = CircuitBreaker()
        cb.check(loss_pct=0.02)  # Level 1
        cb.check(loss_pct=0.04)  # Level 3
        history = cb.get_history()
        assert len(history) == 2
        assert history[0]["level"] == CircuitBreakerLevel.LEVEL_1
        assert history[1]["level"] == CircuitBreakerLevel.LEVEL_3


# =============================================================================
# DrawdownMonitor
# =============================================================================

class TestDrawdownMonitor:
    """Tests for drawdown monitoring."""

    @pytest.mark.unit
    def test_initial_state(self):
        """Monitor starts clean."""
        dm = DrawdownMonitor()
        assert dm.is_halted() is False
        assert dm.in_recovery() is False

    @pytest.mark.unit
    def test_daily_loss_halt(self):
        """Daily loss exceeding 3% halts trading."""
        dm = DrawdownMonitor(max_daily_loss=0.03)
        dm.update(pnl=-3500, capital=100_000)  # 3.5% daily loss
        assert dm.is_halted() is True
        status = dm.get_status()
        assert "Daily" in status["reason"]

    @pytest.mark.unit
    def test_weekly_loss_halt(self):
        """Cumulative weekly loss exceeding 6% halts trading."""
        dm = DrawdownMonitor(max_weekly_loss=0.06)
        # Accumulate losses across days
        dm.update(pnl=-2000, capital=100_000)
        dm.update(pnl=-2000, capital=98_000)
        dm.update(pnl=-2500, capital=96_000)
        assert dm.is_halted() is True

    @pytest.mark.unit
    def test_recovery_mode(self):
        """5% total drawdown triggers recovery mode."""
        dm = DrawdownMonitor(max_daily_loss=0.10)  # Raise daily limit so it doesn't halt first
        dm.update(pnl=0, capital=100_000)  # Set HWM to 100k
        dm.update(pnl=-5100, capital=94_900)  # 5.1% from HWM, but daily loss only 5.37%
        assert dm.in_recovery() is True

    @pytest.mark.unit
    def test_no_halt_within_limits(self):
        """Small losses don't halt."""
        dm = DrawdownMonitor(max_daily_loss=0.03)
        dm.update(pnl=-1000, capital=100_000)  # 1% loss
        assert dm.is_halted() is False

    @pytest.mark.unit
    def test_reset_daily(self):
        """Reset daily clears daily halt."""
        dm = DrawdownMonitor(max_daily_loss=0.03)
        dm.update(pnl=-4000, capital=100_000)
        assert dm.is_halted() is True
        dm.reset_daily()
        assert dm.is_halted() is False

    @pytest.mark.unit
    def test_reset_weekly(self):
        """Reset weekly clears weekly halt."""
        dm = DrawdownMonitor(max_weekly_loss=0.06)
        dm.update(pnl=-7000, capital=100_000)
        dm.reset_weekly()
        # The daily loss might still halt; reset daily too
        dm.reset_daily()
        assert dm.is_halted() is False

    @pytest.mark.unit
    def test_high_water_mark_updates(self):
        """HWM updates when capital increases."""
        dm = DrawdownMonitor()
        dm.update(pnl=0, capital=100_000)
        dm.update(pnl=5_000, capital=105_000)
        status = dm.get_status()
        assert status["high_water_mark"] == 105_000

    @pytest.mark.unit
    def test_reset_all(self):
        """Full reset reinitializes state."""
        dm = DrawdownMonitor()
        dm.update(pnl=-10_000, capital=90_000)
        dm.reset_all(new_capital=100_000)
        assert dm.is_halted() is False
        assert dm.in_recovery() is False


# =============================================================================
# PositionLimits
# =============================================================================

class TestPositionLimits:
    """Tests for per-position risk limits."""

    @pytest.mark.unit
    def test_within_limits(self):
        """Position within all limits passes."""
        pl = PositionLimits(max_position_pct=0.10, max_risk_pct=0.02, max_positions=20)
        result = pl.check(
            symbol="AAPL", size=8_000, risk=1_500,
            capital=100_000, current_positions=5,
        )
        assert result["passed"] is True

    @pytest.mark.unit
    def test_max_positions_reached(self):
        """At max positions -> fail."""
        pl = PositionLimits(max_positions=10)
        result = pl.check(
            symbol="AAPL", size=5_000, risk=1_000,
            capital=100_000, current_positions=10,
        )
        assert result["passed"] is False

    @pytest.mark.unit
    def test_size_exceeds_limit(self):
        """Size above limit provides adjustment but may still pass."""
        pl = PositionLimits(max_position_pct=0.10)
        result = pl.check(
            symbol="AAPL", size=15_000, risk=1_000,
            capital=100_000, current_positions=5,
        )
        assert "adjusted_size" in result

    @pytest.mark.unit
    def test_risk_exceeds_limit(self):
        """Risk above limit provides adjustment."""
        pl = PositionLimits(max_risk_pct=0.02)
        result = pl.check(
            symbol="AAPL", size=10_000, risk=3_000,
            capital=100_000, current_positions=5,
        )
        assert "adjusted_risk" in result
        assert result["adjusted_risk"] <= 2_000

    @pytest.mark.unit
    def test_get_max_size(self):
        pl = PositionLimits(max_position_pct=0.10)
        assert pl.get_max_size(100_000) == 10_000

    @pytest.mark.unit
    def test_get_max_risk(self):
        pl = PositionLimits(max_risk_pct=0.02)
        assert pl.get_max_risk(100_000) == 2_000


# =============================================================================
# PortfolioLimits
# =============================================================================

class TestPortfolioLimits:
    """Tests for portfolio-level risk limits."""

    @pytest.mark.unit
    def test_within_limits(self):
        """Trade within risk budget passes."""
        pl = PortfolioLimits(max_portfolio_risk=0.06)
        result = pl.check(new_risk=1_000, current_risk=2_000, capital=100_000)
        assert result["passed"] is True

    @pytest.mark.unit
    def test_exceeds_portfolio_risk(self):
        """Total risk after trade exceeds limit."""
        pl = PortfolioLimits(max_portfolio_risk=0.06)
        result = pl.check(new_risk=5_000, current_risk=2_000, capital=100_000)
        # Total would be 7000 > 6000, but may still pass with warnings
        assert len(result["reasons"]) > 0

    @pytest.mark.unit
    def test_no_available_risk(self):
        """Current risk already at max -> fail."""
        pl = PortfolioLimits(max_portfolio_risk=0.06)
        result = pl.check(new_risk=1_000, current_risk=6_000, capital=100_000)
        assert result["passed"] is False

    @pytest.mark.unit
    def test_sector_check(self):
        """Sector concentration check."""
        pl = PortfolioLimits(max_sector_exposure=0.30)
        result = pl.check_sector(sector="Tech", new_exposure=35_000, capital=100_000)
        assert result["passed"] is False

    @pytest.mark.unit
    def test_sector_within_limit(self):
        """Sector within limit passes."""
        pl = PortfolioLimits(max_sector_exposure=0.30)
        result = pl.check_sector(sector="Tech", new_exposure=20_000, capital=100_000)
        assert result["passed"] is True

    @pytest.mark.unit
    def test_get_available_risk(self):
        """Available risk = max - current."""
        pl = PortfolioLimits(max_portfolio_risk=0.06)
        available = pl.get_available_risk(current_risk=2_000, capital=100_000)
        assert available == pytest.approx(4_000, abs=1)

    @pytest.mark.unit
    def test_add_and_remove_sector_exposure(self):
        """Sector tracking updates correctly."""
        pl = PortfolioLimits()
        pl.add_sector_exposure("Tech", 10_000)
        pl.add_sector_exposure("Tech", 5_000)
        summary = pl.get_sector_summary(100_000)
        assert summary["Tech"] == pytest.approx(15.0, abs=0.1)

        pl.remove_sector_exposure("Tech", 5_000)
        summary = pl.get_sector_summary(100_000)
        assert summary["Tech"] == pytest.approx(10.0, abs=0.1)
