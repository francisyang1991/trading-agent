"""
Strategy Selection Module
=========================

This module provides strategy selection based on regime and volatility.

Usage:
    from src.strategy import Strategy, get_strategy, calculate_ev, calculate_kelly
    
    # Get optimal strategy for regime/volatility combination
    strategy, params = get_strategy(Regime.STRONG_UP, VolCategory.MODERATE)
    
    # Calculate expected value
    ev = calculate_ev(win_rate=0.55, avg_win=5.0, avg_loss=3.0)
"""

from .matrix import Strategy, StrategyParams, STRATEGY_MATRIX, get_strategy
from .ev_calculator import (
    calculate_ev,
    calculate_kelly,
    estimate_distribution,
    calculate_risk_reward,
)

__all__ = [
    "Strategy",
    "StrategyParams",
    "STRATEGY_MATRIX",
    "get_strategy",
    "calculate_ev",
    "calculate_kelly",
    "estimate_distribution",
    "calculate_risk_reward",
]
