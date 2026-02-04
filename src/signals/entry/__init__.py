"""Entry signal generators."""

from .trend_entry import TrendEntrySignal
from .pullback_entry import PullbackEntrySignal
from .breakout_entry import BreakoutEntrySignal
from .mean_reversion_entry import MeanReversionEntrySignal
from .volume_pullback_entry import VolumePullbackEntrySignal, scan_for_volume_pullback

__all__ = [
    'TrendEntrySignal',
    'PullbackEntrySignal', 
    'BreakoutEntrySignal',
    'MeanReversionEntrySignal',
    'VolumePullbackEntrySignal',  # HIGH PRIORITY PATTERN
    'scan_for_volume_pullback',
]
