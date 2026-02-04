"""
Scanner Adapter
===============

Provides a simple interface for the scanner to use signal classes.
This ensures that the scanner and backtest use the same signal logic.

The scanner previously had its own pattern detection logic in tools/scanner.py.
This adapter wraps the src/signals/ classes so that:
1. Scanner uses the same code as backtest
2. Pattern parameters are centralized
3. Changes propagate to both scanner and backtest

Usage:
    from src.signals.scanner_adapter import ScannerAdapter
    
    adapter = ScannerAdapter()
    signals = adapter.scan_stock(symbol, price_data)
    
    # Get the volume pullback signal specifically
    vp_signal = adapter.check_volume_pullback(symbol, price_data)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import pandas as pd

from .entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig
from .entry.trend_entry import TrendEntrySignal
from .entry.pullback_entry import PullbackEntrySignal
from .entry.breakout_entry import BreakoutEntrySignal
from .entry.mean_reversion_entry import MeanReversionEntrySignal

from ..core.types import Signal, SignalType, Regime, VolatilityLevel
from ..regime import classify_regime, classify_volatility, Regime as RegimeEnum, VolCategory


@dataclass
class ScannerSignal:
    """
    Unified signal output for scanner.
    
    This dataclass provides a common interface for all signal types,
    making it easy for the scanner to process results.
    """
    symbol: str
    signal_type: str           # e.g., 'VOLUME_PULLBACK', 'TREND_ENTRY', etc.
    is_active: bool            # True if signal is currently triggered
    is_tradeable: bool         # True if setup meets trading criteria
    confidence: float          # 0.0 to 1.0
    
    # Levels
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    risk_reward: Optional[float] = None
    
    # Details
    reasoning: str = ""
    blockers: List[str] = None
    raw_data: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.blockers is None:
            self.blockers = []
        if self.raw_data is None:
            self.raw_data = {}


class ScannerAdapter:
    """
    Adapter to use src/signals/ classes from the scanner.
    
    This ensures scanner and backtest use identical signal logic.
    
    Example:
        adapter = ScannerAdapter()
        
        # Check for volume pullback (highest priority)
        vp_signal = adapter.check_volume_pullback('NVDA', price_data)
        if vp_signal and vp_signal.is_tradeable:
            print(f"STRONG BUY: {vp_signal.reasoning}")
        
        # Or scan for all signals
        all_signals = adapter.scan_stock('NVDA', price_data)
    """
    
    def __init__(self):
        """Initialize signal generators with default configs."""
        # Volume pullback is highest priority
        self.volume_pullback = VolumePullbackEntrySignal(
            config=VolumePullbackConfig(
                # Optimized parameters from backtest
                min_green_candles=3,
                max_pullback_bars=5,
                ema9_tolerance_pct=10.0,  # Relaxed based on backtest
                max_pullback_pct=15.0,    # Relaxed based on backtest
                pullback_vol_ratio=0.85,  # Relaxed
                min_breakout_vol_ratio=1.2,  # 87% win rate threshold
                min_confidence=0.70,
            )
        )
        
        # Other signal generators
        self.trend_entry = TrendEntrySignal()
        self.pullback_entry = PullbackEntrySignal()
        self.breakout_entry = BreakoutEntrySignal()
        self.mean_reversion_entry = MeanReversionEntrySignal()
    
    def check_volume_pullback(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Optional[RegimeEnum] = None,
        vol_category: Optional[VolCategory] = None,
    ) -> Optional[ScannerSignal]:
        """
        Check for Volume-Confirmed EMA9 Pullback pattern.
        
        This is the HIGHEST PRIORITY pattern - check first!
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Optional pre-calculated regime
            vol_category: Optional pre-calculated volatility category
            
        Returns:
            ScannerSignal if pattern detected, None otherwise
        """
        # Convert regime/vol to core types if provided
        core_regime = Regime.UNKNOWN
        core_vol = VolatilityLevel.MEDIUM
        
        try:
            signal = self.volume_pullback.generate(
                symbol=symbol,
                data=data,
                regime=core_regime,
                volatility=core_vol,
            )
        except Exception as e:
            # Log but don't crash scanner
            return None
        
        if signal is None:
            return None
        
        metadata = signal.metadata if hasattr(signal, 'metadata') else {}
        reasons = metadata.get("reasons", [])
        blockers = metadata.get("blockers", [])
        is_tradeable = bool(metadata.get("is_tradeable", signal.confidence >= 0.80))
        reasoning = "; ".join(reasons) if reasons else signal.reasoning if hasattr(signal, "reasoning") else ""
        
        # Convert to ScannerSignal format
        return ScannerSignal(
            symbol=symbol,
            signal_type='VOLUME_PULLBACK',
            is_active=True,
            is_tradeable=is_tradeable,
            confidence=signal.confidence,
            entry_price=signal.price if hasattr(signal, 'price') else None,
            stop_loss=signal.stop_loss if hasattr(signal, 'stop_loss') else None,
            target_1=metadata.get('target_1_50pct'),
            target_2=metadata.get('target_2_full'),
            risk_reward=metadata.get('risk_reward'),
            reasoning=reasoning,
            blockers=blockers,
            raw_data=metadata,
        )
    
    def scan_stock(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> List[ScannerSignal]:
        """
        Scan a stock for all signal types.
        
        Returns signals in priority order:
        1. Volume Pullback (highest priority)
        2. Trend Entry
        3. Pullback Entry
        4. Breakout Entry
        5. Mean Reversion Entry
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            
        Returns:
            List of ScannerSignal objects, ordered by priority
        """
        signals = []
        
        # 1. Volume Pullback (highest priority)
        vp_signal = self.check_volume_pullback(symbol, data)
        if vp_signal:
            signals.append(vp_signal)
        
        # 2. Other signals (lower priority)
        # These use the core Signal type, convert to ScannerSignal
        for generator, signal_type in [
            (self.trend_entry, 'TREND_ENTRY'),
            (self.pullback_entry, 'PULLBACK_ENTRY'),
            (self.breakout_entry, 'BREAKOUT_ENTRY'),
            (self.mean_reversion_entry, 'MEAN_REVERSION'),
        ]:
            try:
                signal = generator.generate(symbol, data)
                if signal:
                    scanner_signal = ScannerSignal(
                        symbol=symbol,
                        signal_type=signal_type,
                        is_active=True,
                        is_tradeable=signal.confidence >= 0.70,
                        confidence=signal.confidence if hasattr(signal, 'confidence') else 0.5,
                        reasoning=signal.reasoning if hasattr(signal, 'reasoning') else "",
                        raw_data=signal.metadata if hasattr(signal, 'metadata') else {},
                    )
                    signals.append(scanner_signal)
            except Exception:
                # Don't crash on individual signal errors
                continue
        
        return signals
    
    def get_highest_priority_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
    ) -> Optional[ScannerSignal]:
        """
        Get the highest priority tradeable signal for a stock.
        
        Checks signals in priority order and returns the first tradeable one.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            
        Returns:
            Highest priority tradeable ScannerSignal, or None
        """
        # Volume pullback is always highest priority
        vp_signal = self.check_volume_pullback(symbol, data)
        if vp_signal and vp_signal.is_tradeable:
            return vp_signal
        
        # Check other signals
        signals = self.scan_stock(symbol, data)
        for signal in signals:
            if signal.is_tradeable:
                return signal
        
        # Return non-tradeable volume pullback if detected (for WAIT status)
        if vp_signal:
            return vp_signal
        
        return None


def scan_with_signals(
    symbol: str,
    data: pd.DataFrame,
) -> Dict[str, Any]:
    """
    Convenience function to scan a stock using the adapter.
    
    Returns a dict compatible with scanner output format.
    
    Args:
        symbol: Stock symbol
        data: OHLCV DataFrame
        
    Returns:
        Dict with signal data for scanner display
    """
    adapter = ScannerAdapter()
    signal = adapter.get_highest_priority_signal(symbol, data)
    
    if signal is None:
        return {
            'symbol': symbol,
            'has_signal': False,
            'signal_type': None,
            'is_tradeable': False,
            'confidence': 0.0,
        }
    
    return {
        'symbol': symbol,
        'has_signal': True,
        'signal_type': signal.signal_type,
        'is_tradeable': signal.is_tradeable,
        'confidence': signal.confidence,
        'entry_price': signal.entry_price,
        'stop_loss': signal.stop_loss,
        'target_1': signal.target_1,
        'target_2': signal.target_2,
        'risk_reward': signal.risk_reward,
        'reasoning': signal.reasoning,
        'blockers': signal.blockers,
    }
