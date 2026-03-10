"""
Trend Indicators Module
EMA structure, trend identification, and higher-high/higher-low detection.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger


class TrendDirection(Enum):
    """Trend direction enumeration."""
    STRONG_UP = "strong_up"
    UP = "up"
    NEUTRAL = "neutral"
    DOWN = "down"
    STRONG_DOWN = "strong_down"


class EMAAlignment(Enum):
    """EMA alignment state."""
    BULLISH = "bullish"      # Fast > Slow > Trend
    BEARISH = "bearish"      # Trend > Slow > Fast
    MIXED = "mixed"          # No clear alignment


@dataclass
class TrendState:
    """Container for trend analysis results."""
    direction: TrendDirection
    ema_alignment: EMAAlignment
    higher_highs: bool
    higher_lows: bool
    trend_strength: float      # 0-1 score
    pullback_depth: float      # % pullback from high
    days_in_trend: int
    support_level: float
    resistance_level: float


@dataclass
class EMAStructure:
    """EMA calculation results."""
    ema_fast: float
    ema_slow: float
    ema_trend: float
    alignment: EMAAlignment
    price_vs_fast: float       # % above/below fast EMA
    price_vs_slow: float       # % above/below slow EMA
    price_vs_trend: float      # % above/below trend EMA


class TrendIndicators:
    """
    Trend analysis and EMA-based indicators.
    """
    
    def __init__(
        self,
        ema_fast: int = 20,
        ema_slow: int = 50,
        ema_trend: int = 200,
        swing_lookback: int = 5
    ):
        """
        Initialize trend indicators.
        
        Args:
            ema_fast: Fast EMA period
            ema_slow: Slow EMA period
            ema_trend: Long-term trend EMA period
            swing_lookback: Bars to look for swing points
        """
        self.ema_fast_period = ema_fast
        self.ema_slow_period = ema_slow
        self.ema_trend_period = ema_trend
        self.swing_lookback = swing_lookback
    
    def calculate_emas(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all EMAs.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            DataFrame with EMA columns added
        """
        if data.empty:
            return data
        
        df = data.copy()
        
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast_period, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow_period, adjust=False).mean()
        df['ema_trend'] = df['close'].ewm(span=self.ema_trend_period, adjust=False).mean()
        
        return df
    
    def get_ema_structure(self, data: pd.DataFrame) -> EMAStructure:
        """
        Get current EMA structure.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            EMAStructure with current values
        """
        df = self.calculate_emas(data)
        
        if df.empty:
            return EMAStructure(
                ema_fast=0, ema_slow=0, ema_trend=0,
                alignment=EMAAlignment.MIXED,
                price_vs_fast=0, price_vs_slow=0, price_vs_trend=0
            )
        
        latest = df.iloc[-1]
        price = latest['close']
        
        ema_f = latest['ema_fast']
        ema_s = latest['ema_slow']
        ema_t = latest['ema_trend']
        
        # Determine alignment
        if ema_f > ema_s > ema_t:
            alignment = EMAAlignment.BULLISH
        elif ema_f < ema_s < ema_t:
            alignment = EMAAlignment.BEARISH
        else:
            alignment = EMAAlignment.MIXED
        
        return EMAStructure(
            ema_fast=ema_f,
            ema_slow=ema_s,
            ema_trend=ema_t,
            alignment=alignment,
            price_vs_fast=(price - ema_f) / ema_f * 100 if ema_f > 0 else 0,
            price_vs_slow=(price - ema_s) / ema_s * 100 if ema_s > 0 else 0,
            price_vs_trend=(price - ema_t) / ema_t * 100 if ema_t > 0 else 0
        )
    
    def _find_swing_points(
        self,
        data: pd.DataFrame,
        column: str,
        find_highs: bool,
        lookback: int = None
    ) -> List[Tuple[int, float]]:
        """
        Find swing high or low points.

        Args:
            data: OHLCV DataFrame
            column: Column to search ('high' for highs, 'low' for lows)
            find_highs: True to find swing highs, False for swing lows
            lookback: Bars on each side to confirm swing

        Returns:
            List of (index, price) tuples
        """
        if lookback is None:
            lookback = self.swing_lookback

        if len(data) < lookback * 2 + 1:
            return []

        swing_points = []
        values = data[column].values

        for i in range(lookback, len(data) - lookback):
            is_swing = True
            current = values[i]

            for j in range(1, lookback + 1):
                if find_highs:
                    if values[i - j] >= current or values[i + j] >= current:
                        is_swing = False
                        break
                else:
                    if values[i - j] <= current or values[i + j] <= current:
                        is_swing = False
                        break

            if is_swing:
                swing_points.append((i, current))

        return swing_points

    def find_swing_highs(
        self,
        data: pd.DataFrame,
        lookback: int = None
    ) -> List[Tuple[int, float]]:
        """Find swing high points."""
        return self._find_swing_points(data, 'high', find_highs=True, lookback=lookback)

    def find_swing_lows(
        self,
        data: pd.DataFrame,
        lookback: int = None
    ) -> List[Tuple[int, float]]:
        """Find swing low points."""
        return self._find_swing_points(data, 'low', find_highs=False, lookback=lookback)
    
    def has_higher_highs(
        self,
        data: pd.DataFrame,
        min_swings: int = 2
    ) -> bool:
        """
        Check if price is making higher highs.
        
        Args:
            data: OHLCV DataFrame
            min_swings: Minimum swing points needed
            
        Returns:
            True if making higher highs
        """
        swing_highs = self.find_swing_highs(data)
        
        if len(swing_highs) < min_swings:
            return False
        
        # Check last few swing highs
        recent_highs = [sh[1] for sh in swing_highs[-min_swings:]]
        
        for i in range(1, len(recent_highs)):
            if recent_highs[i] <= recent_highs[i-1]:
                return False
        
        return True
    
    def has_higher_lows(
        self,
        data: pd.DataFrame,
        min_swings: int = 2
    ) -> bool:
        """
        Check if price is making higher lows.
        
        Args:
            data: OHLCV DataFrame
            min_swings: Minimum swing points needed
            
        Returns:
            True if making higher lows
        """
        swing_lows = self.find_swing_lows(data)
        
        if len(swing_lows) < min_swings:
            return False
        
        # Check last few swing lows
        recent_lows = [sl[1] for sl in swing_lows[-min_swings:]]
        
        for i in range(1, len(recent_lows)):
            if recent_lows[i] <= recent_lows[i-1]:
                return False
        
        return True
    
    def get_trend_state(self, data: pd.DataFrame) -> TrendState:
        """
        Comprehensive trend analysis.
        
        Args:
            data: OHLCV DataFrame
            
        Returns:
            TrendState with all analysis results
        """
        if data.empty or len(data) < self.ema_trend_period:
            return TrendState(
                direction=TrendDirection.NEUTRAL,
                ema_alignment=EMAAlignment.MIXED,
                higher_highs=False,
                higher_lows=False,
                trend_strength=0.0,
                pullback_depth=0.0,
                days_in_trend=0,
                support_level=0.0,
                resistance_level=0.0
            )
        
        # Get EMA structure
        ema_struct = self.get_ema_structure(data)
        
        # Check swing structure
        hh = self.has_higher_highs(data)
        hl = self.has_higher_lows(data)
        
        # Find support/resistance
        swing_highs = self.find_swing_highs(data)
        swing_lows = self.find_swing_lows(data)
        
        resistance = swing_highs[-1][1] if swing_highs else data['high'].max()
        support = swing_lows[-1][1] if swing_lows else data['low'].min()
        
        # Calculate pullback depth
        recent_high = data['high'].tail(60).max()  # Last ~60 bars
        current_price = data['close'].iloc[-1]
        pullback = (recent_high - current_price) / recent_high * 100
        
        # Determine trend direction
        direction = self._determine_trend_direction(ema_struct, hh, hl)
        
        # Calculate trend strength
        strength = self._calculate_trend_strength(ema_struct, hh, hl, data)
        
        # Count days in trend
        days_in_trend = self._count_trend_days(data, direction)
        
        return TrendState(
            direction=direction,
            ema_alignment=ema_struct.alignment,
            higher_highs=hh,
            higher_lows=hl,
            trend_strength=strength,
            pullback_depth=pullback,
            days_in_trend=days_in_trend,
            support_level=support,
            resistance_level=resistance
        )
    
    def _determine_trend_direction(
        self,
        ema_struct: EMAStructure,
        higher_highs: bool,
        higher_lows: bool
    ) -> TrendDirection:
        """Determine overall trend direction."""
        
        bullish_signals = 0
        bearish_signals = 0
        
        # EMA alignment
        if ema_struct.alignment == EMAAlignment.BULLISH:
            bullish_signals += 2
        elif ema_struct.alignment == EMAAlignment.BEARISH:
            bearish_signals += 2
        
        # Swing structure
        if higher_highs:
            bullish_signals += 1
        if higher_lows:
            bullish_signals += 1
        
        # Price position
        if ema_struct.price_vs_fast > 0:
            bullish_signals += 0.5
        else:
            bearish_signals += 0.5
        
        if ema_struct.price_vs_trend > 0:
            bullish_signals += 0.5
        else:
            bearish_signals += 0.5
        
        # Determine direction
        if bullish_signals >= 4:
            return TrendDirection.STRONG_UP
        elif bullish_signals >= 2.5:
            return TrendDirection.UP
        elif bearish_signals >= 4:
            return TrendDirection.STRONG_DOWN
        elif bearish_signals >= 2.5:
            return TrendDirection.DOWN
        else:
            return TrendDirection.NEUTRAL
    
    def _calculate_trend_strength(
        self,
        ema_struct: EMAStructure,
        higher_highs: bool,
        higher_lows: bool,
        data: pd.DataFrame
    ) -> float:
        """Calculate trend strength score (0-1)."""
        
        strength = 0.0
        
        # EMA alignment contributes 0.3
        if ema_struct.alignment == EMAAlignment.BULLISH:
            strength += 0.3
        elif ema_struct.alignment == EMAAlignment.BEARISH:
            strength += 0.3  # Strength of trend regardless of direction
        
        # Swing structure contributes 0.3
        if higher_highs and higher_lows:
            strength += 0.3
        elif higher_highs or higher_lows:
            strength += 0.15
        
        # Price above EMAs contributes 0.2
        if ema_struct.price_vs_fast > 0 and ema_struct.price_vs_slow > 0:
            strength += 0.2
        elif ema_struct.price_vs_fast > 0 or ema_struct.price_vs_slow > 0:
            strength += 0.1
        
        # EMA slope contributes 0.2
        if len(data) >= 10:
            df = self.calculate_emas(data)
            ema_slope = (df['ema_fast'].iloc[-1] - df['ema_fast'].iloc[-10]) / df['ema_fast'].iloc[-10]
            if abs(ema_slope) > 0.02:  # 2%+ movement
                strength += 0.2
            elif abs(ema_slope) > 0.01:
                strength += 0.1
        
        return min(strength, 1.0)
    
    def _count_trend_days(
        self,
        data: pd.DataFrame,
        direction: TrendDirection
    ) -> int:
        """Count consecutive days in current trend."""
        
        if len(data) < 2:
            return 0
        
        df = self.calculate_emas(data)
        
        count = 0
        
        # Count backwards from current bar
        for i in range(len(df) - 1, 0, -1):
            if direction in [TrendDirection.UP, TrendDirection.STRONG_UP]:
                # Check if bar maintains uptrend
                if df['close'].iloc[i] > df['ema_fast'].iloc[i]:
                    count += 1
                else:
                    break
            elif direction in [TrendDirection.DOWN, TrendDirection.STRONG_DOWN]:
                if df['close'].iloc[i] < df['ema_fast'].iloc[i]:
                    count += 1
                else:
                    break
            else:
                break
        
        return count
    
    def detect_ema_retest(
        self,
        data: pd.DataFrame,
        ema_type: str = "fast",
        tolerance_pct: float = 1.0
    ) -> Tuple[bool, str]:
        """
        Detect if price is retesting an EMA.
        
        Args:
            data: OHLCV DataFrame
            ema_type: "fast", "slow", or "trend"
            tolerance_pct: Percentage tolerance for retest
            
        Returns:
            (is_retesting, retest_quality)
        """
        df = self.calculate_emas(data)
        
        if df.empty:
            return False, "none"
        
        latest = df.iloc[-1]
        price = latest['close']
        
        # Get appropriate EMA
        ema_col = f'ema_{ema_type}'
        if ema_col not in df.columns:
            return False, "none"
        
        ema_value = latest[ema_col]
        
        # Calculate distance from EMA
        distance_pct = abs(price - ema_value) / ema_value * 100
        
        if distance_pct > tolerance_pct:
            return False, "none"
        
        # Determine quality of retest
        if distance_pct <= tolerance_pct * 0.3:
            quality = "exact"
        elif distance_pct <= tolerance_pct * 0.6:
            quality = "close"
        else:
            quality = "approximate"
        
        return True, quality
    
    def is_trend_intact(
        self,
        data: pd.DataFrame,
        direction: TrendDirection = TrendDirection.UP
    ) -> bool:
        """
        Check if trend structure is intact.
        
        Args:
            data: OHLCV DataFrame
            direction: Expected trend direction
            
        Returns:
            True if trend is intact
        """
        state = self.get_trend_state(data)
        
        if direction in [TrendDirection.UP, TrendDirection.STRONG_UP]:
            # Uptrend intact if:
            # 1. EMA still bullish or mixed (not bearish)
            # 2. Higher lows maintained
            # 3. Price above slow EMA
            ema_ok = state.ema_alignment != EMAAlignment.BEARISH
            struct_ok = state.higher_lows
            price_ok = self.get_ema_structure(data).price_vs_slow > -5  # Allow 5% dip
            
            return ema_ok and struct_ok and price_ok
        
        elif direction in [TrendDirection.DOWN, TrendDirection.STRONG_DOWN]:
            ema_ok = state.ema_alignment != EMAAlignment.BULLISH
            struct_ok = not state.higher_lows  # Lower lows
            price_ok = self.get_ema_structure(data).price_vs_slow < 5
            
            return ema_ok and struct_ok and price_ok
        
        return True


def calculate_atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range.
    
    Args:
        data: OHLCV DataFrame
        period: ATR period
        
    Returns:
        ATR series
    """
    high = data['high']
    low = data['low']
    close = data['close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return atr


def calculate_rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index.
    
    Args:
        data: OHLCV DataFrame
        period: RSI period
        
    Returns:
        RSI series
    """
    delta = data['close'].diff()
    
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


# ── Series-based adapters (for tools using pd.Series with Close/High/Low) ───

def calculate_ema_series(data: pd.Series, period: int) -> pd.Series:
    """
    Calculate Exponential Moving Average on a price series.
    Use this for Series-based callers (e.g. tools with yfinance Close).
    """
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi_series(data: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate RSI on a close price series.
    Use this for Series-based callers.
    """
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr_series(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """
    Calculate ATR from OHLC series.
    Use this for Series-based callers (e.g. tools with yfinance High/Low/Close).
    """
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()
