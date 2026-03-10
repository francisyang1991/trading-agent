"""
Volume-Confirmed EMA9 Pullback Entry Signal (Trend Reversal Breakout)
=====================================================================

HIGH PRIORITY PATTERN - User identified 2026-01-16

Pattern Description (from UMAC chart analysis):
1. Prior Downtrend: Stock was in downtrend with resistance high (5-10 days ago)
2. Breakout Push: 3+ green candles with INCREASING volume BREAKING ABOVE prior resistance
3. Pullback: Higher low near EMA9 with DECREASING volume (healthy consolidation, not distribution)
4. Trigger: Green candle with BIGGER volume than pullback (volume expansion = confirmation)
5. Target: Previous push high where there was huge volume resistance
6. Exit Strategy: 
   - Sell 50% at previous high resistance
   - Wait for consolidation
   - Re-add near EMA9

Why this pattern works:
- Prior resistance becomes support after breakout
- Volume declining on pullback = no selling pressure (institutions holding)
- Volume expanding on trigger = buyers stepping in
- Higher low + held support = breakout confirmed
- Clear risk/reward with defined levels

Best for:
- Trend reversal plays
- Momentum stocks in early/mid stage moves
- Stocks breaking out of bases/downtrends
- High beta stocks with volume characteristics
"""

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ...core.types import Signal, SignalType, Regime, VolatilityLevel, Strategy


@dataclass
class VolumePullbackConfig:
    """Configuration for Volume-Confirmed EMA9 Pullback signals."""
    # EMAs
    ema_fast: int = 9      # Primary support for pullback
    ema_medium: int = 21   # Secondary support
    ema_trend: int = 50    # Trend confirmation
    
    # Initial Push Detection
    min_green_candles: int = 3           # Minimum consecutive green candles
    min_volume_increase_ratio: float = 1.0  # Each candle's vol >= prev * this
    
    # Pullback Criteria
    max_pullback_bars: int = 7           # Max bars for pullback (tight = better)
    ema9_tolerance_pct: float = 2.0      # Proximity to EMA9 (%)
    max_pullback_pct: float = 18.0       # Max pullback from recent high
    require_pullback_above_ema9: bool = True  # Pullback low must stay near EMA9
    max_below_ema9_pct: float = 4.0      # Allowable dip below EMA9 (percent)
    
    # Volume Divergence (KEY!)
    pullback_vol_ratio: float = 0.7      # Pullback volume should be < 70% of push volume
    
    # Breakout Candle
    min_breakout_vol_ratio: float = 1.1  # Breakout vol > 1.1x pullback avg volume
    
    # Signal confidence
    min_confidence: float = 0.60
    min_confidence_tradeable: float = 0.75
    min_risk_reward_tradeable: float = 1.2
    
    # Prior resistance (trend reversal context)
    require_prior_resistance: bool = True
    prior_resistance_lookback_min: int = 5    # Min bars before push start
    prior_resistance_lookback_max: int = 15   # Max bars before push start
    min_breakout_pct: float = 1.0             # Breakout % above prior resistance
    support_hold_tolerance_pct: float = 8.0   # Allow 8% dip below prior resistance
    
    # Target resistance lookback (longer window)
    target_resistance_lookback_min: int = 15
    target_resistance_lookback_max: int = 60
    max_target1_pct: Optional[float] = 12.0
    min_target1_atr: float = 0.5
    min_target1_pct: float = 2.0
    resistance_lookback_bars: int = 120
    resistance_tolerance_pct: float = 1.2
    resistance_min_touches: int = 2
    target_method: str = "resistance"  # resistance | measured_move | fib | auto
    measured_move_mult1: float = 1.0
    measured_move_mult2: float = 1.5
    fib_ext_1: float = 1.382
    fib_ext_2: float = 1.618

    # Breakout exhaustion & support hold scoring
    max_breakout_vol_ratio: float = 2.2
    blowoff_penalty: int = -12
    support_hold_score: int = 18
    
    # Target spacing
    min_target2_atr: float = 1.0
    min_target2_pct: float = 3.0
    
    # Range-bound detection (mean reversion regime)
    range_lookback_bars: int = 30
    range_bound_pct: float = 8.0
    range_slope_pct: float = 2.0
    
    # Partial exit
    partial_exit_at_target1: bool = True
    partial_exit_size: float = 0.5
    move_stop_to_entry_on_partial: bool = True

    # Stop loss tuning
    stop_loss_pct_min: float = 5.0
    stop_loss_pct_max: float = 8.0
    stop_loss_score_min: float = 55.0
    stop_loss_score_max: float = 85.0

    # Regime filter
    reject_range_bound: bool = False

    # Downtrend context
    require_downtrend_move: bool = True
    downtrend_lookback_bars: int = 20
    min_downtrend_pct: float = 5.0
    require_bottom_confirmation_in_downtrend: bool = True
    double_bottom_tolerance_pct: float = 2.0
    min_bottom_volume_ratio: float = 1.2
    
    # Trigger volume requirement (entry must be stronger than pullback)
    require_trigger_volume_over_pullback: bool = True
    min_trigger_vs_pullback_ratio: float = 1.0  # Trigger volume >= pullback avg volume

    # Pullback structure flexibility
    allow_multi_pullback_rounds: bool = True
    max_pullback_bars_extended: int = 12
    min_pullback_ema9_touch_ratio: float = 0.65
    allow_shallow_pullback: bool = True
    shallow_pullback_pct: float = 6.0
    max_shallow_ema9_distance_pct: float = 6.0

    # Candle strength (body vs wick)
    min_trigger_body_ratio: float = 0.5
    max_trigger_upper_wick_ratio: float = 0.45
    min_push_body_ratio: float = 0.5
    max_push_upper_wick_ratio: float = 0.5

    # Strong push override when volume is mediocre
    allow_strong_green_override_volume: bool = True
    strong_green_override_min: int = 5

    # Volume override when trigger body is strong
    allow_strong_body_override_volume: bool = True
    min_trigger_body_ratio_override: float = 0.6

    # Pullback absorption (high volume + small red bodies above breakout)
    allow_pullback_absorption_override: bool = True
    pullback_small_body_ratio: float = 0.35
    min_pullback_absorption_ratio: float = 0.35


