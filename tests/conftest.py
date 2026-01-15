"""
Pytest configuration and shared fixtures.

This module provides test fixtures used across all test modules.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.core.types import (
    OHLCV, Signal, SignalType, SignalStrength, Order, OrderSide, OrderType,
    Position, Trade, PickScore, Regime, VolatilityLevel, Strategy
)
from src.core.events import EventBus, reset_event_bus
from src.core.config import SystemConfig, reset_config


# =============================================================================
# FIXTURES: CONFIGURATION
# =============================================================================

@pytest.fixture
def test_config() -> SystemConfig:
    """Create test configuration with safe defaults."""
    config = SystemConfig()
    config.mode = "backtest"
    config.risk.max_position_pct = 0.10
    config.risk.max_positions = 10
    config.risk.max_drawdown = 0.10
    config.sizing.base_size_pct = 0.05
    config.backtest.initial_capital = 100000
    return config


@pytest.fixture
def event_bus() -> EventBus:
    """Create fresh event bus for each test."""
    reset_event_bus()
    return EventBus()


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state before each test."""
    reset_event_bus()
    reset_config()
    yield
    reset_event_bus()
    reset_config()


# =============================================================================
# FIXTURES: MARKET DATA
# =============================================================================

@pytest.fixture
def trending_up_data() -> pd.DataFrame:
    """
    Create 200 days of uptrending data.
    Simulates a stock in strong uptrend with 50% gain.
    """
    n_bars = 200  # Enough for 6M momentum (126 bars)
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    # Generate price series with uptrend
    np.random.seed(42)
    base = 100
    returns = np.random.normal(0.003, 0.015, n_bars)  # Slight positive drift
    prices = base * np.exp(np.cumsum(returns))
    
    # Add some structure - 60% gain over period
    prices = prices * (1 + np.linspace(0, 0.6, n_bars))
    
    # Create OHLC
    data = pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.005, 0.015, n_bars)),
        'low': prices * (1 - np.random.uniform(0.005, 0.015, n_bars)),
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, n_bars)
    }, index=dates)
    
    return data


@pytest.fixture
def sideways_data() -> pd.DataFrame:
    """
    Create 200 days of sideways/ranging data.
    Simulates a stock oscillating in a range.
    """
    n_bars = 200
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    np.random.seed(42)
    base = 100
    
    # Oscillating pattern
    cycle = np.sin(np.linspace(0, 8 * np.pi, n_bars)) * 5
    noise = np.random.normal(0, 2, n_bars)
    prices = base + cycle + noise
    
    data = pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.005, 0.010, n_bars)),
        'low': prices * (1 - np.random.uniform(0.005, 0.010, n_bars)),
        'close': prices,
        'volume': np.random.randint(800000, 2000000, n_bars)
    }, index=dates)
    
    return data


@pytest.fixture
def downtrending_data() -> pd.DataFrame:
    """
    Create 200 days of downtrending data.
    Simulates a stock in clear downtrend with 35% loss.
    """
    n_bars = 200  # Enough for 6M momentum
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    np.random.seed(42)
    base = 100
    returns = np.random.normal(-0.002, 0.018, n_bars)
    prices = base * np.exp(np.cumsum(returns))
    
    # Add downtrend structure - 35% loss
    prices = prices * (1 - np.linspace(0, 0.35, n_bars))
    
    data = pd.DataFrame({
        'open': prices * (1 + np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.005, 0.015, n_bars)),
        'low': prices * (1 - np.random.uniform(0.005, 0.015, n_bars)),
        'close': prices,
        'volume': np.random.randint(1500000, 4000000, n_bars)
    }, index=dates)
    
    return data


@pytest.fixture
def volatile_data() -> pd.DataFrame:
    """
    Create 200 days of high volatility data.
    Simulates a volatile stock with large swings.
    """
    n_bars = 200
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    np.random.seed(42)
    base = 100
    
    # High volatility returns
    returns = np.random.normal(0.001, 0.04, n_bars)
    prices = base * np.exp(np.cumsum(returns))
    
    # Larger daily ranges
    data = pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.01, 0.02, n_bars)),
        'high': prices * (1 + np.random.uniform(0.02, 0.05, n_bars)),
        'low': prices * (1 - np.random.uniform(0.02, 0.05, n_bars)),
        'close': prices,
        'volume': np.random.randint(2000000, 8000000, n_bars)
    }, index=dates)
    
    return data


