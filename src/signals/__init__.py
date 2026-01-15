"""
Signal Generation Module - Entry and Exit signals.

This module provides:
- Entry signals (trend, pullback, breakout, mean reversion)
- Exit signals (profit target, trailing stop, time-based)
- Signal aggregation and filtering
"""

from .entry.trend_entry import TrendEntrySignal
from .entry.pullback_entry import PullbackEntrySignal
from .entry.breakout_entry import BreakoutEntrySignal
from .entry.mean_reversion_entry import MeanReversionEntrySignal
from .entry_engine import EntryEngine, create_entry_engine

from .exit.profit_target import ProfitTargetExit
from .exit.trailing_stop import TrailingStopExit
from .exit.time_exit import TimeBasedExit
from .exit_engine import ExitEngine, create_exit_engine

from .signal_aggregator import SignalAggregator

__all__ = [
    # Entry signals
    'TrendEntrySignal',
    'PullbackEntrySignal',
    'BreakoutEntrySignal',
    'MeanReversionEntrySignal',
    'EntryEngine',
    'create_entry_engine',
    
    # Exit signals
    'ProfitTargetExit',
    'TrailingStopExit',
    'TimeBasedExit',
    'ExitEngine',
    'create_exit_engine',
    
    # Aggregation
    'SignalAggregator',
]