class VolumePullbackEntrySignal:
    """
    Generate HIGH PRIORITY entry signals for Volume-Confirmed EMA9 Pullback.
    
    This is a premium pattern with high win rate when properly identified.
    
    Pattern Sequence:
    1. PUSH PHASE: 3+ green candles, each with higher volume than previous
    2. PULLBACK PHASE: Price pulls back to EMA9 with LOWER volume (key!)
    3. TRIGGER: Green candle with volume expansion signals entry
    
    Exit Strategy (built into signal metadata):
    - Target 1: Previous high (sell 50%)
    - Target 2: 1.5x measured move
    - Re-entry: Wait for consolidation near EMA9
    
    Example:
        signal_gen = VolumePullbackEntrySignal()
        signal = signal_gen.generate('UMAC', price_data)
        
        if signal and signal.confidence > 0.75:
            # HIGH CONVICTION ENTRY
            execute_entry(signal)
    """
    
    def __init__(self, config: Optional[VolumePullbackConfig] = None):
        self.config = config or VolumePullbackConfig()

    def _candle_metrics(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
    ) -> Tuple[float, float, float]:
        """Return (body_ratio, upper_wick_ratio, lower_wick_ratio)."""
        candle_range = max(0.0000001, high - low)
        body = abs(close - open_price)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        body_ratio = body / candle_range
        upper_wick_ratio = max(0.0, upper_wick) / candle_range
        lower_wick_ratio = max(0.0, lower_wick) / candle_range
        return body_ratio, upper_wick_ratio, lower_wick_ratio

    def _max_confidence_score(self) -> float:
        """Approximate max possible score for confidence normalization."""
        return (
            25  # push_phase
            + 10  # breakout_strength
            + 12  # push_strength
            + 20  # pullback_structure
            + 8   # multi_round_pullback
            + 6   # shallow_pullback
            + 25  # volume_divergence
            + 20  # trigger_candle
            + 10  # ema9_support
            + 12  # trigger_body
            + self.config.support_hold_score
            + 10  # breakout_exhaustion
            + 10  # double_bottom
        )
    
    def generate(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM
    ) -> Optional[Signal]:
        """
        Generate Volume-Confirmed EMA9 Pullback entry signal.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            regime: Current market regime
            volatility: Current volatility level
            
        Returns:
            Signal if pattern criteria met, None otherwise
        """
        if len(data) < self.config.ema_trend + 20:
            return None
        
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        open_price = data['open'] if 'open' in data.columns else data['Open']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        ema9 = close.ewm(span=self.config.ema_fast, adjust=False).mean()
        ema21 = close.ewm(span=self.config.ema_medium, adjust=False).mean()
        ema50 = close.ewm(span=self.config.ema_trend, adjust=False).mean()
        volume_ma = volume.rolling(window=20).mean()
        atr = self._calculate_atr(high, low, close, 14)
        
        return self._generate_from_series(
            symbol=symbol,
            close=close,
            open_price=open_price,
            high=high,
            low=low,
            volume=volume,
            ema9=ema9,
            ema21=ema21,
            ema50=ema50,
            volume_ma=volume_ma,
            atr=atr,
            regime=regime,
            volatility=volatility,
        )

    def generate_at_index(
        self,
        symbol: str,
        data: pd.DataFrame,
        end_idx: int,
        indicators: Optional[Dict[str, pd.Series]] = None,
        regime: Regime = Regime.UNKNOWN,
        volatility: VolatilityLevel = VolatilityLevel.MEDIUM,
    ) -> Optional[Signal]:
        """
        Generate signal at a specific index using optional precomputed indicators.
        """
        if len(data) < self.config.ema_trend + 20:
            return None
        
        close = data['close'] if 'close' in data.columns else data['Close']
        high = data['high'] if 'high' in data.columns else data['High']
        low = data['low'] if 'low' in data.columns else data['Low']
        open_price = data['open'] if 'open' in data.columns else data['Open']
        volume = data['volume'] if 'volume' in data.columns else data['Volume']
        
        end_idx = self._resolve_end_idx(close, end_idx)
        
        if indicators is None:
            ema9 = close.ewm(span=self.config.ema_fast, adjust=False).mean()
            ema21 = close.ewm(span=self.config.ema_medium, adjust=False).mean()
            ema50 = close.ewm(span=self.config.ema_trend, adjust=False).mean()
            volume_ma = volume.rolling(window=20).mean()
            atr = self._calculate_atr(high, low, close, 14)
        else:
            ema9 = indicators["ema9"]
            ema21 = indicators["ema21"]
            ema50 = indicators["ema50"]
            volume_ma = indicators["volume_ma"]
            atr = indicators["atr"]
        
        # Slice to current end_idx for consistent behavior
        close = close.iloc[: end_idx + 1]
        high = high.iloc[: end_idx + 1]
        low = low.iloc[: end_idx + 1]
        open_price = open_price.iloc[: end_idx + 1]
        volume = volume.iloc[: end_idx + 1]
        ema9 = ema9.iloc[: end_idx + 1]
        ema21 = ema21.iloc[: end_idx + 1]
        ema50 = ema50.iloc[: end_idx + 1]
        volume_ma = volume_ma.iloc[: end_idx + 1]
        atr = atr.iloc[: end_idx + 1]
        
        return self._generate_from_series(
            symbol=symbol,
            close=close,
            open_price=open_price,
            high=high,
            low=low,
            volume=volume,
            ema9=ema9,
            ema21=ema21,
            ema50=ema50,
            volume_ma=volume_ma,
            atr=atr,
            regime=regime,
            volatility=volatility,
        )

    def _generate_from_series(
        self,
        symbol: str,
        close: pd.Series,
        open_price: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series,
        ema9: pd.Series,
        ema21: pd.Series,
        ema50: pd.Series,
        volume_ma: pd.Series,
        atr: pd.Series,
        regime: Regime,
        volatility: VolatilityLevel,
    ) -> Optional[Signal]:
        """Core signal generation using precomputed series."""
        # Current values
        current_price = close.iloc[-1]
        current_open = open_price.iloc[-1]
        current_high = high.iloc[-1]
        current_low = low.iloc[-1]
        current_volume = volume.iloc[-1]
        current_ema9 = ema9.iloc[-1]
        current_ema21 = ema21.iloc[-1]
        current_ema50 = ema50.iloc[-1]
        current_volume_ma = volume_ma.iloc[-1]
        
        # Check if current candle is green (potential trigger)
        is_green_candle = current_price > current_open
        
        if not is_green_candle:
            return None  # Need green candle for trigger

        trigger_body_ratio, trigger_upper_wick_ratio, trigger_lower_wick_ratio = self._candle_metrics(
            float(current_open),
            float(current_high),
            float(current_low),
            float(current_price),
        )
        
        # ============================================================
        # PATTERN DETECTION
        # ============================================================
        
        scores = {}
        reasons = []
        
        # 1. Find the initial push (3+ green candles with increasing volume)
        push_info = self._find_push_phase(close, open_price, volume, high, low)
        
        if push_info is None:
            return None  # No valid push found
        
        push_start_idx = push_info["start_idx"]
        push_end_idx = push_info["end_idx"]
        push_high = push_info["push_high"]
        push_avg_volume = push_info["push_avg_volume"]
        scores["push_phase"] = 25
        reasons.append(f"Push: {push_info['green_candles']} green candles")
        strong_green_count = int(push_info.get("strong_green_count", 0))
        avg_push_body_ratio = float(push_info.get("avg_body_ratio", 0.0))
        strong_push = (
            self.config.allow_strong_green_override_volume
            and strong_green_count >= self.config.strong_green_override_min
        )
        if strong_push:
            scores["push_strength"] = 12
            reasons.append(f"Strong push bodies ({strong_green_count} strong greens)")
        elif avg_push_body_ratio >= self.config.min_push_body_ratio:
            scores["push_strength"] = 6
            reasons.append("Healthy push candle bodies")
        
        # 1.5 Prior resistance check (trend reversal context)
        prior_info = self._find_prior_resistance(high, close, push_start_idx)
        prior_resistance = None
        prior_resistance_idx = None
        target_resistance = None
        target_resistance_idx = None
        breakout_pct = 0.0
        was_downtrend = False
        downtrend_pct = 0.0
        
        if push_start_idx > 0:
            lookback_idx = max(0, push_start_idx - self.config.downtrend_lookback_bars)
            base_price = float(close.iloc[lookback_idx])
            if base_price > 0:
                downtrend_pct = (close.iloc[push_start_idx] - base_price) / base_price * 100
        
        if prior_info is None and self.config.require_prior_resistance:
            return None
        
        if prior_info is not None:
            prior_resistance, prior_resistance_idx = prior_info
            target_info = self._find_target_resistance(high, push_start_idx)
            if target_info is not None:
                target_resistance, target_resistance_idx = target_info
            breakout_pct = (
                (push_high - prior_resistance) / prior_resistance * 100
                if prior_resistance > 0
                else 0.0
            )
            was_downtrend = close.iloc[push_start_idx] < prior_resistance * 0.98
            downtrend_ok = downtrend_pct <= -self.config.min_downtrend_pct
            
            broke_resistance = push_high > prior_resistance
            if self.config.require_prior_resistance and not broke_resistance:
                return None
            if self.config.require_prior_resistance and not was_downtrend:
                return None
            if self.config.require_downtrend_move and not downtrend_ok:
                return None
            
            if breakout_pct >= self.config.min_breakout_pct:
                scores["breakout_strength"] = 10
                reasons.append(f"Breakout +{breakout_pct:.1f}% above prior resistance")
        
        # Entry 1: second green breakout candle with volume
        green_indices = push_info.get("green_indices", [])
        entry1_idx = green_indices[1] if len(green_indices) > 1 else push_start_idx
        
        if prior_resistance is not None and green_indices:
            breakout_greens = [i for i in green_indices if close.iloc[i] > prior_resistance]
            breakout_with_vol = [
                i for i in breakout_greens
                if i > 0 and volume.iloc[i] >= volume.iloc[i - 1]
            ]
            if len(breakout_with_vol) >= 2:
                entry1_idx = breakout_with_vol[1]
            elif len(breakout_with_vol) == 1:
                entry1_idx = breakout_with_vol[0]
            elif len(breakout_greens) >= 2:
                entry1_idx = breakout_greens[1]
            elif len(breakout_greens) == 1:
                entry1_idx = breakout_greens[0]
        
        entry1_price = float(close.iloc[entry1_idx])
        entry1_volume = float(volume.iloc[entry1_idx])
        entry1_volume_ratio = (
            entry1_volume / float(volume.iloc[entry1_idx - 1])
            if entry1_idx > 0
            else 1.0
        )
        
        # 2. Check for pullback to EMA9 with low volume
        pullback_info = self._check_pullback_phase(
            close,
            high,
            low,
            open_price,
            volume,
            ema9,
            push_end_idx,
            push_high,
            push_avg_volume,
            prior_resistance,
        )
        
        if pullback_info is None:
            return None  # No valid pullback
        
        pullback_low = pullback_info["pullback_low"]
        pullback_avg_volume = pullback_info["pullback_avg_volume"]
        pullback_bars = pullback_info["pullback_bars"]
        higher_low = pullback_info["higher_low"]
        pullback_start_idx = pullback_info["pullback_start_idx"]
        pullback_end_idx = pullback_info["pullback_end_idx"]
        pullback_pct = pullback_info["pullback_pct"]
        dist_to_ema9_pct = pullback_info["dist_to_ema9_pct"]
        near_ema9_ratio = pullback_info.get("near_ema9_ratio", 0.0)
        contraction_rounds = pullback_info.get("contraction_rounds", 0)
        pullback_absorption_ratio = pullback_info.get("pullback_absorption_ratio", 0.0)
        double_bottom_confirmed = pullback_info.get("double_bottom_confirmed", False)
        bottom_volume_ratio = pullback_info.get("bottom_volume_ratio", 0.0)
        
        pullback_held_support = True
        if prior_resistance is not None:
            support_floor = prior_resistance * (1 - self.config.support_hold_tolerance_pct / 100)
            pullback_held_support = pullback_low >= support_floor
        
        if not higher_low:
            scores["pullback_structure"] = 15
        else:
            scores["pullback_structure"] = 20
            reasons.append("Higher low confirmed")
        
        if self.config.allow_shallow_pullback and pullback_pct <= self.config.shallow_pullback_pct:
            scores["shallow_pullback"] = 6
            reasons.append(f"Shallow pullback ({pullback_pct:.1f}%)")
        
        if double_bottom_confirmed:
            scores["double_bottom"] = 10
            reasons.append(f"Double bottom volume support ({bottom_volume_ratio:.2f}x)")
        
        if contraction_rounds >= 2 and near_ema9_ratio >= self.config.min_pullback_ema9_touch_ratio:
            scores["multi_round_pullback"] = 8
            reasons.append(f"Multi-round contraction near EMA9 ({contraction_rounds} rounds)")
        
        # 3. Volume divergence check (CRITICAL!)
        vol_divergence_ratio = pullback_avg_volume / push_avg_volume if push_avg_volume > 0 else 1
        
        if vol_divergence_ratio <= self.config.pullback_vol_ratio:
            scores['volume_divergence'] = 25
            reasons.append(f"Volume divergence: {vol_divergence_ratio:.2f}x (healthy)")
        elif (
            self.config.allow_pullback_absorption_override
            and pullback_absorption_ratio >= self.config.min_pullback_absorption_ratio
        ):
            scores['volume_divergence'] = 18
            reasons.append(f"Absorption pullback ({pullback_absorption_ratio:.0%} small red bodies above breakout)")
        elif strong_push:
            scores['volume_divergence'] = 15
            reasons.append("Strong push override on pullback volume")
        elif vol_divergence_ratio <= 0.85:
            scores['volume_divergence'] = 18
            reasons.append("Moderate volume divergence")
        else:
            scores['volume_divergence'] = 5
        
        # 4. Check current candle is trigger (green with volume expansion)
        breakout_vol_ratio = current_volume / pullback_avg_volume if pullback_avg_volume > 0 else 1
        
        if self.config.require_trigger_volume_over_pullback and breakout_vol_ratio < self.config.min_trigger_vs_pullback_ratio:
            reasons.append(
                f"Trigger volume below pullback avg ({breakout_vol_ratio:.2f}x < {self.config.min_trigger_vs_pullback_ratio:.2f}x)"
            )
        
        if breakout_vol_ratio >= self.config.min_breakout_vol_ratio:
            scores['trigger_candle'] = 20
            reasons.append(f"Volume expansion: {breakout_vol_ratio:.1f}x")
        elif breakout_vol_ratio >= 1.1:
            scores['trigger_candle'] = 12
            reasons.append("Moderate volume on trigger")
        else:
            if (
                self.config.allow_strong_body_override_volume
                and trigger_body_ratio >= self.config.min_trigger_body_ratio_override
            ):
                scores['trigger_candle'] = 10
                reasons.append("Strong trigger body despite modest volume")
            else:
                scores['trigger_candle'] = 5
        
        # 5. Price near EMA9 (support proximity)
        dist_to_ema9 = abs(current_low - current_ema9) / current_ema9 * 100
        
        if dist_to_ema9 <= self.config.ema9_tolerance_pct:
            scores['ema9_support'] = 10
            reasons.append(f"At EMA9 support ({dist_to_ema9:.1f}%)")
        elif dist_to_ema9 <= self.config.ema9_tolerance_pct * 2:
            scores['ema9_support'] = 7
        else:
            scores['ema9_support'] = 3

        # 5.5 Trigger candle strength (body vs wick)
        if (
            trigger_body_ratio >= self.config.min_trigger_body_ratio
            and trigger_upper_wick_ratio <= self.config.max_trigger_upper_wick_ratio
        ):
            scores["trigger_body"] = 12
            reasons.append("Strong trigger body")
        elif trigger_body_ratio >= self.config.min_trigger_body_ratio * 0.8:
            scores["trigger_body"] = 6
            reasons.append("Moderate trigger body")
        else:
            scores["trigger_body"] = 2

        # 5.6 Support hold priority (higher weight)
        if pullback_held_support:
            scores["support_hold"] = self.config.support_hold_score
            reasons.append("Held prior resistance")
        else:
            scores["support_hold"] = 0
            reasons.append("Support break: pullback under prior resistance")

        # 5.7 No blow-off breakout priority (avoid exhaustion spikes)
        if breakout_vol_ratio <= self.config.max_breakout_vol_ratio:
            scores["breakout_exhaustion"] = 10
            reasons.append("No blow-off breakout volume")
        else:
            scores["breakout_exhaustion"] = self.config.blowoff_penalty
            reasons.append(f"Blow-off breakout volume ({breakout_vol_ratio:.1f}x)")
        
        # ============================================================
        # CALCULATE CONFIDENCE
        # ============================================================
        
        total_score = sum(scores.values())
        score = max(0.0, min(100.0, total_score))
        max_score = self._max_confidence_score()
        confidence = (total_score / max_score) if max_score > 0 else 0.0
        confidence = max(0.0, min(1.0, confidence))
        
        # Regime bonus (this pattern works best in uptrends)
        if regime in [Regime.PARABOLIC, Regime.STRONG_UP]:
            confidence *= 1.20
            reasons.append(f"Regime boost: {regime.value}")
        elif regime == Regime.MODERATE_UP:
            confidence *= 1.10
        elif regime == Regime.WEAK_UP:
            confidence *= 1.0
        elif regime == Regime.SIDEWAYS:
            confidence *= 0.85
        elif regime == Regime.DOWNTREND:
            confidence *= 0.5
        
        if volatility in [VolatilityLevel.MEDIUM, VolatilityLevel.HIGH]:
            confidence *= 1.05
        elif volatility == VolatilityLevel.EXTREME:
            confidence *= 0.9
        
        confidence = min(1.0, confidence)
        
        if confidence < self.config.min_confidence:
            return None
        
        # ============================================================
        # CALCULATE LEVELS
        # ============================================================
        
        atr_value = atr.iloc[-1]
        # Stop loss based on entry price (5-8% depending on score)
        score_for_stop = max(self.config.stop_loss_score_min, min(self.config.stop_loss_score_max, score))
        if self.config.stop_loss_score_max > self.config.stop_loss_score_min:
            t = (score_for_stop - self.config.stop_loss_score_min) / (
                self.config.stop_loss_score_max - self.config.stop_loss_score_min
            )
        else:
            t = 0.5
        stop_loss_pct = self.config.stop_loss_pct_max - t * (
            self.config.stop_loss_pct_max - self.config.stop_loss_pct_min
        )
        stop_loss = current_price * (1 - stop_loss_pct / 100)
        
        min_gap_t1 = max(
            atr_value * self.config.min_target1_atr,
            current_price * (self.config.min_target1_pct / 100),
        )
        max_gap_t1 = None
        if self.config.max_target1_pct is not None and self.config.max_target1_pct > 0:
            max_gap_t1 = current_price * (self.config.max_target1_pct / 100)
        min_gap = max(
            atr_value * self.config.min_target2_atr,
            current_price * (self.config.min_target2_pct / 100),
        )
        
        target_1 = float(push_high)
        target_2 = None
        method = self.config.target_method
        levels = self._find_resistance_levels(high, push_start_idx)
        major_levels = [c for c in levels if c["touches"] >= self.config.resistance_min_touches]
        candidates = major_levels if major_levels else levels
        candidates = [c for c in candidates if c["price"] > current_price]
        candidates.sort(key=lambda c: c["price"])
        if method == "auto":
            method = "resistance" if candidates else "measured_move"
        
        if method == "measured_move":
            height = push_high - pullback_low
            target_1 = current_price + height * self.config.measured_move_mult1
            target_2 = current_price + height * self.config.measured_move_mult2
            reasons.append("Targets from measured move")
        elif method == "fib":
            height = push_high - pullback_low
            target_1 = pullback_low + height * self.config.fib_ext_1
            target_2 = pullback_low + height * self.config.fib_ext_2
            reasons.append("Targets from Fibonacci extension")
        else:
            target_1 = float(push_high)
            reasons.append("Target1 from push high")
            for c in candidates:
                if c["price"] < current_price + min_gap_t1:
                    continue
                if max_gap_t1 is not None and c["price"] > current_price + max_gap_t1:
                    continue
                target_1 = float(c["price"])
                reasons.append("Target1 from resistance")
                break
            
            for c in candidates:
                if c["price"] >= target_1 + min_gap:
                    target_2 = float(c["price"])
                    reasons.append("Target2 from resistance")
                    break
            if target_2 is None:
                target_2 = target_1 + min_gap
                reasons.append("Target2 fallback (min distance)")
        
        target_1 = self._clamp_target(target_1, current_price, min_gap_t1, max_gap_t1, reasons, "Target1")
        if target_2 is None or target_2 < target_1 + min_gap:
            target_2 = target_1 + min_gap
            reasons.append("Target2 fallback (min distance)")
        
        risk = current_price - stop_loss
        reward_1 = target_1 - current_price
        risk_reward = reward_1 / risk if risk > 0 else 0
        
        # Range-bound detection (mean reversion regime)
        range_start = max(0, len(close) - self.config.range_lookback_bars)
        range_high = float(high.iloc[range_start:].max())
        range_low = float(low.iloc[range_start:].min())
        range_pct = (range_high - range_low) / current_price * 100 if current_price > 0 else 0.0
        ema21_start = float(ema21.iloc[range_start])
        ema21_slope = ((current_ema21 - ema21_start) / ema21_start * 100) if ema21_start > 0 else 0.0
        range_bound = range_pct <= self.config.range_bound_pct and abs(ema21_slope) <= self.config.range_slope_pct
        
        blockers: List[str] = []
        if range_bound and self.config.reject_range_bound:
            blockers.append("Range-bound: use mean reversion")
        elif range_bound:
            reasons.append("Range-bound context (mean reversion preferred)")
        if target_1 <= current_price:
            blockers.append("Late: price already at/above T1")
        if breakout_vol_ratio < self.config.min_breakout_vol_ratio:
            if not (
                self.config.allow_strong_body_override_volume
                and trigger_body_ratio >= self.config.min_trigger_body_ratio_override
            ):
                blockers.append(
                    f"Trigger vol {breakout_vol_ratio:.1f}x < {self.config.min_breakout_vol_ratio:.1f}x"
                )
        if self.config.require_trigger_volume_over_pullback and breakout_vol_ratio < self.config.min_trigger_vs_pullback_ratio:
            blockers.append(
                f"Trigger vol {breakout_vol_ratio:.2f}x < pullback avg ({self.config.min_trigger_vs_pullback_ratio:.2f}x)"
            )
        if self.config.require_prior_resistance and breakout_pct < self.config.min_breakout_pct:
            blockers.append(f"Weak breakout (+{breakout_pct:.1f}%)")
        if trigger_body_ratio < self.config.min_trigger_body_ratio:
            blockers.append(f"Weak trigger body ({trigger_body_ratio:.2f})")
        if trigger_upper_wick_ratio > self.config.max_trigger_upper_wick_ratio:
            blockers.append(f"Long trigger upper wick ({trigger_upper_wick_ratio:.2f})")
        if (
            was_downtrend
            and self.config.require_bottom_confirmation_in_downtrend
            and not (higher_low or double_bottom_confirmed)
        ):
            blockers.append("No higher low/double bottom in downtrend")
        if risk_reward < self.config.min_risk_reward_tradeable:
            blockers.append(f"Low R:R ({risk_reward:.1f}x)")
        if confidence < self.config.min_confidence_tradeable:
            blockers.append(f"Low confidence ({confidence:.2f})")
        
        is_tradeable = len(blockers) == 0
        setup_grade = "A" if is_tradeable else ("B" if "Late:" not in " ".join(blockers) else "C")
        
        return Signal(
            symbol=symbol,
            signal_type=SignalType.ENTRY,
            direction=1,
            price=current_price,
            confidence=confidence,
            reasoning="; ".join(reasons),
            stop_loss=stop_loss,
            take_profit=target_1,
            strategy=Strategy.SWING_TRADE,
            regime=regime,
            volatility=volatility,
            metadata={
                'entry_type': 'volume_pullback_ema9',
                'priority': 'HIGH',
                'scores': scores,
                'score': score,
                'reasons': reasons,
                'is_tradeable': is_tradeable,
                'setup_grade': setup_grade,
                'blockers': blockers,
                'entry1_idx': entry1_idx,
                'entry1_price': entry1_price,
                'entry1_volume_ratio': entry1_volume_ratio,
                'trigger_idx': len(close) - 1,
                'trigger_price': current_price,
                'trigger_body_ratio': trigger_body_ratio,
                'trigger_upper_wick_ratio': trigger_upper_wick_ratio,
                'trigger_lower_wick_ratio': trigger_lower_wick_ratio,
                'push_start_idx': push_start_idx,
                'push_end_idx': push_end_idx,
                'push_high': push_high,
                'push_green_candles': push_info['green_candles'],
                'push_avg_volume': push_avg_volume,
                'push_strong_green_count': strong_green_count,
                'push_avg_body_ratio': avg_push_body_ratio,
                'push_strong_override': strong_push,
                'pullback_start_idx': pullback_start_idx,
                'pullback_end_idx': pullback_end_idx,
                'pullback_low': pullback_low,
                'pullback_bars': pullback_bars,
                'pullback_avg_volume': pullback_avg_volume,
                'pullback_pct': pullback_pct,
                'dist_to_ema9_pct': dist_to_ema9_pct,
                'near_ema9_ratio': near_ema9_ratio,
                'contraction_rounds': contraction_rounds,
                'pullback_absorption_ratio': pullback_absorption_ratio,
                'double_bottom_confirmed': double_bottom_confirmed,
                'bottom_volume_ratio': bottom_volume_ratio,
                'volume_divergence_ratio': vol_divergence_ratio,
                'breakout_volume_ratio': breakout_vol_ratio,
                'blowoff_breakout': breakout_vol_ratio > self.config.max_breakout_vol_ratio,
                'range_bound': range_bound,
                'range_pct': range_pct,
                'ema21_slope_pct': ema21_slope,
                'higher_low': higher_low,
                'pullback_held_support': pullback_held_support,
                'prior_resistance': prior_resistance,
                'prior_resistance_idx': prior_resistance_idx,
                'target_resistance': target_resistance,
                'target_resistance_idx': target_resistance_idx,
                'breakout_pct': breakout_pct,
                'was_downtrend': was_downtrend,
                'downtrend_pct': downtrend_pct,
                'stop_loss_pct': stop_loss_pct,
                'target_method': self.config.target_method,
                'ema9': current_ema9,
                'ema21': current_ema21,
                'stop_loss': stop_loss,
                'target_1_50pct': target_1,
                'target_2_full': target_2,
                'risk_reward': risk_reward,
                'atr': atr_value,
                'exit_strategy': {
                    'at_target_1': 'Sell 50% at previous high resistance',
                    'after_target_1': 'Wait for consolidation',
                    're_entry': 'Add back near EMA9 after consolidation',
                }
            }
        )
    
    def _find_push_phase(
        self,
        close: pd.Series,
        open_price: pd.Series,
        volume: pd.Series,
        high: pd.Series,
        low: pd.Series,
    ) -> Optional[Dict[str, float]]:
        """
        Find the initial push phase: 3+ green candles with increasing volume.
        
        Returns:
            Dict with push details or None
        """
        # Look back up to 15 bars to find the push
        lookback = 15
        data_len = len(close)
        
        # We need to find a sequence BEFORE the pullback (which is recent)
        # So we look for the push ending 2-7 bars ago
        
        for end_offset in range(2, 8):
            end_idx = data_len - 1 - end_offset
            
            if end_idx < self.config.min_green_candles:
                continue
            
            # Count consecutive green candles with increasing volume backwards
            green_count = 0
            volumes = []
            push_high = 0
            push_start_idx = end_idx
            green_indices = []
            body_ratios = []
            strong_green_count = 0
            
            for i in range(end_idx, max(end_idx - 10, 0), -1):
                is_green = close.iloc[i] > open_price.iloc[i]
                
                if not is_green:
                    break
                
                body_ratio, upper_wick_ratio, _ = self._candle_metrics(
                    float(open_price.iloc[i]),
                    float(high.iloc[i]),
                    float(low.iloc[i]),
                    float(close.iloc[i]),
                )
                body_ratios.append(body_ratio)
                if (
                    body_ratio >= self.config.min_push_body_ratio
                    and upper_wick_ratio <= self.config.max_push_upper_wick_ratio
                ):
                    strong_green_count += 1
                
                green_count += 1
                volumes.append(volume.iloc[i])
                push_high = max(push_high, high.iloc[i])
                push_start_idx = i
                green_indices.append(i)
            
            if green_count >= self.config.min_green_candles:
                # Verify increasing volume trend (not strict, just general)
                volumes = volumes[::-1]  # Reverse to chronological order
                increasing = sum(
                    1
                    for i in range(1, len(volumes))
                    if volumes[i] > volumes[i - 1] * self.config.min_volume_increase_ratio
                )
                
                strong_override = (
                    self.config.allow_strong_green_override_volume
                    and strong_green_count >= self.config.strong_green_override_min
                )
                
                if increasing >= len(volumes) // 2 or strong_override:  # At least half showing increase
                    avg_volume = sum(volumes) / len(volumes)
                    green_indices = list(reversed(green_indices))
                    return {
                        "start_idx": int(push_start_idx),
                        "end_idx": int(end_idx),
                        "push_high": float(push_high),
                        "push_avg_volume": float(avg_volume),
                        "green_candles": int(green_count),
                        "green_indices": green_indices,
                        "strong_green_count": int(strong_green_count),
                        "avg_body_ratio": float(np.mean(body_ratios)) if body_ratios else 0.0,
                        "strong_override": bool(strong_override),
                    }
        
        return None
    
    def _find_prior_resistance(
        self,
        high: pd.Series,
        close: pd.Series,
        push_start_idx: int,
    ) -> Optional[Tuple[float, int]]:
        """
        Find prior resistance high before the push phase.
        
        Looks back between prior_resistance_lookback_max and
        prior_resistance_lookback_min bars before the push start.
        """
        lookback_start = max(0, push_start_idx - self.config.prior_resistance_lookback_max)
        lookback_end = max(0, push_start_idx - self.config.prior_resistance_lookback_min)
        
        if lookback_end <= lookback_start:
            return None
        
        prior_highs = high.iloc[lookback_start:lookback_end]
        if len(prior_highs) < 3:
            return None
        
        prior_high = float(prior_highs.max())
        prior_label = prior_highs.idxmax()
        
        try:
            prior_loc = high.index.get_loc(prior_label)
            if isinstance(prior_loc, slice):
                prior_idx = int(prior_loc.start)
            elif isinstance(prior_loc, (list, tuple, np.ndarray)):
                prior_idx = int(prior_loc[0]) if len(prior_loc) > 0 else None
            else:
                prior_idx = int(prior_loc)
        except Exception:
            prior_idx = None
        
        return prior_high, prior_idx

    def _find_target_resistance(
        self,
        high: pd.Series,
        push_start_idx: int,
    ) -> Optional[Tuple[float, int]]:
        """
        Find a broader prior resistance area for target setting.
        """
        lookback_start = max(0, push_start_idx - self.config.target_resistance_lookback_max)
        lookback_end = max(0, push_start_idx - self.config.target_resistance_lookback_min)
        if lookback_end <= lookback_start:
            return None
        target_highs = high.iloc[lookback_start:lookback_end]
        if len(target_highs) < 3:
            return None
        target_high = float(target_highs.max())
        target_label = target_highs.idxmax()
        try:
            target_loc = high.index.get_loc(target_label)
            if isinstance(target_loc, slice):
                target_idx = int(target_loc.start)
            elif isinstance(target_loc, (list, tuple, np.ndarray)):
                target_idx = int(target_loc[0]) if len(target_loc) > 0 else None
            else:
                target_idx = int(target_loc)
        except Exception:
            target_idx = None
        return target_high, target_idx

    def _find_resistance_levels(
        self,
        high: pd.Series,
        start_idx: int,
    ) -> List[Dict[str, float]]:
        """
        Identify clustered resistance levels from local maxima.
        """
        end = max(0, start_idx - 1)
        start = max(0, end - self.config.resistance_lookback_bars)
        if end - start < 5:
            return []
        peaks: List[Tuple[int, float]] = []
        for i in range(start + 1, end):
            if high.iloc[i] >= high.iloc[i - 1] and high.iloc[i] >= high.iloc[i + 1]:
                peaks.append((i, float(high.iloc[i])))
        clusters: List[Dict[str, float]] = []
        for idx, price in peaks:
            placed = False
            for cluster in clusters:
                if abs(price - cluster["price"]) / cluster["price"] * 100 <= self.config.resistance_tolerance_pct:
                    cluster["touches"] += 1
                    cluster["price"] = (cluster["price"] * (cluster["touches"] - 1) + price) / cluster["touches"]
                    cluster["last_idx"] = idx
                    placed = True
                    break
            if not placed:
                clusters.append({"price": price, "touches": 1, "last_idx": idx})
        clusters.sort(key=lambda c: c["price"])
        return clusters

    def _clamp_target(
        self,
        target: float,
        entry_price: float,
        min_gap: float,
        max_gap: Optional[float],
        reasons: List[str],
        label: str,
    ) -> float:
        if target <= entry_price + min_gap:
            target = entry_price + min_gap
            reasons.append(f"{label} fallback (min distance)")
        if max_gap is not None and target > entry_price + max_gap:
            target = entry_price + max_gap
            reasons.append(f"{label} fallback (max distance)")
        return target
    
    def _check_pullback_phase(
        self,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        open_price: pd.Series,
        volume: pd.Series,
        ema9: pd.Series,
        push_end_idx: int,
        push_high: float,
        push_avg_volume: float,
        prior_resistance: Optional[float],
    ) -> Optional[Dict[str, float]]:
        """
        Check for valid pullback after push.
        
        Returns:
            Dict with pullback details or None
        """
        data_len = len(close)
        pullback_start = push_end_idx + 1
        pullback_end = data_len - 2  # Exclude the current (trigger) candle
        
        if pullback_end <= pullback_start:
            return None
        
        pullback_bars = pullback_end - pullback_start + 1
        
        # Get pullback data
        pullback_lows = low.iloc[pullback_start:pullback_end + 1]
        pullback_highs = high.iloc[pullback_start:pullback_end + 1]
        pullback_closes = close.iloc[pullback_start:pullback_end + 1]
        pullback_opens = open_price.iloc[pullback_start:pullback_end + 1]
        pullback_volumes = volume.iloc[pullback_start:pullback_end + 1]
        pullback_ema9 = ema9.iloc[pullback_start:pullback_end + 1]
        
        pullback_low = pullback_lows.min()
        pullback_avg_volume = pullback_volumes.mean()
        
        # Pullback depth
        pullback_pct = (push_high - pullback_low) / push_high * 100
        if pullback_pct > self.config.max_pullback_pct:
            return None  # Pullback too deep
        
        # EMA9 proximity across the pullback (multi-round contraction)
        ema9_dist_pct = (pullback_lows - pullback_ema9).abs() / pullback_ema9 * 100
        near_ema9_mask = ema9_dist_pct <= (self.config.ema9_tolerance_pct * 2)
        near_ema9_ratio = float(near_ema9_mask.mean()) if len(near_ema9_mask) > 0 else 0.0
        contraction_rounds = 0
        in_round = False
        for is_near in list(near_ema9_mask):
            if is_near and not in_round:
                contraction_rounds += 1
                in_round = True
            elif not is_near:
                in_round = False
        
        if pullback_bars > self.config.max_pullback_bars:
            if not (
                self.config.allow_multi_pullback_rounds
                and pullback_bars <= self.config.max_pullback_bars_extended
                and near_ema9_ratio >= self.config.min_pullback_ema9_touch_ratio
            ):
                return None  # Pullback too long
        
        # Check if pullback found support near EMA9
        min_low_idx = pullback_lows.idxmin()
        ema9_at_low = ema9.loc[min_low_idx]
        dist_to_ema9 = abs(pullback_low - ema9_at_low) / ema9_at_low * 100
        
        if self.config.require_pullback_above_ema9 and pullback_low < ema9_at_low:
            below_ema9_pct = (ema9_at_low - pullback_low) / ema9_at_low * 100
            if below_ema9_pct > self.config.max_below_ema9_pct:
                return None  # Pullback dipped below EMA9
        
        if dist_to_ema9 > self.config.ema9_tolerance_pct * 2:
            shallow_ok = False
            if self.config.allow_shallow_pullback:
                max_dist_ok = dist_to_ema9 <= self.config.max_shallow_ema9_distance_pct
                closes_above_ema9 = (
                    pullback_closes >= pullback_ema9 * (1 - self.config.max_below_ema9_pct / 100)
                ).all()
                shallow_ok = max_dist_ok and closes_above_ema9 and pullback_pct <= self.config.shallow_pullback_pct
            if not shallow_ok:
                return None  # Pullback didn't reach EMA9 area
        
        # Check if it's a higher low compared to before the push
        # Look for lows before the push started
        pre_push_low = low.iloc[max(0, push_end_idx - 15):push_end_idx].min()
        higher_low = pullback_low > pre_push_low

        # Double-bottom confirmation with volume
        double_bottom_confirmed = False
        bottom_volume_ratio = 0.0
        if pre_push_low > 0:
            bottom_diff_pct = abs(pullback_low - pre_push_low) / pre_push_low * 100
            if bottom_diff_pct <= self.config.double_bottom_tolerance_pct:
                try:
                    low_volume = float(volume.loc[min_low_idx])
                except Exception:
                    low_volume = float(volume.iloc[pullback_lows.idxmin()])
                bottom_volume_ratio = low_volume / pullback_avg_volume if pullback_avg_volume > 0 else 0.0
                if bottom_volume_ratio >= self.config.min_bottom_volume_ratio:
                    double_bottom_confirmed = True

        # Pullback absorption: small red bodies above breakout with high volume
        absorption_count = 0
        body_ratios = []
        for o, h, l, c in zip(pullback_opens, pullback_highs, pullback_lows, pullback_closes):
            body_ratio, _, _ = self._candle_metrics(float(o), float(h), float(l), float(c))
            body_ratios.append(body_ratio)
            if prior_resistance is None:
                continue
            is_red = c < o
            small_body = body_ratio <= self.config.pullback_small_body_ratio
            above_breakout = c >= prior_resistance
            if is_red and small_body and above_breakout:
                absorption_count += 1
        pullback_absorption_ratio = absorption_count / pullback_bars if pullback_bars > 0 else 0.0
        
        return {
            "pullback_low": float(pullback_low),
            "pullback_avg_volume": float(pullback_avg_volume),
            "pullback_bars": int(pullback_bars),
            "higher_low": bool(higher_low),
            "pullback_start_idx": int(pullback_start),
            "pullback_end_idx": int(pullback_end),
            "pullback_pct": float(pullback_pct),
            "dist_to_ema9_pct": float(dist_to_ema9),
            "near_ema9_ratio": float(near_ema9_ratio),
            "contraction_rounds": int(contraction_rounds),
            "pullback_absorption_ratio": float(pullback_absorption_ratio),
            "pullback_avg_body_ratio": float(np.mean(body_ratios)) if body_ratios else 0.0,
            "double_bottom_confirmed": bool(double_bottom_confirmed),
            "bottom_volume_ratio": float(bottom_volume_ratio),
        }
    
    def _calculate_atr(
        self, 
        high: pd.Series, 
        low: pd.Series, 
        close: pd.Series, 
        period: int
    ) -> pd.Series:
        """Calculate ATR."""
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def _resolve_end_idx(self, series: pd.Series, end_idx: int) -> int:
        """Clamp end_idx to valid range for a series."""
        if end_idx is None:
            return len(series) - 1
        return max(0, min(int(end_idx), len(series) - 1))


def scan_for_volume_pullback(
    symbol: str,
    data: pd.DataFrame,
    regime: Regime = Regime.UNKNOWN,
    volatility: VolatilityLevel = VolatilityLevel.MEDIUM
) -> Optional[Signal]:
    """
    Convenience function to scan a single stock for Volume Pullback pattern.
    
    This is the HIGH PRIORITY pattern - check this first!
    """
    scanner = VolumePullbackEntrySignal()
    return scanner.generate(symbol, data, regime, volatility)
