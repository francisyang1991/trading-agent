"""
Entry Engine
============

Orchestrates all entry signal generators based on regime and strategy.
Selects the appropriate entry strategy for current market conditions.
"""

import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from ..core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy
from .entry.trend_entry import TrendEntrySignal
from .entry.pullback_entry import PullbackEntrySignal
from .entry.breakout_entry import BreakoutEntrySignal
from .entry.mean_reversion_entry import MeanReversionEntrySignal


# Strategy Matrix - Maps (Regime, Volatility) to Entry Strategies
ENTRY_STRATEGY_MATRIX = {
    # STRONG UPTREND (Parabolic + Strong Up)
    (Regime.PARABOLIC, VolatilityLevel.LOW): [Strategy.TREND_FOLLOWING],
    (Regime.PARABOLIC, VolatilityLevel.MEDIUM): [Strategy.TREND_FOLLOWING],
    (Regime.PARABOLIC, VolatilityLevel.HIGH): [Strategy.STAY_CASH],  # Too risky
    (Regime.PARABOLIC, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    (Regime.STRONG_UP, VolatilityLevel.LOW): [Strategy.TREND_FOLLOWING, Strategy.SWING_TRADE],
    (Regime.STRONG_UP, VolatilityLevel.MEDIUM): [Strategy.TREND_FOLLOWING, Strategy.SWING_TRADE],
    (Regime.STRONG_UP, VolatilityLevel.HIGH): [Strategy.SWING_TRADE],
    (Regime.STRONG_UP, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    # MODERATE UPTREND
    (Regime.MODERATE_UP, VolatilityLevel.LOW): [Strategy.TREND_FOLLOWING, Strategy.SWING_TRADE],
    (Regime.MODERATE_UP, VolatilityLevel.MEDIUM): [Strategy.SWING_TRADE],
    (Regime.MODERATE_UP, VolatilityLevel.HIGH): [Strategy.SWING_TRADE],
    (Regime.MODERATE_UP, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    # WEAK UPTREND
    (Regime.WEAK_UP, VolatilityLevel.LOW): [Strategy.SWING_TRADE],
    (Regime.WEAK_UP, VolatilityLevel.MEDIUM): [Strategy.SWING_TRADE, Strategy.MEAN_REVERSION],
    (Regime.WEAK_UP, VolatilityLevel.HIGH): [Strategy.MEAN_REVERSION],
    (Regime.WEAK_UP, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    # SIDEWAYS
    (Regime.SIDEWAYS, VolatilityLevel.LOW): [Strategy.TREND_FOLLOWING],  # Breakout
    (Regime.SIDEWAYS, VolatilityLevel.MEDIUM): [Strategy.MEAN_REVERSION, Strategy.SWING_TRADE],
    (Regime.SIDEWAYS, VolatilityLevel.HIGH): [Strategy.MEAN_REVERSION],
    (Regime.SIDEWAYS, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    # DOWNTREND
    (Regime.DOWNTREND, VolatilityLevel.LOW): [Strategy.MEAN_REVERSION],
    (Regime.DOWNTREND, VolatilityLevel.MEDIUM): [Strategy.MEAN_REVERSION],
    (Regime.DOWNTREND, VolatilityLevel.HIGH): [Strategy.STAY_CASH],
    (Regime.DOWNTREND, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
    
    # UNKNOWN
    (Regime.UNKNOWN, VolatilityLevel.LOW): [Strategy.SWING_TRADE],
    (Regime.UNKNOWN, VolatilityLevel.MEDIUM): [Strategy.SWING_TRADE],
    (Regime.UNKNOWN, VolatilityLevel.HIGH): [Strategy.STAY_CASH],
    (Regime.UNKNOWN, VolatilityLevel.EXTREME): [Strategy.STAY_CASH],
}


@dataclass
class EntryEngineConfig:
    """Configuration for entry engine."""
    min_confidence: float = 0.60
    max_signals_per_symbol: int = 1
    enable_trend: bool = True
    enable_pullback: bool = True
    enable_breakout: bool = True
    enable_mean_reversion: bool = True


class EntryEngine:
    """
    Entry Signal Engine - Generates entry signals based on market conditions.
    
    Features:
    - Automatic strategy selection based on regime/volatility
    - Multiple entry signal generators
    - Signal filtering and ranking
    
    Example:
        engine = EntryEngine()
        
        # Generate entries for watchlist
        signals = engine.generate_entries(
            symbols=['NVDA', 'AAPL', 'GOOGL'],
            price_data=data_dict,
            regime=Regime.UPTREND,
            volatility=VolatilityLevel.MEDIUM
        )
        
        for signal in signals:
            if signal.confidence > 0.7:
                execute_entry(signal)
    """
    
    def __init__(self, config: Optional[EntryEngineConfig] = None):
        self.config = config or EntryEngineConfig()
        
        # Initialize signal generators
        self.trend_signal = TrendEntrySignal()
        self.pullback_signal = PullbackEntrySignal()
        self.breakout_signal = BreakoutEntrySignal()
        self.mean_reversion_signal = MeanReversionEntrySignal()
    
    def generate_entry(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM,
        allowed_strategies: Optional[List[Strategy]] = None
    ) -> Optional[Signal]:
        """
        Generate entry signal for a single symbol.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            allowed_strategies: Override default strategy selection
            
        Returns:
            Best entry signal if found, None otherwise
        """
        # Determine which strategies to use
        if allowed_strategies is None:
            key = (regime, volatility)
            allowed_strategies = ENTRY_STRATEGY_MATRIX.get(
                key, 
                [Strategy.SWING_TRADE]  # Default
            )
        
        # Check if we should stay in cash
        if Strategy.STAY_CASH in allowed_strategies and len(allowed_strategies) == 1:
            return None
        
        # Generate signals from each enabled generator
        signals = []
        
        # Trend Following
        if (self.config.enable_trend and 
            Strategy.TREND_FOLLOWING in allowed_strategies):
            signal = self.trend_signal.generate(symbol, data, regime, volatility)
            if signal and signal.confidence >= self.config.min_confidence:
                signals.append(signal)
        
        # Pullback / Swing Trade
        if (self.config.enable_pullback and 
            Strategy.SWING_TRADE in allowed_strategies):
            signal = self.pullback_signal.generate(symbol, data, regime, volatility)
            if signal and signal.confidence >= self.config.min_confidence:
                signals.append(signal)
        
        # Breakout (also part of trend following)
        if (self.config.enable_breakout and 
            Strategy.TREND_FOLLOWING in allowed_strategies):
            signal = self.breakout_signal.generate(symbol, data, regime, volatility)
            if signal and signal.confidence >= self.config.min_confidence:
                signals.append(signal)
        
        # Mean Reversion
        if (self.config.enable_mean_reversion and 
            Strategy.MEAN_REVERSION in allowed_strategies):
            signal = self.mean_reversion_signal.generate(symbol, data, regime, volatility)
            if signal and signal.confidence >= self.config.min_confidence:
                signals.append(signal)
        
        # Return best signal
        if not signals:
            return None
        
        return max(signals, key=lambda s: s.confidence)
    
    def generate_entries(
        self,
        symbols: List[str],
        price_data: Dict[str, pd.DataFrame],
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM,
        allowed_strategies: Optional[List[Strategy]] = None
    ) -> List[Signal]:
        """
        Generate entry signals for multiple symbols.
        
        Args:
            symbols: List of stock symbols
            price_data: Dictionary of symbol -> OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            allowed_strategies: Override default strategy selection
            
        Returns:
            List of entry signals, sorted by confidence
        """
        signals = []
        
        for symbol in symbols:
            if symbol not in price_data:
                continue
            
            signal = self.generate_entry(
                symbol=symbol,
                data=price_data[symbol],
                regime=regime,
                volatility=volatility,
                allowed_strategies=allowed_strategies
            )
            
            if signal:
                signals.append(signal)
        
        # Sort by confidence
        signals.sort(key=lambda s: s.confidence, reverse=True)
        
        return signals
    
    def scan_for_entries(
        self,
        symbols: List[str],
        price_data: Dict[str, pd.DataFrame],
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM,
        min_confidence: float = 0.65,
        max_entries: int = 10
    ) -> List[Signal]:
        """
        Scan watchlist for best entry opportunities.
        
        Args:
            symbols: List of symbols to scan
            price_data: Price data dictionary
            regime: Market regime
            volatility: Volatility level
            min_confidence: Minimum confidence threshold
            max_entries: Maximum entries to return
            
        Returns:
            Top entry signals
        """
        all_signals = self.generate_entries(
            symbols=symbols,
            price_data=price_data,
            regime=regime,
            volatility=volatility
        )
        
        # Filter by confidence
        filtered = [s for s in all_signals if s.confidence >= min_confidence]
        
        return filtered[:max_entries]


def create_entry_engine(
    min_confidence: float = 0.60,
    enable_all: bool = True
) -> EntryEngine:
    """
    Factory function to create entry engine.
    
    Args:
        min_confidence: Minimum signal confidence
        enable_all: Enable all signal generators
        
    Returns:
        Configured EntryEngine
    """
    config = EntryEngineConfig(
        min_confidence=min_confidence,
        enable_trend=enable_all,
        enable_pullback=enable_all,
        enable_breakout=enable_all,
        enable_mean_reversion=enable_all
    )
    
    return EntryEngine(config)
