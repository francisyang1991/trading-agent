"""
Strategy Matrix
===============

Maps (Regime, VolCategory) combinations to optimal trading strategies.

The matrix is derived from backtesting results documented in 
docs/STRATEGY_ITERATION.md.

Usage:
    from src.strategy import get_strategy, Strategy
    from src.regime import Regime, VolCategory
    
    strategy, params = get_strategy(Regime.STRONG_UP, VolCategory.MODERATE)
    print(f"Use {strategy.value} with {params.position_size_pct}% position")
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

from ..regime import Regime, VolCategory


class Strategy(Enum):
    """Trading strategy types."""
    BUY_HOLD = "Buy & Hold"
    TRAILING_STOP = "Trailing Stop"
    TREND_FOLLOWING = "Trend Following"
    SWING_TRADE = "Swing Trade"
    MEAN_REVERSION = "Mean Reversion"
    STAY_CASH = "Stay Cash"


@dataclass
class StrategyParams:
    """Parameters for a strategy based on regime/volatility."""
    strategy: Strategy
    position_size_pct: float  # Base position size as percentage (e.g., 0.10 = 10%)
    stop_loss_pct: float      # Stop loss as percentage below entry
    take_profit_pct: float    # Take profit as percentage above entry
    
    def __post_init__(self):
        # Validate ranges
        assert 0 <= self.position_size_pct <= 1, "Position size must be 0-1"
        assert 0 <= self.stop_loss_pct <= 1, "Stop loss must be 0-1"
        assert 0 <= self.take_profit_pct <= 2, "Take profit must be 0-2"


# Strategy Matrix: (Regime, VolCategory) -> StrategyParams
# Based on backtesting from docs/STRATEGY_ITERATION.md
STRATEGY_MATRIX: Dict[Tuple[Regime, VolCategory], StrategyParams] = {
    # PARABOLIC regime (>100% 6M momentum)
    # Key insight: Don't fight the trend, but be ready to exit
    (Regime.PARABOLIC, VolCategory.LOW): StrategyParams(
        Strategy.BUY_HOLD, 0.15, 0.10, 0.50
    ),
    (Regime.PARABOLIC, VolCategory.MODERATE): StrategyParams(
        Strategy.TRAILING_STOP, 0.12, 0.12, 0.40
    ),
    (Regime.PARABOLIC, VolCategory.HIGH): StrategyParams(
        Strategy.TRAILING_STOP, 0.08, 0.15, 0.35
    ),
    (Regime.PARABOLIC, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.TRAILING_STOP, 0.05, 0.20, 0.30
    ),
    
    # STRONG_UP regime (50-100% 6M momentum)
    # Key insight: Trend following works well, swing trade for high vol
    (Regime.STRONG_UP, VolCategory.LOW): StrategyParams(
        Strategy.TREND_FOLLOWING, 0.15, 0.08, 0.25
    ),
    (Regime.STRONG_UP, VolCategory.MODERATE): StrategyParams(
        Strategy.TREND_FOLLOWING, 0.12, 0.10, 0.25
    ),
    (Regime.STRONG_UP, VolCategory.HIGH): StrategyParams(
        Strategy.SWING_TRADE, 0.08, 0.12, 0.20
    ),
    (Regime.STRONG_UP, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.SWING_TRADE, 0.05, 0.15, 0.20
    ),
    
    # MODERATE_UP regime (20-50% 6M momentum)
    # Key insight: Trend following still works, swing for high vol
    (Regime.MODERATE_UP, VolCategory.LOW): StrategyParams(
        Strategy.TREND_FOLLOWING, 0.15, 0.06, 0.15
    ),
    (Regime.MODERATE_UP, VolCategory.MODERATE): StrategyParams(
        Strategy.TREND_FOLLOWING, 0.12, 0.08, 0.15
    ),
    (Regime.MODERATE_UP, VolCategory.HIGH): StrategyParams(
        Strategy.SWING_TRADE, 0.08, 0.10, 0.15
    ),
    (Regime.MODERATE_UP, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.SWING_TRADE, 0.05, 0.12, 0.15
    ),
    
    # WEAK_UP regime (0-20% 6M momentum)
    # Key insight: Be selective, swing trade or mean reversion
    (Regime.WEAK_UP, VolCategory.LOW): StrategyParams(
        Strategy.SWING_TRADE, 0.12, 0.05, 0.10
    ),
    (Regime.WEAK_UP, VolCategory.MODERATE): StrategyParams(
        Strategy.SWING_TRADE, 0.10, 0.06, 0.10
    ),
    (Regime.WEAK_UP, VolCategory.HIGH): StrategyParams(
        Strategy.MEAN_REVERSION, 0.08, 0.08, 0.10
    ),
    (Regime.WEAK_UP, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.MEAN_REVERSION, 0.05, 0.10, 0.10
    ),
    
    # SIDEWAYS regime (-20% to 0% 6M momentum)
    # Key insight: Mean reversion works, but be very selective
    (Regime.SIDEWAYS, VolCategory.LOW): StrategyParams(
        Strategy.MEAN_REVERSION, 0.12, 0.04, 0.08
    ),
    (Regime.SIDEWAYS, VolCategory.MODERATE): StrategyParams(
        Strategy.MEAN_REVERSION, 0.10, 0.05, 0.08
    ),
    (Regime.SIDEWAYS, VolCategory.HIGH): StrategyParams(
        Strategy.MEAN_REVERSION, 0.06, 0.08, 0.10
    ),
    (Regime.SIDEWAYS, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.STAY_CASH, 0.00, 0.00, 0.00
    ),
    
    # DOWNTREND regime (<-20% 6M momentum)
    # Key insight: Stay in cash - don't fight the trend
    (Regime.DOWNTREND, VolCategory.LOW): StrategyParams(
        Strategy.STAY_CASH, 0.00, 0.00, 0.00
    ),
    (Regime.DOWNTREND, VolCategory.MODERATE): StrategyParams(
        Strategy.STAY_CASH, 0.00, 0.00, 0.00
    ),
    (Regime.DOWNTREND, VolCategory.HIGH): StrategyParams(
        Strategy.STAY_CASH, 0.00, 0.00, 0.00
    ),
    (Regime.DOWNTREND, VolCategory.ULTRA_HIGH): StrategyParams(
        Strategy.STAY_CASH, 0.00, 0.00, 0.00
    ),
}


def get_strategy(
    regime: Regime,
    vol_category: VolCategory
) -> Tuple[Strategy, StrategyParams]:
    """
    Get optimal strategy and parameters for regime/volatility combination.
    
    Args:
        regime: Market regime
        vol_category: Volatility category
        
    Returns:
        Tuple of (Strategy enum, StrategyParams)
        
    Example:
        >>> strategy, params = get_strategy(Regime.STRONG_UP, VolCategory.MODERATE)
        >>> print(f"Use {strategy.value}")
        >>> print(f"Position: {params.position_size_pct:.0%}")
        >>> print(f"Stop: {params.stop_loss_pct:.0%}")
    """
    params = STRATEGY_MATRIX.get(
        (regime, vol_category),
        StrategyParams(Strategy.STAY_CASH, 0.00, 0.00, 0.00)
    )
    return params.strategy, params


def is_tradeable_strategy(strategy: Strategy) -> bool:
    """Check if strategy allows trading (not STAY_CASH)."""
    return strategy != Strategy.STAY_CASH


def get_all_strategies_for_regime(regime: Regime) -> Dict[VolCategory, StrategyParams]:
    """Get all strategy configurations for a given regime."""
    result = {}
    for vol_cat in VolCategory:
        params = STRATEGY_MATRIX.get((regime, vol_cat))
        if params:
            result[vol_cat] = params
    return result
