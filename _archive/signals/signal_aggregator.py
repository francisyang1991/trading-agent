"""
Signal Aggregator
=================

Combine and filter signals from multiple sources.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass

from ..core.types import Signal, SignalType


@dataclass
class AggregatorConfig:
    """Signal aggregation configuration."""
    min_confidence: float = 0.60
    max_signals_per_symbol: int = 1
    combine_method: str = 'best'  # 'best', 'average', 'unanimous'


class SignalAggregator:
    """
    Aggregate signals from multiple sources.
    
    Example:
        aggregator = SignalAggregator()
        
        # Add signals
        aggregator.add_signal(trend_signal)
        aggregator.add_signal(pullback_signal)
        
        # Get best signal
        best = aggregator.get_best_signal('NVDA')
    """
    
    def __init__(self, config: Optional[AggregatorConfig] = None):
        self.config = config or AggregatorConfig()
        self._signals: Dict[str, List[Signal]] = {}
    
    def add_signal(self, signal: Signal):
        """Add signal to aggregator."""
        symbol = signal.symbol
        if symbol not in self._signals:
            self._signals[symbol] = []
        self._signals[symbol].append(signal)
    
    def add_signals(self, signals: List[Signal]):
        """Add multiple signals."""
        for signal in signals:
            self.add_signal(signal)
    
    def get_signals(self, symbol: str) -> List[Signal]:
        """Get all signals for symbol."""
        return self._signals.get(symbol, [])
    
    def get_best_signal(self, symbol: str) -> Optional[Signal]:
        """Get best signal for symbol."""
        signals = self._signals.get(symbol, [])
        if not signals:
            return None
        
        # Filter by minimum confidence
        filtered = [s for s in signals if s.confidence >= self.config.min_confidence]
        if not filtered:
            return None
        
        # Return highest confidence
        return max(filtered, key=lambda s: s.confidence)
    
    def get_all_best_signals(self) -> Dict[str, Signal]:
        """Get best signal for each symbol."""
        result = {}
        for symbol in self._signals:
            best = self.get_best_signal(symbol)
            if best:
                result[symbol] = best
        return result
    
    def clear(self, symbol: str = None):
        """Clear signals."""
        if symbol:
            self._signals.pop(symbol, None)
        else:
            self._signals.clear()
    
    def filter_by_type(self, signal_type: SignalType) -> List[Signal]:
        """Get all signals of a specific type."""
        result = []
        for signals in self._signals.values():
            for signal in signals:
                if signal.signal_type == signal_type:
                    result.append(signal)
        return result
