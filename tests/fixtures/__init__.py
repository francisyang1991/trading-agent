"""
Test fixtures for market data and scenarios.
"""

from .market_data import (
    MarketDataGenerator,
    create_trend_data,
    create_range_data,
    create_volatile_data,
    create_consolidation_data,
    create_reversal_data,
    create_parabolic_data,
)

from .scenarios import (
    create_bull_market_scenario,
    create_bear_market_scenario,
    create_choppy_market_scenario,
    create_crash_scenario,
    create_recovery_scenario,
)

__all__ = [
    'MarketDataGenerator',
    'create_trend_data',
    'create_range_data',
    'create_volatile_data',
    'create_consolidation_data',
    'create_reversal_data',
    'create_parabolic_data',
    'create_bull_market_scenario',
    'create_bear_market_scenario',
    'create_choppy_market_scenario',
    'create_crash_scenario',
    'create_recovery_scenario',
]
