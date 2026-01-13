"""
Stock Classification System
Categorizes stocks into Type A (Trend), Type B (Range), or Type C (Reversal).

Classification determines which trading strategies are allowed.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger

from ..indicators.trend import TrendIndicators, TrendDirection, EMAAlignment
from ..indicators.vpes import VPES
from ..indicators.volume import VolumeIndicators


class StockType(Enum):
    """Stock classification types."""
    TYPE_A_TREND = "A"        # Clear trend - full trend trading allowed
    TYPE_B_RANGE = "B"        # Range-bound - only small speculation
    TYPE_C_REVERSAL = "C"     # Potential reversal - observation only
    UNCLASSIFIED = "X"        # Cannot be classified


@dataclass
class ClassificationResult:
    """Stock classification results."""
    symbol: str
    stock_type: StockType
    confidence: float              # 0-1 classification confidence
    
    # Type A specific
    rally_magnitude: float         # % of prior rally
    pullback_depth: float          # % pullback from high
    ema_intact: bool               # EMA structure preserved
    
    # Type B specific
    range_high: float              # Range resistance
    range_low: float               # Range support
    range_width: float             # % width of range
    
    # Type C specific
    downtrend_duration: int        # Days in downtrend
    ema_proximity: float           # % from long-term EMA
    reversal_signals: int          # Number of reversal signals
    
    # Trading permissions
    allow_trend_trade: bool
    allow_add_position: bool
    allow_speculation: bool
    max_position_pct: float
    
    reasoning: str                 # Explanation for classification


class StockClassifier:
    """
    Classifies stocks based on technical characteristics.
    
    Type A (Trend): Past rally + pullback + EMA intact → Full trend trading
    Type B (Range): Sideways, support/resistance bound → Small speculation only
    Type C (Reversal): Breaking long-term downtrend → Tiny position, observation
    """
    
    def __init__(
        self,
        # Type A parameters
        min_rally_pct: float = 0.15,
        max_pullback_pct: float = 0.10,
        rally_lookback_days: int = 60,
        
        # Type B parameters
        range_band_pct: float = 0.10,
        range_lookback_days: int = 30,
        
        # Type C parameters
        downtrend_days: int = 90,
        ema_proximity_pct: float = 0.02,
        
        # Position limits
        type_a_max_position: float = 0.25,
        type_b_max_position: float = 0.05,
        type_c_max_position: float = 0.03
    ):
        """
        Initialize classifier.
        
        Args:
            min_rally_pct: Minimum rally for Type A
            max_pullback_pct: Maximum pullback for Type A
            rally_lookback_days: Days to look for rally
            range_band_pct: Range width for Type B
            range_lookback_days: Days to analyze for range
            downtrend_days: Minimum downtrend for Type C
            ema_proximity_pct: Proximity to EMA for Type C
            type_a_max_position: Max position for Type A
            type_b_max_position: Max position for Type B
            type_c_max_position: Max position for Type C
        """
        self.min_rally_pct = min_rally_pct
        self.max_pullback_pct = max_pullback_pct
        self.rally_lookback = rally_lookback_days
        
        self.range_band_pct = range_band_pct
        self.range_lookback = range_lookback_days
        
        self.downtrend_days = downtrend_days
        self.ema_proximity_pct = ema_proximity_pct
        
        self.type_a_max = type_a_max_position
        self.type_b_max = type_b_max_position
        self.type_c_max = type_c_max_position
        
        # Initialize indicator calculators
        self.trend_indicators = TrendIndicators()
        self.vpes = VPES()
        self.volume_indicators = VolumeIndicators()
    
    def classify(
        self,
        symbol: str,
        data: pd.DataFrame,
        multi_tf_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> ClassificationResult:
        """
        Classify a stock based on its price action.
        
        Args:
            symbol: Stock ticker
            data: Daily OHLCV data
            multi_tf_data: Optional multi-timeframe data
            
        Returns:
            ClassificationResult with classification and permissions
        """
        if data.empty or len(data) < max(self.rally_lookback, self.range_lookback, self.downtrend_days):
            return self._unclassified_result(symbol, "Insufficient data")
        
        # Check each type in order
        type_a_result = self._check_type_a(data)
        type_b_result = self._check_type_b(data)
        type_c_result = self._check_type_c(data)
        
        # Determine best classification
        if type_a_result['is_type_a'] and type_a_result['confidence'] > 0.6:
            return self._create_type_a_result(symbol, data, type_a_result)
        
        elif type_b_result['is_type_b'] and type_b_result['confidence'] > 0.6:
            return self._create_type_b_result(symbol, data, type_b_result)
        
        elif type_c_result['is_type_c'] and type_c_result['confidence'] > 0.5:
            return self._create_type_c_result(symbol, data, type_c_result)
        
        # If no clear classification, determine best fit
        confidences = {
            'A': type_a_result['confidence'],
            'B': type_b_result['confidence'],
            'C': type_c_result['confidence']
        }
        
        best_type = max(confidences, key=confidences.get)
        
        if confidences[best_type] >= 0.4:
            if best_type == 'A':
                return self._create_type_a_result(symbol, data, type_a_result, uncertain=True)
            elif best_type == 'B':
                return self._create_type_b_result(symbol, data, type_b_result, uncertain=True)
            else:
                return self._create_type_c_result(symbol, data, type_c_result, uncertain=True)
        
        return self._unclassified_result(symbol, "No clear pattern match")
    
    def _check_type_a(self, data: pd.DataFrame) -> Dict:
        """
        Check if stock qualifies as Type A (Trend).
        
        Criteria:
        1. Clear prior rally (>15% move up)
        2. Pullback or consolidation (<10% from high)
        3. EMA structure still bullish
        """
        result = {
            'is_type_a': False,
            'confidence': 0.0,
            'rally_magnitude': 0.0,
            'pullback_depth': 0.0,
            'ema_intact': False
        }
        
        # Analyze lookback period
        lookback_data = data.tail(self.rally_lookback)
        
        if len(lookback_data) < 30:
            return result
        
        # Find rally - look for significant swing low to high
        period_low = lookback_data['low'].min()
        period_high = lookback_data['high'].max()
        current_price = lookback_data['close'].iloc[-1]
        
        # Calculate rally magnitude
        rally = (period_high - period_low) / period_low
        result['rally_magnitude'] = rally
        
        # Calculate pullback from high
        pullback = (period_high - current_price) / period_high
        result['pullback_depth'] = pullback
        
        # Check EMA structure
        trend_state = self.trend_indicators.get_trend_state(data)
        ema_struct = self.trend_indicators.get_ema_structure(data)
        
        ema_bullish = ema_struct.alignment == EMAAlignment.BULLISH
        ema_ok = ema_bullish or (
            ema_struct.alignment == EMAAlignment.MIXED and
            ema_struct.price_vs_slow > -3  # Allow slight dip below slow EMA
        )
        result['ema_intact'] = ema_ok
        
        # Calculate confidence
        confidence = 0.0
        
        # Rally criterion (weight: 0.4)
        if rally >= self.min_rally_pct:
            confidence += 0.4
        elif rally >= self.min_rally_pct * 0.7:
            confidence += 0.2
        
        # Pullback criterion (weight: 0.3)
        if 0.03 <= pullback <= self.max_pullback_pct:
            confidence += 0.3  # Ideal pullback
        elif pullback < 0.03:
            confidence += 0.2  # Still strong, not much pullback
        elif pullback <= self.max_pullback_pct * 1.5:
            confidence += 0.15  # Deeper pullback but acceptable
        
        # EMA criterion (weight: 0.3)
        if ema_bullish:
            confidence += 0.3
        elif ema_ok:
            confidence += 0.15
        
        result['confidence'] = confidence
        result['is_type_a'] = (
            rally >= self.min_rally_pct and
            pullback <= self.max_pullback_pct and
            ema_ok
        )
        
        return result
    
    def _check_type_b(self, data: pd.DataFrame) -> Dict:
        """
        Check if stock qualifies as Type B (Range-bound).
        
        Criteria:
        1. Price oscillating within a band
        2. Clear support and resistance
        3. No dominant trend
        """
        result = {
            'is_type_b': False,
            'confidence': 0.0,
            'range_high': 0.0,
            'range_low': 0.0,
            'range_width': 0.0
        }
        
        # Analyze range period
        range_data = data.tail(self.range_lookback)
        
        if len(range_data) < 20:
            return result
        
        # Calculate range
        range_high = range_data['high'].max()
        range_low = range_data['low'].min()
        range_mid = (range_high + range_low) / 2
        range_width = (range_high - range_low) / range_mid
        
        result['range_high'] = range_high
        result['range_low'] = range_low
        result['range_width'] = range_width
        
        # Check if price is oscillating (not trending)
        closes = range_data['close'].values
        
        # Count crosses through midpoint
        crosses = 0
        for i in range(1, len(closes)):
            if (closes[i-1] < range_mid and closes[i] > range_mid) or \
               (closes[i-1] > range_mid and closes[i] < range_mid):
                crosses += 1
        
        # Check trend neutrality
        trend_state = self.trend_indicators.get_trend_state(data)
        is_neutral = trend_state.direction == TrendDirection.NEUTRAL
        
        # Calculate confidence
        confidence = 0.0
        
        # Range width criterion (weight: 0.3)
        if range_width <= self.range_band_pct:
            confidence += 0.3
        elif range_width <= self.range_band_pct * 1.5:
            confidence += 0.15
        
        # Oscillation criterion (weight: 0.4)
        expected_crosses = self.range_lookback / 10  # Expect cross every ~10 bars
        if crosses >= expected_crosses:
            confidence += 0.4
        elif crosses >= expected_crosses * 0.5:
            confidence += 0.2
        
        # Trend neutrality (weight: 0.3)
        if is_neutral:
            confidence += 0.3
        elif trend_state.trend_strength < 0.3:
            confidence += 0.15
        
        result['confidence'] = confidence
        result['is_type_b'] = (
            range_width <= self.range_band_pct * 1.2 and
            crosses >= expected_crosses * 0.5 and
            trend_state.trend_strength < 0.5
        )
        
        return result
    
    def _check_type_c(self, data: pd.DataFrame) -> Dict:
        """
        Check if stock qualifies as Type C (Reversal candidate).
        
        Criteria:
        1. Long-term downtrend or consolidation
        2. Approaching or breaking long-term EMA
        3. Early reversal signals
        """
        result = {
            'is_type_c': False,
            'confidence': 0.0,
            'downtrend_duration': 0,
            'ema_proximity': 0.0,
            'reversal_signals': 0
        }
        
        if len(data) < self.downtrend_days:
            return result
        
        # Get EMA structure
        ema_struct = self.trend_indicators.get_ema_structure(data)
        
        # Calculate proximity to trend EMA (200)
        current_price = data['close'].iloc[-1]
        trend_ema = ema_struct.ema_trend
        
        if trend_ema > 0:
            ema_proximity = (current_price - trend_ema) / trend_ema
        else:
            ema_proximity = 0
        
        result['ema_proximity'] = ema_proximity
        
        # Check if was in downtrend
        lookback_data = data.tail(self.downtrend_days)
        early_price = lookback_data['close'].iloc[:30].mean()
        late_price = lookback_data['close'].iloc[-30:].mean()
        
        was_downtrend = early_price > late_price * 1.1  # 10%+ decline
        
        # Count reversal signals
        reversal_signals = 0
        
        # Signal 1: Price near/above trend EMA after being below
        if abs(ema_proximity) <= self.ema_proximity_pct:
            reversal_signals += 1
        
        # Signal 2: First higher high in a while
        recent_highs = self.trend_indicators.find_swing_highs(data.tail(60))
        if len(recent_highs) >= 2:
            if recent_highs[-1][1] > recent_highs[-2][1]:
                reversal_signals += 1
        
        # Signal 3: Volume increasing on up days
        vol_analysis = self.volume_indicators.get_volume_analysis(data)
        if vol_analysis.up_volume_ratio > 0.55:  # More volume on up days
            reversal_signals += 1
        
        # Signal 4: VPES turning positive
        vpes_result = self.vpes.get_current_vpes(data)
        if vpes_result.cumulative_vpes > 0:
            reversal_signals += 1
        
        result['reversal_signals'] = reversal_signals
        
        # Estimate downtrend duration
        if was_downtrend:
            result['downtrend_duration'] = self.downtrend_days
        
        # Calculate confidence
        confidence = 0.0
        
        # Prior downtrend (weight: 0.3)
        if was_downtrend:
            confidence += 0.3
        
        # EMA proximity (weight: 0.3)
        if abs(ema_proximity) <= self.ema_proximity_pct:
            confidence += 0.3
        elif abs(ema_proximity) <= self.ema_proximity_pct * 2:
            confidence += 0.15
        
        # Reversal signals (weight: 0.4)
        confidence += min(reversal_signals * 0.1, 0.4)
        
        result['confidence'] = confidence
        result['is_type_c'] = (
            was_downtrend and
            abs(ema_proximity) <= self.ema_proximity_pct * 2 and
            reversal_signals >= 2
        )
        
        return result
    
    def _create_type_a_result(
        self,
        symbol: str,
        data: pd.DataFrame,
        check_result: Dict,
        uncertain: bool = False
    ) -> ClassificationResult:
        """Create Type A classification result."""
        return ClassificationResult(
            symbol=symbol,
            stock_type=StockType.TYPE_A_TREND,
            confidence=check_result['confidence'] * (0.8 if uncertain else 1.0),
            
            rally_magnitude=check_result['rally_magnitude'],
            pullback_depth=check_result['pullback_depth'],
            ema_intact=check_result['ema_intact'],
            
            range_high=0.0,
            range_low=0.0,
            range_width=0.0,
            
            downtrend_duration=0,
            ema_proximity=0.0,
            reversal_signals=0,
            
            allow_trend_trade=True,
            allow_add_position=True,
            allow_speculation=False,
            max_position_pct=self.type_a_max if not uncertain else self.type_a_max * 0.5,
            
            reasoning=f"Type A Trend: {check_result['rally_magnitude']*100:.1f}% rally, "
                     f"{check_result['pullback_depth']*100:.1f}% pullback, "
                     f"EMA {'intact' if check_result['ema_intact'] else 'weak'}"
        )
    
    def _create_type_b_result(
        self,
        symbol: str,
        data: pd.DataFrame,
        check_result: Dict,
        uncertain: bool = False
    ) -> ClassificationResult:
        """Create Type B classification result."""
        return ClassificationResult(
            symbol=symbol,
            stock_type=StockType.TYPE_B_RANGE,
            confidence=check_result['confidence'] * (0.8 if uncertain else 1.0),
            
            rally_magnitude=0.0,
            pullback_depth=0.0,
            ema_intact=False,
            
            range_high=check_result['range_high'],
            range_low=check_result['range_low'],
            range_width=check_result['range_width'],
            
            downtrend_duration=0,
            ema_proximity=0.0,
            reversal_signals=0,
            
            allow_trend_trade=False,
            allow_add_position=False,
            allow_speculation=True,
            max_position_pct=self.type_b_max,
            
            reasoning=f"Type B Range: {check_result['range_width']*100:.1f}% range, "
                     f"support={check_result['range_low']:.2f}, "
                     f"resistance={check_result['range_high']:.2f}"
        )
    
    def _create_type_c_result(
        self,
        symbol: str,
        data: pd.DataFrame,
        check_result: Dict,
        uncertain: bool = False
    ) -> ClassificationResult:
        """Create Type C classification result."""
        return ClassificationResult(
            symbol=symbol,
            stock_type=StockType.TYPE_C_REVERSAL,
            confidence=check_result['confidence'] * (0.8 if uncertain else 1.0),
            
            rally_magnitude=0.0,
            pullback_depth=0.0,
            ema_intact=False,
            
            range_high=0.0,
            range_low=0.0,
            range_width=0.0,
            
            downtrend_duration=check_result['downtrend_duration'],
            ema_proximity=check_result['ema_proximity'],
            reversal_signals=check_result['reversal_signals'],
            
            allow_trend_trade=False,
            allow_add_position=False,
            allow_speculation=True,  # Very small
            max_position_pct=self.type_c_max,
            
            reasoning=f"Type C Reversal: {check_result['reversal_signals']} signals, "
                     f"{check_result['ema_proximity']*100:.1f}% from EMA200"
        )
    
    def _unclassified_result(self, symbol: str, reason: str) -> ClassificationResult:
        """Create unclassified result."""
        return ClassificationResult(
            symbol=symbol,
            stock_type=StockType.UNCLASSIFIED,
            confidence=0.0,
            
            rally_magnitude=0.0,
            pullback_depth=0.0,
            ema_intact=False,
            
            range_high=0.0,
            range_low=0.0,
            range_width=0.0,
            
            downtrend_duration=0,
            ema_proximity=0.0,
            reversal_signals=0,
            
            allow_trend_trade=False,
            allow_add_position=False,
            allow_speculation=False,
            max_position_pct=0.0,
            
            reasoning=f"Unclassified: {reason}"
        )
    
    def reclassify_on_breakout(
        self,
        current_result: ClassificationResult,
        data: pd.DataFrame
    ) -> ClassificationResult:
        """
        Re-evaluate classification after a breakout.
        Type B or C stocks may upgrade to Type A after breakout.
        
        Args:
            current_result: Current classification
            data: Updated price data
            
        Returns:
            New classification result
        """
        if current_result.stock_type == StockType.TYPE_A_TREND:
            # Already Type A, just refresh
            return self.classify(current_result.symbol, data)
        
        # Check for breakout from range
        if current_result.stock_type == StockType.TYPE_B_RANGE:
            current_price = data['close'].iloc[-1]
            
            # Breakout above resistance
            if current_price > current_result.range_high * 1.02:  # 2% above
                # Check volume confirmation
                vol_analysis = self.volume_indicators.get_volume_analysis(data)
                if vol_analysis.state.value in ['surge', 'high']:
                    # Reclassify
                    return self.classify(current_result.symbol, data)
        
        # Check for confirmed reversal
        if current_result.stock_type == StockType.TYPE_C_REVERSAL:
            ema_struct = self.trend_indicators.get_ema_structure(data)
            
            # Price sustainably above trend EMA
            if ema_struct.price_vs_trend > 5:  # 5% above EMA200
                trend_state = self.trend_indicators.get_trend_state(data)
                
                if trend_state.direction in [TrendDirection.UP, TrendDirection.STRONG_UP]:
                    return self.classify(current_result.symbol, data)
        
        return current_result
