"""
Market Regime Detection
=======================

This module provides regime classification for stocks and markets.

Usage:
    from src.regime import Regime, VolCategory, classify_regime, classify_volatility
    
    regime = classify_regime(momentum_6m=45.0)  # Returns Regime.MODERATE_UP
    vol_cat = classify_volatility(volatility_pct=55.0)  # Returns VolCategory.HIGH
"""

from .classifier import Regime, classify_regime
from .volatility import VolCategory, classify_volatility

__all__ = [
    "Regime",
    "VolCategory",
    "classify_regime",
    "classify_volatility",
]
