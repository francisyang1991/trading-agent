"""
Exit Signal Generator
Handles stop loss, take profit, and exit conditions.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger

from ..indicators.vpes import VPES, VPESResult
from ..indicators.trend import TrendIndicators, TrendDirection, EMAAlignment
from ..indicators.volume import VolumeIndicators, VolumeState


class ExitReason(Enum):
    """Exit reason types."""
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    SWING_LOW_BREAK = "swing_low_break"
    EMA_BREAK = "ema_break"
    VPES_DIVERGENCE = "vpes_divergence"
    TARGET_REACHED = "target_reached"
    PARTIAL_PROFIT = "partial_profit"
    TIME_STOP = "time_stop"
    RISK_EVENT = "risk_event"


@dataclass
class ExitSignal:
    """Exit signal details."""
    should_exit: bool
    is_partial: bool              # True for partial exit
    exit_pct: float               # % of position to exit
    reason: ExitReason
    urgency: str                  # "immediate", "normal", "optional"
    
    current_price: float
    trigger_price: float          # Price that triggered exit
    
    pnl_pct: float               # Current P&L %
    days_held: int
    
    reasoning: str


class ExitSignalGenerator:
    """
    Generates exit signals based on technical conditions and risk rules.
    """
    
    def __init__(
        self,
        trend_indicators: Optional[TrendIndicators] = None,
        vpes: Optional[VPES] = None,
        volume_indicators: Optional[VolumeIndicators] = None,
        ema_break_bars: int = 3,
        partial_profit_pct: float = 0.50,
        trailing_stop_pct: float = 0.08
    ):
        """
        Initialize exit signal generator.
        
        Args:
            trend_indicators: Trend calculator
            vpes: VPES calculator
            volume_indicators: Volume calculator
            ema_break_bars: Bars below EMA to trigger exit
            partial_profit_pct: % of position for partial profit
            trailing_stop_pct: Trailing stop percentage
        """
        self.trend = trend_indicators or TrendIndicators()
        self.vpes = vpes or VPES()
        self.volume = volume_indicators or VolumeIndicators()
        
        self.ema_break_bars = ema_break_bars
        self.partial_profit_pct = partial_profit_pct
        self.trailing_stop_pct = trailing_stop_pct
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        entry_price: float,
        entry_date: datetime,
        stop_price: float,
        target_price: Optional[float] = None,
        highest_price: Optional[float] = None
    ) -> ExitSignal:
        """
        Generate exit signal.
        
        Args:
            symbol: Stock ticker
            data: OHLCV data
            entry_price: Original entry price
            entry_date: Entry date
            stop_price: Current stop loss price
            target_price: Optional profit target
            highest_price: Highest price since entry (for trailing)
            
        Returns:
            ExitSignal with details
        """
        if data.empty:
            return self._no_exit_signal()
        
        current_price = data['close'].iloc[-1]
        current_low = data['low'].iloc[-1]
        
        # Calculate P&L
        pnl_pct = (current_price - entry_price) / entry_price * 100
        
        # Calculate days held
        days_held = (datetime.now() - entry_date).days if isinstance(entry_date, datetime) else 0
        
        # Update highest price for trailing stop
        if highest_price is None:
            highest_price = data['high'].max()
        
        # Check exit conditions in priority order
        
        # 1. Hard stop loss
        if current_low <= stop_price:
            return ExitSignal(
                should_exit=True,
                is_partial=False,
                exit_pct=1.0,
                reason=ExitReason.STOP_LOSS,
                urgency="immediate",
                current_price=current_price,
                trigger_price=stop_price,
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=f"Stop loss triggered at ${stop_price:.2f}"
            )
        
        # 2. Swing low break
        swing_low_exit = self._check_swing_low_break(data, entry_price)
        if swing_low_exit['trigger']:
            return ExitSignal(
                should_exit=True,
                is_partial=False,
                exit_pct=1.0,
                reason=ExitReason.SWING_LOW_BREAK,
                urgency="immediate",
                current_price=current_price,
                trigger_price=swing_low_exit['price'],
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=swing_low_exit['reason']
            )
        
        # 3. EMA break (sustained)
        ema_break_exit = self._check_ema_break(data)
        if ema_break_exit['trigger']:
            return ExitSignal(
                should_exit=True,
                is_partial=False,
                exit_pct=1.0,
                reason=ExitReason.EMA_BREAK,
                urgency="normal",
                current_price=current_price,
                trigger_price=ema_break_exit['price'],
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=ema_break_exit['reason']
            )
        
        # 4. Target reached
        if target_price and current_price >= target_price:
            return ExitSignal(
                should_exit=True,
                is_partial=True,
                exit_pct=self.partial_profit_pct,
                reason=ExitReason.TARGET_REACHED,
                urgency="normal",
                current_price=current_price,
                trigger_price=target_price,
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=f"Target ${target_price:.2f} reached"
            )
        
        # 5. Trailing stop
        trailing_stop = highest_price * (1 - self.trailing_stop_pct)
        if current_price < trailing_stop and pnl_pct > 0:
            return ExitSignal(
                should_exit=True,
                is_partial=True,
                exit_pct=0.5,  # Partial exit on trailing
                reason=ExitReason.TRAILING_STOP,
                urgency="normal",
                current_price=current_price,
                trigger_price=trailing_stop,
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=f"Trailing stop at ${trailing_stop:.2f} (8% from high ${highest_price:.2f})"
            )
        
        # 6. VPES divergence (partial exit)
        divergence_exit = self._check_vpes_divergence(data)
        if divergence_exit['trigger'] and pnl_pct > 5:  # Only if profitable
            return ExitSignal(
                should_exit=True,
                is_partial=True,
                exit_pct=0.3,  # Smaller partial on divergence
                reason=ExitReason.VPES_DIVERGENCE,
                urgency="optional",
                current_price=current_price,
                trigger_price=current_price,
                pnl_pct=pnl_pct,
                days_held=days_held,
                reasoning=divergence_exit['reason']
            )
        
        # No exit signal
        return self._no_exit_signal(current_price, pnl_pct, days_held)
    
    def _check_swing_low_break(
        self,
        data: pd.DataFrame,
        entry_price: float
    ) -> Dict:
        """Check if price broke below swing low."""
        result = {'trigger': False, 'price': 0.0, 'reason': ''}
        
        # Find recent swing low
        swing_lows = self.trend.find_swing_lows(data.tail(30))
        
        if not swing_lows:
            return result
        
        recent_swing_low = swing_lows[-1][1]
        current_close = data['close'].iloc[-1]
        
        # Check if closed below swing low
        if current_close < recent_swing_low:
            result['trigger'] = True
            result['price'] = recent_swing_low
            result['reason'] = f"Closed below swing low ${recent_swing_low:.2f}"
        
        return result
    
    def _check_ema_break(self, data: pd.DataFrame) -> Dict:
        """Check for sustained EMA break."""
        result = {'trigger': False, 'price': 0.0, 'reason': ''}
        
        if len(data) < self.ema_break_bars + 20:
            return result
        
        ema_struct = self.trend.get_ema_structure(data)
        
        # Check slow EMA (EMA50)
        df = self.trend.calculate_emas(data)
        recent_bars = df.tail(self.ema_break_bars)
        
        # Count bars below slow EMA
        bars_below = (recent_bars['close'] < recent_bars['ema_slow']).sum()
        
        if bars_below >= self.ema_break_bars:
            result['trigger'] = True
            result['price'] = ema_struct.ema_slow
            result['reason'] = f"Closed below EMA50 for {self.ema_break_bars} bars"
        
        return result
    
    def _check_vpes_divergence(self, data: pd.DataFrame) -> Dict:
        """Check for bearish VPES divergence."""
        result = {'trigger': False, 'reason': ''}
        
        has_divergence, div_type = self.vpes.detect_divergence(data, lookback=15)
        
        if has_divergence and div_type == "bearish":
            result['trigger'] = True
            result['reason'] = "Bearish VPES divergence detected"
        
        return result
    
    def calculate_dynamic_stop(
        self,
        data: pd.DataFrame,
        entry_price: float,
        current_stop: float,
        pnl_pct: float
    ) -> float:
        """
        Calculate dynamic stop loss that adapts to price action.
        
        Args:
            data: OHLCV data
            entry_price: Original entry price
            current_stop: Current stop price
            pnl_pct: Current profit/loss %
            
        Returns:
            Updated stop price
        """
        # Get current indicators
        ema_struct = self.trend.get_ema_structure(data)
        
        # Find recent swing low
        swing_lows = self.trend.find_swing_lows(data.tail(20))
        
        if swing_lows:
            recent_swing_low = swing_lows[-1][1]
        else:
            recent_swing_low = data['low'].tail(10).min()
        
        # Calculate ATR-based stop
        atr = (data['high'] - data['low']).tail(14).mean()
        current_price = data['close'].iloc[-1]
        atr_stop = current_price - 2 * atr
        
        # Choose most protective stop
        candidates = [current_stop]
        
        # If profitable, move stop up
        if pnl_pct > 5:
            # Trail to swing low
            candidates.append(recent_swing_low * 0.99)
        
        if pnl_pct > 10:
            # Trail to EMA20
            candidates.append(ema_struct.ema_fast * 0.98)
        
        if pnl_pct > 20:
            # Trail to EMA50
            candidates.append(ema_struct.ema_slow * 0.98)
        
        # Never lower stop, only raise it
        new_stop = max(candidates)
        
        # Don't move stop above breakeven too aggressively
        if new_stop > entry_price and pnl_pct < 3:
            new_stop = entry_price * 0.99  # Just below breakeven
        
        return new_stop
    
    def _no_exit_signal(
        self,
        current_price: float = 0,
        pnl_pct: float = 0,
        days_held: int = 0
    ) -> ExitSignal:
        """Create no-exit signal."""
        return ExitSignal(
            should_exit=False,
            is_partial=False,
            exit_pct=0.0,
            reason=ExitReason.STOP_LOSS,  # Placeholder
            urgency="none",
            current_price=current_price,
            trigger_price=0.0,
            pnl_pct=pnl_pct,
            days_held=days_held,
            reasoning="No exit signal"
        )
