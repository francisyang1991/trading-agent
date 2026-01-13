"""
Signal Engine
Core signal generation combining multiple indicators and timeframes.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger

from ..indicators.vpes import VPES, VPESResult, VPESAnalyzer
from ..indicators.trend import TrendIndicators, TrendState, TrendDirection, EMAAlignment
from ..indicators.volume import VolumeIndicators, VolumeAnalysis, VolumeState
from ..classifier.stock_classifier import StockClassifier, ClassificationResult, StockType


class SignalType(Enum):
    """Signal types."""
    ENTRY_LONG = "entry_long"
    ADD_LONG = "add_long"
    EXIT_LONG = "exit_long"
    PARTIAL_EXIT = "partial_exit"
    HOLD = "hold"
    NO_ACTION = "no_action"


@dataclass
class Signal:
    """Trading signal container."""
    symbol: str
    signal_type: SignalType
    timestamp: datetime
    price: float
    
    # Signal quality
    strength: float              # 0-1 signal strength
    confidence: float            # 0-1 confidence level
    
    # Components
    trend_score: float           # Trend alignment score
    vpes_score: float            # VPES score
    volume_score: float          # Volume confirmation score
    multi_tf_score: float        # Multi-timeframe alignment
    news_score: float            # News sentiment (if available)
    
    # Total weighted score
    total_score: float
    
    # Classification context
    stock_type: StockType
    
    # Metadata
    reasoning: str
    indicators: Dict = field(default_factory=dict)
    
    def is_actionable(self, min_score: float = 2.0) -> bool:
        """Check if signal meets minimum score threshold."""
        return self.total_score >= min_score


class SignalEngine:
    """
    Main signal generation engine.
    Combines indicators, classification, and multi-timeframe analysis.
    """
    
    # Scoring weights
    WEIGHTS = {
        'trend': 0.25,
        'vpes': 0.25,
        'volume': 0.20,
        'multi_tf': 0.20,
        'news': 0.10
    }
    
    def __init__(
        self,
        classifier: Optional[StockClassifier] = None,
        trend_indicators: Optional[TrendIndicators] = None,
        vpes: Optional[VPES] = None,
        volume_indicators: Optional[VolumeIndicators] = None,
        min_entry_score: float = 2.0,
        min_add_score: float = 2.5,
        min_timeframes_aligned: int = 3
    ):
        """
        Initialize signal engine.
        
        Args:
            classifier: Stock classifier instance
            trend_indicators: Trend indicator calculator
            vpes: VPES calculator
            volume_indicators: Volume calculator
            min_entry_score: Minimum score for entry
            min_add_score: Minimum score for adding
            min_timeframes_aligned: Required aligned timeframes
        """
        self.classifier = classifier or StockClassifier()
        self.trend = trend_indicators or TrendIndicators()
        self.vpes = vpes or VPES()
        self.volume = volume_indicators or VolumeIndicators()
        self.vpes_analyzer = VPESAnalyzer(self.vpes)
        
        self.min_entry_score = min_entry_score
        self.min_add_score = min_add_score
        self.min_tf_aligned = min_timeframes_aligned
    
    def generate_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        multi_tf_data: Optional[Dict[str, pd.DataFrame]] = None,
        current_position: float = 0,
        news_score: float = 0.0
    ) -> Signal:
        """
        Generate trading signal.
        
        Args:
            symbol: Stock ticker
            data: Primary timeframe OHLCV data
            multi_tf_data: Optional multi-timeframe data
            current_position: Current position size (0 = no position)
            news_score: Optional news sentiment score (0-1)
            
        Returns:
            Signal with action recommendation
        """
        timestamp = datetime.now()
        
        if data.empty:
            return self._no_signal(symbol, timestamp, "No data")
        
        current_price = data['close'].iloc[-1]
        
        # Classify stock
        classification = self.classifier.classify(symbol, data, multi_tf_data)
        
        # Calculate component scores
        trend_score = self._calculate_trend_score(data, classification)
        vpes_score = self._calculate_vpes_score(data)
        volume_score = self._calculate_volume_score(data)
        multi_tf_score = self._calculate_multi_tf_score(multi_tf_data) if multi_tf_data else 0.5
        
        # Weighted total (each component is 0-1, total is 0-5 scale)
        total_score = (
            trend_score * self.WEIGHTS['trend'] +
            vpes_score * self.WEIGHTS['vpes'] +
            volume_score * self.WEIGHTS['volume'] +
            multi_tf_score * self.WEIGHTS['multi_tf'] +
            news_score * self.WEIGHTS['news']
        ) * 5  # Scale to 0-5
        
        # Determine signal type based on classification and scores
        signal_type, reasoning = self._determine_signal_type(
            classification=classification,
            total_score=total_score,
            current_position=current_position,
            trend_score=trend_score,
            vpes_score=vpes_score,
            volume_score=volume_score,
            multi_tf_score=multi_tf_score,
            data=data
        )
        
        # Calculate confidence
        confidence = min(total_score / 3.0, 1.0)  # Max confidence at score 3.0
        
        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            timestamp=timestamp,
            price=current_price,
            strength=min(total_score / 5.0, 1.0),
            confidence=confidence,
            trend_score=trend_score,
            vpes_score=vpes_score,
            volume_score=volume_score,
            multi_tf_score=multi_tf_score,
            news_score=news_score,
            total_score=total_score,
            stock_type=classification.stock_type,
            reasoning=reasoning,
            indicators=self._collect_indicator_snapshot(data)
        )
    
    def _calculate_trend_score(
        self,
        data: pd.DataFrame,
        classification: ClassificationResult
    ) -> float:
        """Calculate trend alignment score (0-1)."""
        trend_state = self.trend.get_trend_state(data)
        ema_struct = self.trend.get_ema_structure(data)
        
        score = 0.0
        
        # EMA alignment
        if ema_struct.alignment == EMAAlignment.BULLISH:
            score += 0.4
        elif ema_struct.alignment == EMAAlignment.MIXED and ema_struct.price_vs_fast > 0:
            score += 0.2
        
        # Higher highs/lows
        if trend_state.higher_highs and trend_state.higher_lows:
            score += 0.3
        elif trend_state.higher_lows:
            score += 0.15
        
        # Trend direction
        if trend_state.direction == TrendDirection.STRONG_UP:
            score += 0.3
        elif trend_state.direction == TrendDirection.UP:
            score += 0.2
        elif trend_state.direction == TrendDirection.NEUTRAL:
            score += 0.1
        
        return min(score, 1.0)
    
    def _calculate_vpes_score(self, data: pd.DataFrame) -> float:
        """Calculate VPES score (0-1)."""
        vpes_result = self.vpes.get_current_vpes(data)
        
        score = 0.0
        
        # Current VPES value
        if vpes_result.vpes >= 0.02:
            score += 0.4
        elif vpes_result.vpes >= 0.01:
            score += 0.3
        elif vpes_result.vpes >= 0.005:
            score += 0.2
        elif vpes_result.vpes > 0:
            score += 0.1
        
        # VPES signal strength
        if vpes_result.signal_strength == "strong":
            score += 0.3
        elif vpes_result.signal_strength == "moderate":
            score += 0.2
        elif vpes_result.signal_strength == "weak":
            score += 0.1
        
        # Cumulative VPES (momentum)
        if vpes_result.cumulative_vpes > 0.05:
            score += 0.3
        elif vpes_result.cumulative_vpes > 0.02:
            score += 0.2
        elif vpes_result.cumulative_vpes > 0:
            score += 0.1
        
        return min(score, 1.0)
    
    def _calculate_volume_score(self, data: pd.DataFrame) -> float:
        """Calculate volume confirmation score (0-1)."""
        vol_analysis = self.volume.get_volume_analysis(data)
        
        score = 0.0
        
        # Volume state
        if vol_analysis.state == VolumeState.SURGE:
            score += 0.4
        elif vol_analysis.state == VolumeState.HIGH:
            score += 0.3
        elif vol_analysis.state == VolumeState.NORMAL:
            score += 0.15
        # LOW/DRY = 0
        
        # Volume trend
        if vol_analysis.trend_volume == "increasing":
            score += 0.3
        elif vol_analysis.trend_volume == "stable":
            score += 0.15
        
        # Up volume ratio
        if vol_analysis.up_volume_ratio > 0.6:
            score += 0.3
        elif vol_analysis.up_volume_ratio > 0.5:
            score += 0.15
        
        return min(score, 1.0)
    
    def _calculate_multi_tf_score(
        self,
        multi_tf_data: Dict[str, pd.DataFrame]
    ) -> float:
        """Calculate multi-timeframe alignment score (0-1)."""
        if not multi_tf_data:
            return 0.5  # Neutral if no multi-TF data
        
        aligned_count = 0
        total_count = 0
        
        for tf, data in multi_tf_data.items():
            if data.empty:
                continue
            
            total_count += 1
            trend_state = self.trend.get_trend_state(data)
            
            # Count as aligned if uptrend
            if trend_state.direction in [TrendDirection.UP, TrendDirection.STRONG_UP]:
                aligned_count += 1
            elif trend_state.direction == TrendDirection.NEUTRAL:
                aligned_count += 0.5
        
        if total_count == 0:
            return 0.5
        
        alignment_ratio = aligned_count / total_count
        
        # Bonus for meeting minimum aligned requirement
        if aligned_count >= self.min_tf_aligned:
            return min(alignment_ratio + 0.2, 1.0)
        
        return alignment_ratio
    
    def _determine_signal_type(
        self,
        classification: ClassificationResult,
        total_score: float,
        current_position: float,
        trend_score: float,
        vpes_score: float,
        volume_score: float,
        multi_tf_score: float,
        data: pd.DataFrame
    ) -> Tuple[SignalType, str]:
        """
        Determine signal type based on context.
        
        Returns:
            (signal_type, reasoning)
        """
        # Check if trading allowed
        if classification.stock_type == StockType.UNCLASSIFIED:
            return SignalType.NO_ACTION, "Stock unclassified - no trading"
        
        # Type B (Range) - only speculation
        if classification.stock_type == StockType.TYPE_B_RANGE:
            if classification.allow_speculation:
                # Check if near support
                current_price = data['close'].iloc[-1]
                if current_price <= classification.range_low * 1.02:
                    if total_score >= 1.5:
                        return SignalType.ENTRY_LONG, f"Near range support (${classification.range_low:.2f})"
            return SignalType.NO_ACTION, "Type B - no trend trading allowed"
        
        # Type C (Reversal) - observation only
        if classification.stock_type == StockType.TYPE_C_REVERSAL:
            if total_score >= 2.5 and vpes_score >= 0.7:
                return SignalType.ENTRY_LONG, f"Type C reversal signal - tiny position only"
            return SignalType.HOLD, "Type C - observing for reversal confirmation"
        
        # Type A (Trend) - full trading allowed
        if classification.stock_type == StockType.TYPE_A_TREND:
            # Exit signals
            exit_signal = self._check_exit_conditions(data, classification)
            if exit_signal[0]:
                return exit_signal[1], exit_signal[2]
            
            # No position - check entry
            if current_position == 0:
                if total_score >= self.min_entry_score:
                    if self._check_entry_conditions(data, classification):
                        return SignalType.ENTRY_LONG, f"Entry signal: score={total_score:.2f}"
                return SignalType.NO_ACTION, f"Score {total_score:.2f} below entry threshold {self.min_entry_score}"
            
            # Has position - check add or hold
            else:
                if total_score >= self.min_add_score:
                    if self._check_add_conditions(data, classification, current_position):
                        return SignalType.ADD_LONG, f"Add signal: score={total_score:.2f}"
                return SignalType.HOLD, f"Holding - no add signal (score={total_score:.2f})"
        
        return SignalType.NO_ACTION, "No signal generated"
    
    def _check_entry_conditions(
        self,
        data: pd.DataFrame,
        classification: ClassificationResult
    ) -> bool:
        """
        Check specific entry conditions.
        
        Entry model: Rally → Pullback → Consolidation → Breakout → EMA Retest → Continue
        """
        # Check EMA retest
        is_retesting, quality = self.trend.detect_ema_retest(data, "fast", 2.0)
        
        if is_retesting and quality in ['exact', 'close']:
            # Volume should be low during retest (consolidation)
            vol_analysis = self.volume.get_volume_analysis(data)
            
            # Accept if volume is contracting
            if vol_analysis.state in [VolumeState.NORMAL, VolumeState.LOW]:
                return True
        
        # Alternative: Breakout with volume
        vpes_result = self.vpes.get_current_vpes(data)
        
        if vpes_result.vpes >= 0.01 and vpes_result.volume_ratio >= 1.5:
            # Breakout above recent high
            recent_high = data['high'].tail(10).max()
            current_close = data['close'].iloc[-1]
            
            if current_close >= recent_high:
                return True
        
        return False
    
    def _check_add_conditions(
        self,
        data: pd.DataFrame,
        classification: ClassificationResult,
        current_position: float
    ) -> bool:
        """
        Check conditions for adding to position.
        Must be stricter than entry.
        """
        if current_position >= classification.max_position_pct:
            return False  # Already at max
        
        # Require new high
        recent_high = data['high'].tail(20).max()
        current_close = data['close'].iloc[-1]
        
        if current_close < recent_high * 0.99:  # Not at new high
            return False
        
        # Require stronger VPES
        vpes_result = self.vpes.get_current_vpes(data)
        
        if vpes_result.vpes < 0.015 or vpes_result.volume_ratio < 1.5:
            return False  # Volume not confirming
        
        # Trend must be intact
        if not self.trend.is_trend_intact(data, TrendDirection.UP):
            return False
        
        return True
    
    def _check_exit_conditions(
        self,
        data: pd.DataFrame,
        classification: ClassificationResult
    ) -> Tuple[bool, SignalType, str]:
        """
        Check exit conditions.
        
        Returns:
            (should_exit, signal_type, reason)
        """
        # Hard stop: Break swing low
        swing_lows = self.trend.find_swing_lows(data.tail(30))
        if swing_lows:
            recent_swing_low = swing_lows[-1][1]
            current_close = data['close'].iloc[-1]
            
            if current_close < recent_swing_low:
                return True, SignalType.EXIT_LONG, f"Broke swing low ${recent_swing_low:.2f}"
        
        # Hard stop: Break key EMA
        ema_struct = self.trend.get_ema_structure(data)
        
        if ema_struct.price_vs_slow < -3:  # 3% below slow EMA
            # Check if failed to recover
            last_3_bars = data.tail(3)
            all_below = (last_3_bars['close'] < ema_struct.ema_slow).all()
            
            if all_below:
                return True, SignalType.EXIT_LONG, f"Failed to recover above EMA50"
        
        # Partial exit: VPES divergence
        has_divergence, div_type = self.vpes.detect_divergence(data)
        
        if has_divergence and div_type == "bearish":
            return True, SignalType.PARTIAL_EXIT, "Bearish VPES divergence"
        
        # Partial exit: Near resistance with weakening VPES
        vpes_result = self.vpes.get_current_vpes(data)
        trend_state = self.trend.get_trend_state(data)
        
        if vpes_result.vpes_ma < 0.005 and vpes_result.cumulative_vpes < 0:
            if data['close'].iloc[-1] >= trend_state.resistance_level * 0.98:
                return True, SignalType.PARTIAL_EXIT, "Near resistance with weak VPES"
        
        return False, SignalType.HOLD, ""
    
    def _collect_indicator_snapshot(self, data: pd.DataFrame) -> Dict:
        """Collect current indicator values for logging."""
        if data.empty:
            return {}
        
        ema_struct = self.trend.get_ema_structure(data)
        trend_state = self.trend.get_trend_state(data)
        vpes_result = self.vpes.get_current_vpes(data)
        vol_analysis = self.volume.get_volume_analysis(data)
        
        return {
            'price': data['close'].iloc[-1],
            'ema_fast': ema_struct.ema_fast,
            'ema_slow': ema_struct.ema_slow,
            'ema_trend': ema_struct.ema_trend,
            'ema_alignment': ema_struct.alignment.value,
            'trend_direction': trend_state.direction.value,
            'trend_strength': trend_state.trend_strength,
            'vpes': vpes_result.vpes,
            'vpes_cumulative': vpes_result.cumulative_vpes,
            'volume_ratio': vpes_result.volume_ratio,
            'volume_state': vol_analysis.state.value,
        }
    
    def _no_signal(self, symbol: str, timestamp: datetime, reason: str) -> Signal:
        """Create no-action signal."""
        return Signal(
            symbol=symbol,
            signal_type=SignalType.NO_ACTION,
            timestamp=timestamp,
            price=0.0,
            strength=0.0,
            confidence=0.0,
            trend_score=0.0,
            vpes_score=0.0,
            volume_score=0.0,
            multi_tf_score=0.0,
            news_score=0.0,
            total_score=0.0,
            stock_type=StockType.UNCLASSIFIED,
            reasoning=reason,
            indicators={}
        )
