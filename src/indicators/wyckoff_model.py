"""
Wyckoff Market Structure Model

5-layer model that combines market structure analysis with Wyckoff methodology:
  Layer 1: Context Engine - classify market state
  Layer 2: Liquidity Map - track where liquidity pools sit
  Layer 3: Structure Shifts - detect CHOCH/BOS sequences
  Layer 4: Wyckoff Phase - score accumulation/distribution/markup/markdown
  Layer 5: Execution Rules - produce actionable setups

For offline analysis and visualization. Not wired into live signal pipeline yet.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger

from .trend import TrendIndicators, TrendDirection, EMAAlignment, calculate_atr
from .market_structure import (
    MarketStructureAnalyzer,
    StructureAnalysis,
    StructureDirection,
    StructureBreakType,
    StructureBreak,
    LiquidityMap,
    LiquiditySweep,
    DemandSupplyZone,
)


# =============================================================================
# ENUMS
# =============================================================================

class MarketContext(Enum):
    RANGE_ACCUMULATION = "range_accumulation"
    RANGE_DISTRIBUTION = "range_distribution"
    MARKUP = "markup"
    MARKDOWN = "markdown"
    UNDEFINED = "undefined"


class WyckoffPhase(Enum):
    ACCUMULATION = "accumulation"
    DISTRIBUTION = "distribution"
    MARKUP = "markup"
    MARKDOWN = "markdown"
    UNDEFINED = "undefined"


# =============================================================================
# RESULT DATACLASSES
# =============================================================================

@dataclass
class ContextResult:
    """Layer 1: Market context classification."""
    context: MarketContext
    confidence: float
    is_range: bool
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    momentum_declining: bool = False
    reasoning: str = ""


@dataclass
class WyckoffPhaseResult:
    """Layer 4: Phase scoring."""
    phase: WyckoffPhase
    accumulation_score: float = 0.0
    distribution_score: float = 0.0
    markup_score: float = 0.0
    markdown_score: float = 0.0
    dominant_score: float = 0.0
    reasoning: str = ""


@dataclass
class WyckoffSetup:
    """Layer 5: Actionable trade setup."""
    is_valid: bool = False
    direction: int = 0                  # 1 = long, -1 = short
    confidence: float = 0.0

    # Checklist
    htf_context_aligned: bool = False
    sweep_detected: bool = False
    reclaim_confirmed: bool = False
    choch_confirmed: bool = False
    bos_confirmed: bool = False
    in_demand_supply_zone: bool = False
    volume_confirmed: bool = False
    risk_reward_ok: bool = False

    # Levels
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    risk_reward: float = 0.0

    reasoning: str = ""


@dataclass
class WyckoffAnalysis:
    """Complete Wyckoff model output combining all 5 layers."""
    context: ContextResult
    liquidity_map: LiquidityMap
    structure: StructureAnalysis
    phase: WyckoffPhaseResult
    setup: WyckoffSetup
    wyckoff_score: float = 0.0


# =============================================================================
# WYCKOFF MODEL
# =============================================================================

class WyckoffModel:
    """
    5-layer Wyckoff Market Structure Model.

    Combines structural analysis (BOS/CHOCH/liquidity) with Wyckoff phase
    annotation to produce scored market context and actionable trade setups.
    """

    def __init__(
        self,
        trend_indicators: Optional[TrendIndicators] = None,
        structure_analyzer: Optional[MarketStructureAnalyzer] = None,
        swing_lookback: int = 5,
        min_rr_ratio: float = 2.0,
        range_pct_threshold: float = 0.10,
    ):
        self.trend = trend_indicators or TrendIndicators(swing_lookback=swing_lookback)
        self.structure = structure_analyzer or MarketStructureAnalyzer(
            trend_indicators=self.trend,
            swing_lookback=swing_lookback,
        )
        self.min_rr_ratio = min_rr_ratio
        self.range_pct_threshold = range_pct_threshold

    def analyze(
        self,
        data: pd.DataFrame,
        htf_data: Optional[pd.DataFrame] = None,
    ) -> WyckoffAnalysis:
        """
        Run all 5 layers of the Wyckoff model.

        Args:
            data: OHLCV DataFrame (primary / lower timeframe).
            htf_data: Optional higher timeframe OHLCV for context.

        Returns:
            WyckoffAnalysis with context, liquidity map, phase, and setup.
        """
        # Structure analysis (layers 2-3 foundation)
        struct = self.structure.analyze(data)

        # Layer 1: Context
        context = self._classify_context(data, struct)

        # Layer 4: Phase scoring
        phase = self._score_wyckoff_phase(data, struct, context)

        # Layer 5: Setup evaluation
        htf_context = None
        if htf_data is not None and len(htf_data) > 20:
            htf_struct = self.structure.analyze(htf_data)
            htf_context = self._classify_context(htf_data, htf_struct)

        setup = self._evaluate_setup(data, struct, context, phase, htf_context)

        # Overall score
        wyckoff_score = self._calculate_wyckoff_score(phase, setup)

        return WyckoffAnalysis(
            context=context,
            liquidity_map=struct.liquidity_map,
            structure=struct,
            phase=phase,
            setup=setup,
            wyckoff_score=wyckoff_score,
        )

    # =========================================================================
    # LAYER 1: CONTEXT ENGINE
    # =========================================================================

    def _classify_context(
        self,
        data: pd.DataFrame,
        struct: StructureAnalysis,
    ) -> ContextResult:
        """Classify current market into one of 4 states."""
        if len(data) < 30:
            return ContextResult(
                context=MarketContext.UNDEFINED,
                confidence=0.0,
                is_range=False,
                reasoning="Insufficient data",
            )

        # Check for range
        lookback_data = data.tail(50)
        price_high = lookback_data['high'].max()
        price_low = lookback_data['low'].min()
        price_range_pct = (price_high - price_low) / price_low

        is_range = price_range_pct < self.range_pct_threshold

        # Trend state
        trend_state = self.trend.get_trend_state(data)
        hh = self.trend.has_higher_highs(data)
        hl = self.trend.has_higher_lows(data)

        # Check momentum direction (using close vs EMA slope)
        ema_df = self.trend.calculate_emas(data)
        if len(ema_df) >= 20:
            ema_slope = (ema_df['ema_fast'].iloc[-1] - ema_df['ema_fast'].iloc[-10]) / ema_df['ema_fast'].iloc[-10]
        else:
            ema_slope = 0.0

        # Count sell-side vs buy-side sweeps
        sell_sweeps = [s for s in struct.liquidity_map.recent_sweeps if s.direction == "sell_side"]
        buy_sweeps = [s for s in struct.liquidity_map.recent_sweeps if s.direction == "buy_side"]

        # Recent structure direction
        struct_dir = struct.direction

        # Scoring each context
        scores = {
            MarketContext.RANGE_ACCUMULATION: 0.0,
            MarketContext.RANGE_DISTRIBUTION: 0.0,
            MarketContext.MARKUP: 0.0,
            MarketContext.MARKDOWN: 0.0,
        }

        # Range accumulation: sideways + sell-side sweeps + declining bearish momentum
        if is_range:
            scores[MarketContext.RANGE_ACCUMULATION] += 0.3
            scores[MarketContext.RANGE_DISTRIBUTION] += 0.3

        if len(sell_sweeps) > len(buy_sweeps):
            scores[MarketContext.RANGE_ACCUMULATION] += 0.25
        if len(buy_sweeps) > len(sell_sweeps):
            scores[MarketContext.RANGE_DISTRIBUTION] += 0.25

        # EMA slope direction
        if ema_slope > 0.005:
            scores[MarketContext.MARKUP] += 0.2
            scores[MarketContext.RANGE_ACCUMULATION] += 0.1
        elif ema_slope < -0.005:
            scores[MarketContext.MARKDOWN] += 0.2
            scores[MarketContext.RANGE_DISTRIBUTION] += 0.1

        # HH/HL pattern
        if hh and hl:
            scores[MarketContext.MARKUP] += 0.3
        elif not hh and not hl:
            if trend_state.direction in (TrendDirection.DOWN, TrendDirection.STRONG_DOWN):
                scores[MarketContext.MARKDOWN] += 0.3
            else:
                scores[MarketContext.RANGE_ACCUMULATION] += 0.1
                scores[MarketContext.RANGE_DISTRIBUTION] += 0.1

        # Structure breaks direction
        if struct_dir == StructureDirection.BULLISH:
            scores[MarketContext.MARKUP] += 0.2
            scores[MarketContext.RANGE_ACCUMULATION] += 0.1
        elif struct_dir == StructureDirection.BEARISH:
            scores[MarketContext.MARKDOWN] += 0.2
            scores[MarketContext.RANGE_DISTRIBUTION] += 0.1

        # Pick the winner
        best_context = max(scores, key=scores.get)
        best_score = scores[best_context]
        total = sum(scores.values()) or 1.0
        confidence = best_score / total

        reasoning_parts = []
        if is_range:
            reasoning_parts.append(f"Range: {price_range_pct:.1%}")
        reasoning_parts.append(f"EMA slope: {ema_slope:.4f}")
        reasoning_parts.append(f"Sweeps: sell={len(sell_sweeps)} buy={len(buy_sweeps)}")
        reasoning_parts.append(f"Structure: {struct_dir.value}")

        return ContextResult(
            context=best_context,
            confidence=confidence,
            is_range=is_range,
            range_high=price_high if is_range else None,
            range_low=price_low if is_range else None,
            momentum_declining=(abs(ema_slope) < 0.002),
            reasoning="; ".join(reasoning_parts),
        )

    # =========================================================================
    # LAYER 4: WYCKOFF PHASE SCORING
    # =========================================================================

    def _score_wyckoff_phase(
        self,
        data: pd.DataFrame,
        struct: StructureAnalysis,
        context: ContextResult,
    ) -> WyckoffPhaseResult:
        """Score each Wyckoff phase based on structural evidence."""
        if data.empty or len(data) < 5:
            return WyckoffPhaseResult(phase=WyckoffPhase.UNDEFINED)

        # Component checks
        has_range = context.is_range
        sell_sweeps = [s for s in struct.liquidity_map.recent_sweeps if s.direction == "sell_side"]
        buy_sweeps = [s for s in struct.liquidity_map.recent_sweeps if s.direction == "buy_side"]
        has_sell_sweep = len(sell_sweeps) > 0
        has_buy_sweep = len(buy_sweeps) > 0

        has_bullish_choch = any(
            b.break_type == StructureBreakType.CHOCH and b.direction == StructureDirection.BULLISH
            for b in struct.structure_breaks
        )
        has_bearish_choch = any(
            b.break_type == StructureBreakType.CHOCH and b.direction == StructureDirection.BEARISH
            for b in struct.structure_breaks
        )
        has_bullish_bos = any(
            b.break_type == StructureBreakType.BOS and b.direction == StructureDirection.BULLISH
            for b in struct.structure_breaks
        )
        has_bearish_bos = any(
            b.break_type == StructureBreakType.BOS and b.direction == StructureDirection.BEARISH
            for b in struct.structure_breaks
        )

        # HH/HL check
        hh = self.trend.has_higher_highs(data)
        hl = self.trend.has_higher_lows(data)

        # Volume declining check (simple: recent volume below average)
        if len(data) >= 20:
            vol_avg = data['volume'].tail(20).mean()
            vol_recent = data['volume'].tail(5).mean()
            volume_declining = vol_recent < vol_avg * 0.8
        else:
            volume_declining = False

        # Accumulation score: range + sell-side sweep (spring) + CHOCH up + BOS up
        acc_score = 0.0
        if has_range:
            acc_score += 0.20
        if has_sell_sweep:
            acc_score += 0.25
        if has_bullish_choch:
            acc_score += 0.30
        if has_bullish_bos:
            acc_score += 0.25

        # Distribution score: range + buy-side sweep (UTAD) + CHOCH down + BOS down
        dist_score = 0.0
        if has_range:
            dist_score += 0.20
        if has_buy_sweep:
            dist_score += 0.25
        if has_bearish_choch:
            dist_score += 0.30
        if has_bearish_bos:
            dist_score += 0.25

        # Markup score: HH/HL + shallow pullback + bullish BOS + volume
        markup_score = 0.0
        if hh and hl:
            markup_score += 0.30
        if has_bullish_bos:
            markup_score += 0.25
        if not volume_declining:
            markup_score += 0.25
        pullback = (data['high'].tail(30).max() - data['close'].iloc[-1]) / data['high'].tail(30).max()
        if pullback < 0.05:  # shallow pullback
            markup_score += 0.20

        # Markdown score: LL/LH + weak rally + bearish BOS
        markdown_score = 0.0
        if not hh and not hl:
            markdown_score += 0.30
        if has_bearish_bos:
            markdown_score += 0.25
        if volume_declining:
            markdown_score += 0.25
        rally = (data['close'].iloc[-1] - data['low'].tail(30).min()) / data['low'].tail(30).min()
        if rally < 0.03:  # weak rally
            markdown_score += 0.20

        # Find dominant
        phase_scores = {
            WyckoffPhase.ACCUMULATION: acc_score,
            WyckoffPhase.DISTRIBUTION: dist_score,
            WyckoffPhase.MARKUP: markup_score,
            WyckoffPhase.MARKDOWN: markdown_score,
        }
        dominant_phase = max(phase_scores, key=phase_scores.get)
        dominant_score = phase_scores[dominant_phase]

        if dominant_score < 0.2:
            dominant_phase = WyckoffPhase.UNDEFINED

        reasoning_parts = [
            f"acc={acc_score:.2f}",
            f"dist={dist_score:.2f}",
            f"markup={markup_score:.2f}",
            f"markdown={markdown_score:.2f}",
        ]

        return WyckoffPhaseResult(
            phase=dominant_phase,
            accumulation_score=acc_score,
            distribution_score=dist_score,
            markup_score=markup_score,
            markdown_score=markdown_score,
            dominant_score=dominant_score,
            reasoning="; ".join(reasoning_parts),
        )

    # =========================================================================
    # LAYER 5: SETUP EVALUATION
    # =========================================================================

    def _evaluate_setup(
        self,
        data: pd.DataFrame,
        struct: StructureAnalysis,
        context: ContextResult,
        phase: WyckoffPhaseResult,
        htf_context: Optional[ContextResult] = None,
    ) -> WyckoffSetup:
        """Evaluate whether a valid trade setup exists using multi-condition checklist."""
        if data.empty or len(data) < 5:
            return WyckoffSetup()
        current_price = data['close'].iloc[-1]
        atr = calculate_atr(data)
        atr_val = atr.iloc[-1] if not atr.empty and not pd.isna(atr.iloc[-1]) else current_price * 0.02

        # Try long setup
        long_setup = self._evaluate_directional_setup(
            data, struct, context, phase, htf_context, direction=1,
            current_price=current_price, atr_val=atr_val,
        )

        # Try short setup
        short_setup = self._evaluate_directional_setup(
            data, struct, context, phase, htf_context, direction=-1,
            current_price=current_price, atr_val=atr_val,
        )

        # Return the better one
        if long_setup.is_valid and short_setup.is_valid:
            return long_setup if long_setup.confidence >= short_setup.confidence else short_setup
        elif long_setup.is_valid:
            return long_setup
        elif short_setup.is_valid:
            return short_setup
        # Return the one with higher confidence even if invalid
        return long_setup if long_setup.confidence >= short_setup.confidence else short_setup

    def _evaluate_directional_setup(
        self,
        data: pd.DataFrame,
        struct: StructureAnalysis,
        context: ContextResult,
        phase: WyckoffPhaseResult,
        htf_context: Optional[ContextResult],
        direction: int,
        current_price: float,
        atr_val: float,
    ) -> WyckoffSetup:
        """Evaluate one direction (long or short) setup."""

        if direction == 1:
            # LONG setup
            valid_contexts = (MarketContext.RANGE_ACCUMULATION, MarketContext.MARKUP)
            sweep_dir = "sell_side"
            choch_dir = StructureDirection.BULLISH
            bos_dir = StructureDirection.BULLISH
            zones = struct.demand_zones
        else:
            # SHORT setup
            valid_contexts = (MarketContext.RANGE_DISTRIBUTION, MarketContext.MARKDOWN)
            sweep_dir = "buy_side"
            choch_dir = StructureDirection.BEARISH
            bos_dir = StructureDirection.BEARISH
            zones = struct.supply_zones

        # 1. HTF context alignment
        htf_aligned = False
        if htf_context and htf_context.context in valid_contexts:
            htf_aligned = True
        elif htf_context is None:
            htf_aligned = context.context in valid_contexts

        # 2. Sweep detected
        relevant_sweeps = [s for s in struct.liquidity_map.recent_sweeps if s.direction == sweep_dir]
        sweep_detected = len(relevant_sweeps) > 0

        # 3. Reclaim confirmed
        reclaim_confirmed = any(s.is_reclaimed for s in relevant_sweeps)

        # 4. CHOCH confirmed
        choch_confirmed = any(
            b.break_type == StructureBreakType.CHOCH and b.direction == choch_dir
            for b in struct.structure_breaks
        )

        # 5. BOS confirmed
        bos_confirmed = any(
            b.break_type == StructureBreakType.BOS and b.direction == bos_dir
            for b in struct.structure_breaks
        )

        # 6. In demand/supply zone
        in_zone = False
        for zone in zones:
            if zone.low <= current_price <= zone.high:
                in_zone = True
                break

        # 7. Volume confirmation (basic check)
        vol_confirmed = False
        if len(data) >= 20:
            vol_avg = data['volume'].tail(20).mean()
            vol_now = data['volume'].iloc[-1]
            vol_confirmed = vol_now >= vol_avg * 0.8

        # 8. Calculate levels and R:R
        if direction == 1:
            # Stop at sweep extreme or recent swing low
            if relevant_sweeps:
                stop_price = min(s.sweep_price for s in relevant_sweeps) - atr_val * 0.2
            else:
                swing_lows = struct.swing_lows
                stop_price = swing_lows[-1][1] - atr_val * 0.2 if swing_lows else current_price - 2 * atr_val

            # TP1: nearest buy-side liquidity
            tp1 = struct.liquidity_map.nearest_buy_side.price if struct.liquidity_map.nearest_buy_side else current_price + 2 * atr_val
            # TP2: recent swing high
            tp2 = struct.swing_highs[-1][1] if struct.swing_highs else current_price + 3 * atr_val
            tp2 = max(tp2, tp1)
            # TP3: trend continuation
            tp3 = tp2 + atr_val
        else:
            # Short: mirror
            if relevant_sweeps:
                stop_price = max(s.sweep_price for s in relevant_sweeps) + atr_val * 0.2
            else:
                swing_highs = struct.swing_highs
                stop_price = swing_highs[-1][1] + atr_val * 0.2 if swing_highs else current_price + 2 * atr_val

            tp1 = struct.liquidity_map.nearest_sell_side.price if struct.liquidity_map.nearest_sell_side else current_price - 2 * atr_val
            tp2 = struct.swing_lows[-1][1] if struct.swing_lows else current_price - 3 * atr_val
            tp2 = min(tp2, tp1)
            tp3 = tp2 - atr_val

        # Risk/reward
        risk = abs(current_price - stop_price)
        reward = abs(tp1 - current_price)
        rr = reward / risk if risk > 0 else 0.0
        rr_ok = rr >= self.min_rr_ratio

        # Validity: minimum required conditions
        is_valid = (
            sweep_detected
            and reclaim_confirmed
            and choch_confirmed
            and (bos_confirmed or in_zone)
            and rr_ok
        )

        # Confidence from checklist score
        checks = [htf_aligned, sweep_detected, reclaim_confirmed, choch_confirmed,
                  bos_confirmed, in_zone, vol_confirmed, rr_ok]
        checklist_score = sum(checks)
        confidence = checklist_score / 8.0

        reasoning_parts = []
        labels = ["HTF", "Sweep", "Reclaim", "CHOCH", "BOS", "Zone", "Vol", "R:R"]
        for label, val in zip(labels, checks):
            reasoning_parts.append(f"{label}:{'Y' if val else 'N'}")
        reasoning_parts.append(f"R:R={rr:.1f}")

        return WyckoffSetup(
            is_valid=is_valid,
            direction=direction,
            confidence=confidence,
            htf_context_aligned=htf_aligned,
            sweep_detected=sweep_detected,
            reclaim_confirmed=reclaim_confirmed,
            choch_confirmed=choch_confirmed,
            bos_confirmed=bos_confirmed,
            in_demand_supply_zone=in_zone,
            volume_confirmed=vol_confirmed,
            risk_reward_ok=rr_ok,
            entry_price=current_price,
            stop_price=stop_price,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            risk_reward=rr,
            reasoning="; ".join(reasoning_parts),
        )

    # =========================================================================
    # SCORING
    # =========================================================================

    def _calculate_wyckoff_score(
        self,
        phase: WyckoffPhaseResult,
        setup: WyckoffSetup,
    ) -> float:
        """Calculate overall 0-1 Wyckoff score for signal engine integration."""
        # Phase clarity contributes 50%, setup confidence contributes 50%
        phase_score = phase.dominant_score
        setup_score = setup.confidence

        return 0.5 * phase_score + 0.5 * setup_score
