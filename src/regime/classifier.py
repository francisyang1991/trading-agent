"""
Market Regime Classification
============================

Classifies stock momentum regimes based on 6-month momentum.

Regimes:
- PARABOLIC: >100% 6M momentum (rare, extreme moves)
- STRONG_UP: 50-100% 6M momentum (strong uptrend)
- MODERATE_UP: 20-50% 6M momentum (healthy uptrend)
- WEAK_UP: 0-20% 6M momentum (mild uptrend)
- SIDEWAYS: -20% to 0% 6M momentum (consolidation)
- DOWNTREND: <-20% 6M momentum (avoid)

Usage:
    from src.regime import Regime, classify_regime
    
    regime = classify_regime(momentum_6m=45.0)  # Returns Regime.MODERATE_UP
    
    # With price data
    regime = classify_regime_from_prices(close_series)
"""

from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np


class Regime(Enum):
    """Market regime classification based on 6-month momentum."""
    PARABOLIC = "PARABOLIC"      # >100% 6M momentum
    STRONG_UP = "STRONG_UP"      # 50-100%
    MODERATE_UP = "MODERATE_UP"  # 20-50%
    WEAK_UP = "WEAK_UP"          # 0-20%
    SIDEWAYS = "SIDEWAYS"        # -20% to 0%
    DOWNTREND = "DOWNTREND"      # <-20%


# Regime thresholds (momentum percentages)
REGIME_THRESHOLDS = {
    Regime.PARABOLIC: (100, float('inf')),
    Regime.STRONG_UP: (50, 100),
    Regime.MODERATE_UP: (20, 50),
    Regime.WEAK_UP: (0, 20),
    Regime.SIDEWAYS: (-20, 0),
    Regime.DOWNTREND: (float('-inf'), -20),
}


def classify_regime(momentum_6m: float) -> Regime:
    """
    Classify regime based on 6-month momentum percentage.
    
    Args:
        momentum_6m: 6-month return as a percentage (e.g., 45.0 for +45%)
        
    Returns:
        Regime enum value
        
    Example:
        >>> classify_regime(150.0)
        Regime.PARABOLIC
        >>> classify_regime(45.0)
        Regime.MODERATE_UP
        >>> classify_regime(-25.0)
        Regime.DOWNTREND
    """
    if momentum_6m > 100:
        return Regime.PARABOLIC
    elif momentum_6m > 50:
        return Regime.STRONG_UP
    elif momentum_6m > 20:
        return Regime.MODERATE_UP
    elif momentum_6m > 0:
        return Regime.WEAK_UP
    elif momentum_6m > -20:
        return Regime.SIDEWAYS
    else:
        return Regime.DOWNTREND


def classify_regime_from_prices(close: pd.Series, lookback_days: int = 126) -> Regime:
    """
    Classify regime from a price series.
    
    Args:
        close: Close price series
        lookback_days: Number of trading days to look back (default 126 = ~6 months)
        
    Returns:
        Regime enum value
        
    Example:
        >>> regime = classify_regime_from_prices(df['Close'])
    """
    if len(close) < lookback_days:
        # Not enough data, assume neutral
        if len(close) >= 2:
            return classify_regime((close.iloc[-1] / close.iloc[0] - 1) * 100)
        return Regime.SIDEWAYS
    
    momentum_6m = (close.iloc[-1] / close.iloc[-lookback_days] - 1) * 100
    return classify_regime(momentum_6m)


def calculate_momentum(close: pd.Series, days: int) -> float:
    """
    Calculate momentum as percentage return over N days.
    
    Args:
        close: Close price series
        days: Number of trading days
        
    Returns:
        Momentum as percentage (e.g., 45.0 for +45%)
    """
    if len(close) < days:
        return 0.0
    return (close.iloc[-1] / close.iloc[-days] - 1) * 100


def is_tradeable_regime(regime: Regime) -> bool:
    """
    Check if a regime is suitable for trading (not in downtrend).
    
    Args:
        regime: Regime enum value
        
    Returns:
        True if regime is tradeable
    """
    return regime != Regime.DOWNTREND


def get_regime_risk_multiplier(regime: Regime) -> float:
    """
    Get position sizing risk multiplier based on regime.
    
    Higher for strong trends, lower for weak/sideways.
    
    Args:
        regime: Regime enum value
        
    Returns:
        Risk multiplier (0.0 to 1.0)
    """
    multipliers = {
        Regime.PARABOLIC: 0.7,      # Reduce size in parabolic (risky)
        Regime.STRONG_UP: 1.0,      # Full size in strong trends
        Regime.MODERATE_UP: 0.9,    # Slightly reduced
        Regime.WEAK_UP: 0.7,        # More cautious
        Regime.SIDEWAYS: 0.5,       # Half size
        Regime.DOWNTREND: 0.0,      # No trading
    }
    return multipliers.get(regime, 0.5)
