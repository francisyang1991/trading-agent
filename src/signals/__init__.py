"""
Signal Generation Module - Entry and Exit signals.

This module provides:
- Entry signals (trend, pullback, breakout, mean reversion, volume pullback)
- Exit signals (profit target, trailing stop, time-based)
- Signal aggregation and filtering
- Scanner adapter for unified signal access

Usage:
    # For scanning (recommended)
    from src.signals import ScannerAdapter, scan_with_signals
    
    adapter = ScannerAdapter()
    signal = adapter.get_highest_priority_signal('NVDA', price_data)
    
    # For direct signal class access
    from src.signals import VolumePullbackEntrySignal, TrendEntrySignal
"""

from .entry.trend_entry import TrendEntrySignal
from .entry.pullback_entry import PullbackEntrySignal
from .entry.breakout_entry import BreakoutEntrySignal
from .entry.mean_reversion_entry import MeanReversionEntrySignal
from .entry.volume_pullback_entry import VolumePullbackEntrySignal
from .entry_engine import EntryEngine, create_entry_engine

from .exit.profit_target import ProfitTargetExit
from .exit.trailing_stop import TrailingStopExit
from .exit.time_exit import TimeBasedExit
from .exit_engine import ExitEngine, create_exit_engine

from .signal_aggregator import SignalAggregator
from .scanner_adapter import ScannerAdapter, ScannerSignal, scan_with_signals

__all__ = [
    # Entry signals
    'TrendEntrySignal',
    'PullbackEntrySignal',
    'BreakoutEntrySignal',
    'MeanReversionEntrySignal',
    'VolumePullbackEntrySignal',  # HIGH PRIORITY
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
    
    # Scanner adapter (for tools/scanner.py)
    'ScannerAdapter',
    'ScannerSignal',
    'scan_with_signals',
]