@pytest.fixture
def consolidation_data() -> pd.DataFrame:
    """
    Create 200 days showing rally then consolidation.
    Simulates a stock forming a base after a move.
    """
    n_bars = 200
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    np.random.seed(42)
    prices = []
    base = 100
    
    # Rally phase (days 0-100): +40%
    for i in range(100):
        base = base * (1 + np.random.uniform(0.002, 0.006))
        prices.append(base)
    
    # Consolidation phase (days 100-200): +/- 3%
    consolidation_base = base
    for i in range(100):
        price = consolidation_base * (1 + np.random.uniform(-0.02, 0.02))
        prices.append(price)
    
    prices = np.array(prices)
    
    data = pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.003, 0.008, n_bars)),
        'low': prices * (1 - np.random.uniform(0.003, 0.008, n_bars)),
        'close': prices,
        'volume': np.random.randint(1000000, 3000000, n_bars)
    }, index=dates)
    
    return data


@pytest.fixture
def multi_symbol_data(
    trending_up_data, 
    sideways_data, 
    downtrending_data,
    volatile_data
) -> Dict[str, pd.DataFrame]:
    """Create data for multiple symbols with different characteristics."""
    return {
        'TREND': trending_up_data,
        'RANGE': sideways_data,
        'DOWN': downtrending_data,
        'VOLATILE': volatile_data
    }


# =============================================================================
# FIXTURES: SIGNALS
# =============================================================================

@pytest.fixture
def entry_signal() -> Signal:
    """Create a typical entry signal."""
    return Signal(
        symbol="NVDA",
        signal_type=SignalType.ENTRY_LONG,
        strength=SignalStrength.STRONG,
        timestamp=datetime.now(),
        score=85.0,
        confidence=0.8,
        entry_price=500.0,
        stop_price=475.0,
        target_price=550.0,
        entry_zone_low=495.0,
        entry_zone_high=505.0,
        regime=Regime.STRONG_UP,
        strategy=Strategy.TREND_FOLLOWING,
        reasoning="Strong momentum, pullback to EMA21",
        factors={
            'momentum': 85,
            'volume': 80,
            'technical': 90,
            'rs_vs_spy': 75
        }
    )


@pytest.fixture
def exit_signal() -> Signal:
    """Create a typical exit signal."""
    return Signal(
        symbol="NVDA",
        signal_type=SignalType.EXIT_LONG,
        strength=SignalStrength.MODERATE,
        timestamp=datetime.now(),
        score=70.0,
        confidence=0.7,
        reasoning="Trend weakening, RSI overbought"
    )


# =============================================================================
# FIXTURES: POSITIONS
# =============================================================================

@pytest.fixture
def open_position() -> Position:
    """Create a typical open position."""
    return Position(
        symbol="NVDA",
        quantity=100,
        avg_entry_price=500.0,
        stop_price=475.0,
        target_price=550.0,
        regime=Regime.STRONG_UP,
        strategy=Strategy.TREND_FOLLOWING,
        entry_date=datetime.now() - timedelta(days=5),
        initial_quantity=100,
        entry_reason="Entry signal score 85",
        signal_score=85.0,
        current_price=510.0,
        unrealized_pnl=1000.0,
        unrealized_pnl_pct=2.0
    )


@pytest.fixture
def profitable_position() -> Position:
    """Create a profitable position."""
    pos = Position(
        symbol="PLTR",
        quantity=200,
        avg_entry_price=25.0,
        stop_price=22.5,
        target_price=30.0,
        entry_date=datetime.now() - timedelta(days=15),
        initial_quantity=200,
        entry_reason="Breakout entry",
        signal_score=80.0
    )
    pos.update_price(28.0)  # 12% profit
    return pos


@pytest.fixture
def losing_position() -> Position:
    """Create a losing position."""
    pos = Position(
        symbol="MSTR",
        quantity=50,
        avg_entry_price=400.0,
        stop_price=360.0,
        target_price=480.0,
        entry_date=datetime.now() - timedelta(days=10),
        initial_quantity=50,
        entry_reason="Momentum entry",
        signal_score=70.0
    )
    pos.update_price(380.0)  # 5% loss
    return pos


# =============================================================================
# FIXTURES: TRADES
# =============================================================================

@pytest.fixture
def winning_trade() -> Trade:
    """Create a winning trade."""
    return Trade(
        trade_id="T001",
        symbol="NVDA",
        side="LONG",
        entry_date=datetime.now() - timedelta(days=20),
        entry_price=480.0,
        entry_quantity=100,
        entry_reason="Trend entry",
        exit_date=datetime.now() - timedelta(days=5),
        exit_price=520.0,
        exit_quantity=100,
        exit_reason="Target hit",
        pnl=4000.0,
        pnl_pct=8.33,
        holding_days=15,
        regime=Regime.STRONG_UP,
        strategy=Strategy.TREND_FOLLOWING,
        signal_score=85.0,
        commission=10.0,
        slippage=5.0
    )


@pytest.fixture
def losing_trade() -> Trade:
    """Create a losing trade."""
    return Trade(
        trade_id="T002",
        symbol="COIN",
        side="LONG",
        entry_date=datetime.now() - timedelta(days=10),
        entry_price=150.0,
        entry_quantity=50,
        entry_reason="Pullback entry",
        exit_date=datetime.now() - timedelta(days=2),
        exit_price=135.0,
        exit_quantity=50,
        exit_reason="Stop loss",
        pnl=-750.0,
        pnl_pct=-10.0,
        holding_days=8,
        regime=Regime.SIDEWAYS,
        strategy=Strategy.SWING_TRADE,
        signal_score=65.0,
        commission=8.0,
        slippage=4.0
    )


# =============================================================================
# FIXTURES: PICKS
# =============================================================================

@pytest.fixture
def strong_pick() -> PickScore:
    """Create a high-scoring stock pick."""
    return PickScore(
        symbol="NVDA",
        timestamp=datetime.now(),
        total_score=88.0,
        grade="A",
        rank=1,
        momentum_score=92,
        relative_strength_score=90,
        quality_score=85,
        value_score=70,
        technical_score=88,
        volume_score=85,
        momentum_6m=75.0,
        momentum_3m=35.0,
        rs_vs_spy=45.0,
        volatility=45.0,
        rsi=55.0,
        dist_from_high=5.0,
        regime=Regime.STRONG_UP,
        volatility_level=VolatilityLevel.MODERATE,
        strategy=Strategy.TREND_FOLLOWING,
        entry_zone_low=485.0,
        entry_zone_high=495.0,
        stop_loss=460.0,
        target_1=530.0,
        target_2=560.0,
        win_rate=0.65,
        avg_win=12.0,
        avg_loss=6.0,
        expected_value=5.0,
        risk_reward=2.8,
        position_size_pct=8.0,
        kelly_pct=15.0,
        pattern="TIGHT_CONSOLIDATION",
        catalysts=["AI demand", "Earnings beat"],
        reasoning="Strong momentum leader with consolidation breakout setup"
    )


@pytest.fixture
def weak_pick() -> PickScore:
    """Create a low-scoring stock pick."""
    return PickScore(
        symbol="INTC",
        timestamp=datetime.now(),
        total_score=45.0,
        grade="D",
        rank=50,
        momentum_score=30,
        relative_strength_score=25,
        quality_score=55,
        value_score=75,
        technical_score=40,
        volume_score=35,
        momentum_6m=-15.0,
        momentum_3m=-8.0,
        rs_vs_spy=-25.0,
        volatility=35.0,
        rsi=42.0,
        dist_from_high=35.0,
        regime=Regime.SIDEWAYS,
        volatility_level=VolatilityLevel.MODERATE,
        strategy=Strategy.MEAN_REVERSION,
        entry_zone_low=38.0,
        entry_zone_high=40.0,
        stop_loss=36.0,
        target_1=44.0,
        target_2=48.0,
        win_rate=0.45,
        avg_win=8.0,
        avg_loss=7.0,
        expected_value=-0.5,
        risk_reward=1.2,
        position_size_pct=3.0,
        kelly_pct=2.0,
        pattern="NO_PATTERN",
        catalysts=[],
        reasoning="Weak momentum, underperforming SPY"
    )


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_ohlcv_series(
    n_bars: int = 100,
    start_price: float = 100.0,
    trend: str = "up",
    volatility: float = 0.02,
    seed: int = 42
) -> pd.DataFrame:
    """
    Create OHLCV data with specified characteristics.
    
    Args:
        n_bars: Number of bars to generate
        start_price: Starting price
        trend: "up", "down", or "sideways"
        volatility: Daily volatility (e.g., 0.02 = 2%)
        seed: Random seed for reproducibility
        
    Returns:
        DataFrame with OHLCV data
    """
    np.random.seed(seed)
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    # Set drift based on trend
    if trend == "up":
        drift = 0.001
    elif trend == "down":
        drift = -0.001
    else:
        drift = 0.0
    
    returns = np.random.normal(drift, volatility, n_bars)
    prices = start_price * np.exp(np.cumsum(returns))
    
    data = pd.DataFrame({
        'open': prices * (1 - np.random.uniform(0.002, 0.005, n_bars)),
        'high': prices * (1 + np.random.uniform(0.005, 0.015, n_bars)),
        'low': prices * (1 - np.random.uniform(0.005, 0.015, n_bars)),
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, n_bars)
    }, index=dates)
    
    return data
