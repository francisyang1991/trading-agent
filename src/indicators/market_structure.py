"""
Market Structure Analysis Module

Detects BOS (Break of Structure), CHOCH (Change of Character),
liquidity levels (equal highs/lows), sweeps, and demand/supply zones.

This is the low-level structural analysis engine that operates on a single
timeframe's DataFrame and identifies market structure primitives.
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import pandas as pd
import numpy as np
from loguru import logger

from .trend import TrendIndicators, calculate_atr


# =============================================================================
# ENUMS
# =============================================================================

class StructureDirection(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class StructureBreakType(Enum):
    BOS = "bos"       # Break of Structure (continuation)
    CHOCH = "choch"   # Change of Character (reversal)


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class StructureLevel:
    """A key structural price level (liquidity pool)."""
    price: float
    index: int                          # bar index in the DataFrame
    level_type: str                     # "swing_high", "swing_low", "equal_high", "equal_low"
    strength: int = 1                   # number of touches / confirmations
    is_swept: bool = False
    swept_at_index: Optional[int] = None
    reclaimed: bool = False


@dataclass
class StructureBreak:
    """A detected BOS or CHOCH event."""
    break_type: StructureBreakType
    direction: StructureDirection       # bullish or bearish
    price: float                        # price at which the break occurred
    broken_level: float                 # the structure level that was broken
    index: int                          # bar index of the break
    confirmed: bool = True              # closed beyond (not just wicked)


@dataclass
class LiquiditySweep:
    """A detected liquidity sweep event."""
    level: StructureLevel
    sweep_price: float                  # extreme price of the sweep wick
    reclaim_price: float                # close price after reclaim
    index: int
    direction: str                      # "buy_side" or "sell_side"
    is_reclaimed: bool = False


@dataclass
class DemandSupplyZone:
    """A demand or supply zone derived from BOS origin candles."""
    zone_type: str                      # "demand" or "supply"
    high: float
    low: float
    origin_index: int                   # bar index of the origin candle
    associated_break: Optional[StructureBreak] = None
    is_tested: bool = False
    is_broken: bool = False


@dataclass
class LiquidityMap:
    """Current state of liquidity levels relative to price."""
    buy_side_levels: List[StructureLevel] = field(default_factory=list)
    sell_side_levels: List[StructureLevel] = field(default_factory=list)
    nearest_buy_side: Optional[StructureLevel] = None
    nearest_sell_side: Optional[StructureLevel] = None
    recent_sweeps: List[LiquiditySweep] = field(default_factory=list)


@dataclass
class StructureAnalysis:
    """Complete market structure analysis result."""
    direction: StructureDirection
    swing_highs: List[Tuple[int, float]]
    swing_lows: List[Tuple[int, float]]
    structure_breaks: List[StructureBreak] = field(default_factory=list)
    latest_bos: Optional[StructureBreak] = None
    latest_choch: Optional[StructureBreak] = None
    liquidity_map: LiquidityMap = field(default_factory=LiquidityMap)
    equal_highs: List[StructureLevel] = field(default_factory=list)
    equal_lows: List[StructureLevel] = field(default_factory=list)
    demand_zones: List[DemandSupplyZone] = field(default_factory=list)
    supply_zones: List[DemandSupplyZone] = field(default_factory=list)


# =============================================================================
# ANALYZER
# =============================================================================

class MarketStructureAnalyzer:
    """
    Low-level market structure analysis engine.

    Detects BOS/CHOCH, equal highs/lows, liquidity sweeps, and demand/supply
    zones from OHLCV data. Reuses TrendIndicators for swing detection.
    """

    def __init__(
        self,
        trend_indicators: Optional[TrendIndicators] = None,
        swing_lookback: int = 5,
        equal_level_tolerance_pct: float = 0.003,
        sweep_reclaim_bars: int = 3,
        structure_lookback: int = 300,
    ):
        self.trend = trend_indicators or TrendIndicators(swing_lookback=swing_lookback)
        self.swing_lookback = swing_lookback
        self.equal_level_tolerance_pct = equal_level_tolerance_pct
        self.sweep_reclaim_bars = sweep_reclaim_bars
        self.structure_lookback = structure_lookback

    def analyze(self, data: pd.DataFrame) -> StructureAnalysis:
        """
        Run full market structure analysis on OHLCV data.

        Args:
            data: DataFrame with open, high, low, close, volume columns.
                  Should be reset-indexed (integer index 0..N-1).

        Returns:
            StructureAnalysis with all detected structure primitives.
        """
        if data.empty or len(data) < self.swing_lookback * 2 + 1:
            return StructureAnalysis(
                direction=StructureDirection.NEUTRAL,
                swing_highs=[],
                swing_lows=[],
            )

        # Work on a tail slice for efficiency
        work_data = data.tail(self.structure_lookback).reset_index(drop=True)

        # Step 1: find swing points (reuse existing TrendIndicators)
        swing_highs = self.trend.find_swing_highs(work_data, self.swing_lookback)
        swing_lows = self.trend.find_swing_lows(work_data, self.swing_lookback)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StructureAnalysis(
                direction=StructureDirection.NEUTRAL,
                swing_highs=swing_highs,
                swing_lows=swing_lows,
            )

        # Step 2: detect equal highs / equal lows
        atr = calculate_atr(work_data)
        avg_atr = atr.iloc[-1] if not atr.empty and not pd.isna(atr.iloc[-1]) else 1.0
        tolerance = avg_atr * self.equal_level_tolerance_pct * 100  # scale tolerance with ATR

        equal_highs = self._detect_equal_levels(swing_highs, tolerance, "equal_high")
        equal_lows = self._detect_equal_levels(swing_lows, tolerance, "equal_low")

        # Step 3: detect BOS and CHOCH
        structure_breaks = self._detect_structure_breaks(work_data, swing_highs, swing_lows)

        # Step 4: detect liquidity sweeps
        all_levels = self._build_all_levels(swing_highs, swing_lows, equal_highs, equal_lows)
        sweeps = self._detect_liquidity_sweeps(work_data, all_levels)

        # Step 5: build liquidity map
        current_price = work_data['close'].iloc[-1]
        liquidity_map = self._build_liquidity_map(current_price, all_levels, sweeps)

        # Step 6: identify demand/supply zones
        demand_zones, supply_zones = self._identify_demand_supply_zones(work_data, structure_breaks)

        # Step 7: determine overall direction
        direction = self._determine_direction(structure_breaks)

        # Find latest BOS and CHOCH
        latest_bos = None
        latest_choch = None
        for sb in reversed(structure_breaks):
            if sb.break_type == StructureBreakType.BOS and latest_bos is None:
                latest_bos = sb
            if sb.break_type == StructureBreakType.CHOCH and latest_choch is None:
                latest_choch = sb
            if latest_bos and latest_choch:
                break

        return StructureAnalysis(
            direction=direction,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            structure_breaks=structure_breaks,
            latest_bos=latest_bos,
            latest_choch=latest_choch,
            liquidity_map=liquidity_map,
            equal_highs=equal_highs,
            equal_lows=equal_lows,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
        )

    # =========================================================================
    # EQUAL LEVELS DETECTION
    # =========================================================================

    def _detect_equal_levels(
        self,
        swing_points: List[Tuple[int, float]],
        tolerance: float,
        level_type: str,
    ) -> List[StructureLevel]:
        """
        Find clusters of swing points at similar prices (equal highs/lows).

        These represent liquidity pools where stops accumulate.
        """
        if len(swing_points) < 2:
            return []

        equal_levels = []
        used = set()

        for i in range(len(swing_points)):
            if i in used:
                continue
            cluster = [i]
            for j in range(i + 1, len(swing_points)):
                if j in used:
                    continue
                if abs(swing_points[i][1] - swing_points[j][1]) <= tolerance:
                    cluster.append(j)

            if len(cluster) >= 2:
                for idx in cluster:
                    used.add(idx)
                # Use the average price and the latest index
                avg_price = np.mean([swing_points[k][1] for k in cluster])
                latest_idx = max(swing_points[k][0] for k in cluster)
                equal_levels.append(StructureLevel(
                    price=avg_price,
                    index=latest_idx,
                    level_type=level_type,
                    strength=len(cluster),
                ))

        return equal_levels

    # =========================================================================
    # BOS / CHOCH DETECTION
    # =========================================================================

    def _detect_structure_breaks(
        self,
        data: pd.DataFrame,
        swing_highs: List[Tuple[int, float]],
        swing_lows: List[Tuple[int, float]],
    ) -> List[StructureBreak]:
        """
        Detect BOS and CHOCH by walking through swing points chronologically.

        The key insight:
        - In BEARISH context, the "key level" for CHOCH is the most recent swing HIGH.
          If a new swing high exceeds it, that's a CHOCH to bullish.
          A new swing low below the prior swing low is a bearish BOS (continuation).
        - In BULLISH context, the "key level" for CHOCH is the most recent swing LOW.
          If a new swing low breaks below it, that's a CHOCH to bearish.
          A new swing high above the prior swing high is a bullish BOS (continuation).

        The active levels are updated ONLY on continuation, not on reversal detection.
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return []

        breaks = []
        direction = self._initial_direction(swing_highs, swing_lows)

        # Build chronological timeline of all swings
        all_swings = []
        for idx, price in swing_highs:
            all_swings.append((idx, price, "high"))
        for idx, price in swing_lows:
            all_swings.append((idx, price, "low"))
        all_swings.sort(key=lambda x: x[0])

        # Initialize tracking: most recent swing high and low
        prev_high = None
        prev_low = None

        for idx, price, stype in all_swings:
            # Need at least one of each before we can detect breaks
            if stype == "high":
                if prev_high is None:
                    prev_high = price
                    continue
            else:
                if prev_low is None:
                    prev_low = price
                    continue

            if prev_high is None or prev_low is None:
                if stype == "high":
                    prev_high = price
                else:
                    prev_low = price
                continue

            if direction == StructureDirection.BEARISH:
                if stype == "low" and price < prev_low:
                    # Lower low in bearish = BOS (continuation)
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.BOS,
                        direction=StructureDirection.BEARISH,
                        price=price,
                        broken_level=prev_low,
                        index=idx,
                    ))
                elif stype == "high" and price > prev_high:
                    # Higher high in bearish = CHOCH (reversal to bullish)
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.CHOCH,
                        direction=StructureDirection.BULLISH,
                        price=price,
                        broken_level=prev_high,
                        index=idx,
                    ))
                    direction = StructureDirection.BULLISH

            elif direction == StructureDirection.BULLISH:
                if stype == "high" and price > prev_high:
                    # Higher high in bullish = BOS (continuation)
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.BOS,
                        direction=StructureDirection.BULLISH,
                        price=price,
                        broken_level=prev_high,
                        index=idx,
                    ))
                elif stype == "low" and price < prev_low:
                    # Lower low in bullish = CHOCH (reversal to bearish)
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.CHOCH,
                        direction=StructureDirection.BEARISH,
                        price=price,
                        broken_level=prev_low,
                        index=idx,
                    ))
                    direction = StructureDirection.BEARISH

            else:
                # Neutral: first decisive swing establishes direction
                if stype == "high" and price > prev_high:
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.BOS,
                        direction=StructureDirection.BULLISH,
                        price=price,
                        broken_level=prev_high,
                        index=idx,
                    ))
                    direction = StructureDirection.BULLISH
                elif stype == "low" and price < prev_low:
                    breaks.append(StructureBreak(
                        break_type=StructureBreakType.BOS,
                        direction=StructureDirection.BEARISH,
                        price=price,
                        broken_level=prev_low,
                        index=idx,
                    ))
                    direction = StructureDirection.BEARISH

            # Always update tracking to most recent swing of each type
            if stype == "high":
                prev_high = price
            else:
                prev_low = price

        return breaks

    def _initial_direction(
        self,
        swing_highs: List[Tuple[int, float]],
        swing_lows: List[Tuple[int, float]],
    ) -> StructureDirection:
        """Determine initial structure direction from first two swing pairs."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StructureDirection.NEUTRAL

        hh = swing_highs[1][1] > swing_highs[0][1]
        hl = swing_lows[1][1] > swing_lows[0][1]
        ll = swing_lows[1][1] < swing_lows[0][1]
        lh = swing_highs[1][1] < swing_highs[0][1]

        if hh and hl:
            return StructureDirection.BULLISH
        elif ll and lh:
            return StructureDirection.BEARISH
        return StructureDirection.NEUTRAL

    # =========================================================================
    # LIQUIDITY SWEEP DETECTION
    # =========================================================================

    def _detect_liquidity_sweeps(
        self,
        data: pd.DataFrame,
        levels: List[StructureLevel],
    ) -> List[LiquiditySweep]:
        """
        Detect liquidity sweeps: price wicks beyond a level then closes back inside.

        A sweep is NOT a break. The candle's wick pierces the level but the close
        is back inside. This is the institutional "stop hunt" pattern.
        """
        sweeps = []

        for level in levels:
            is_high_level = level.level_type in ("swing_high", "equal_high")
            is_low_level = level.level_type in ("swing_low", "equal_low")

            # Only check bars after the level was established
            start_bar = level.index + 1
            end_bar = min(start_bar + self.structure_lookback, len(data))

            for i in range(start_bar, end_bar):
                bar = data.iloc[i]

                if is_high_level:
                    # Buy-side sweep: wick above the level, close below
                    if bar['high'] > level.price and bar['close'] <= level.price:
                        level.is_swept = True
                        level.swept_at_index = i
                        # Check reclaim: does price close back below within N bars?
                        reclaim_price = bar['close']
                        is_reclaimed = bar['close'] < level.price
                        level.reclaimed = is_reclaimed
                        sweeps.append(LiquiditySweep(
                            level=level,
                            sweep_price=bar['high'],
                            reclaim_price=reclaim_price,
                            index=i,
                            direction="buy_side",
                            is_reclaimed=is_reclaimed,
                        ))
                        break  # Only detect first sweep per level

                elif is_low_level:
                    # Sell-side sweep: wick below the level, close above
                    if bar['low'] < level.price and bar['close'] >= level.price:
                        level.is_swept = True
                        level.swept_at_index = i
                        reclaim_price = bar['close']
                        is_reclaimed = bar['close'] > level.price
                        level.reclaimed = is_reclaimed
                        sweeps.append(LiquiditySweep(
                            level=level,
                            sweep_price=bar['low'],
                            reclaim_price=reclaim_price,
                            index=i,
                            direction="sell_side",
                            is_reclaimed=is_reclaimed,
                        ))
                        break

        return sweeps

    # =========================================================================
    # LIQUIDITY MAP
    # =========================================================================

    def _build_all_levels(
        self,
        swing_highs: List[Tuple[int, float]],
        swing_lows: List[Tuple[int, float]],
        equal_highs: List[StructureLevel],
        equal_lows: List[StructureLevel],
    ) -> List[StructureLevel]:
        """Combine all swing points and equal levels into a flat list."""
        levels = []

        for idx, price in swing_highs:
            levels.append(StructureLevel(price=price, index=idx, level_type="swing_high"))
        for idx, price in swing_lows:
            levels.append(StructureLevel(price=price, index=idx, level_type="swing_low"))

        levels.extend(equal_highs)
        levels.extend(equal_lows)

        return levels

    def _build_liquidity_map(
        self,
        current_price: float,
        levels: List[StructureLevel],
        sweeps: List[LiquiditySweep],
    ) -> LiquidityMap:
        """Build liquidity map: classify levels as buy-side or sell-side relative to price."""
        buy_side = [l for l in levels
                    if l.level_type in ("swing_high", "equal_high") and l.price > current_price and not l.is_swept]
        sell_side = [l for l in levels
                     if l.level_type in ("swing_low", "equal_low") and l.price < current_price and not l.is_swept]

        buy_side.sort(key=lambda l: l.price)
        sell_side.sort(key=lambda l: l.price, reverse=True)

        return LiquidityMap(
            buy_side_levels=buy_side,
            sell_side_levels=sell_side,
            nearest_buy_side=buy_side[0] if buy_side else None,
            nearest_sell_side=sell_side[0] if sell_side else None,
            recent_sweeps=sweeps,
        )

    # =========================================================================
    # DEMAND / SUPPLY ZONES
    # =========================================================================

    def _identify_demand_supply_zones(
        self,
        data: pd.DataFrame,
        breaks: List[StructureBreak],
    ) -> Tuple[List[DemandSupplyZone], List[DemandSupplyZone]]:
        """
        Identify demand and supply zones from BOS origin candles.

        Demand zone: the last bearish candle body before a bullish BOS.
        Supply zone: the last bullish candle body before a bearish BOS.
        """
        demand_zones = []
        supply_zones = []

        for brk in breaks:
            if brk.break_type != StructureBreakType.BOS:
                continue

            origin_idx = max(0, brk.index - 1)

            if brk.direction == StructureDirection.BULLISH:
                # Find last bearish candle before the break
                for j in range(brk.index - 1, max(brk.index - 10, -1), -1):
                    if j < 0 or j >= len(data):
                        continue
                    bar = data.iloc[j]
                    if bar['close'] < bar['open']:  # bearish candle
                        origin_idx = j
                        break

                bar = data.iloc[origin_idx]
                zone_low = min(bar['open'], bar['close'])
                zone_high = max(bar['open'], bar['close'])
                demand_zones.append(DemandSupplyZone(
                    zone_type="demand",
                    high=zone_high,
                    low=zone_low,
                    origin_index=origin_idx,
                    associated_break=brk,
                ))

            elif brk.direction == StructureDirection.BEARISH:
                # Find last bullish candle before the break
                for j in range(brk.index - 1, max(brk.index - 10, -1), -1):
                    if j < 0 or j >= len(data):
                        continue
                    bar = data.iloc[j]
                    if bar['close'] > bar['open']:  # bullish candle
                        origin_idx = j
                        break

                bar = data.iloc[origin_idx]
                zone_low = min(bar['open'], bar['close'])
                zone_high = max(bar['open'], bar['close'])
                supply_zones.append(DemandSupplyZone(
                    zone_type="supply",
                    high=zone_high,
                    low=zone_low,
                    origin_index=origin_idx,
                    associated_break=brk,
                ))

        return demand_zones, supply_zones

    # =========================================================================
    # DIRECTION
    # =========================================================================

    def _determine_direction(
        self,
        breaks: List[StructureBreak],
    ) -> StructureDirection:
        """Determine current market structure direction from most recent breaks."""
        if not breaks:
            return StructureDirection.NEUTRAL

        # Use the last few breaks to determine direction
        recent = breaks[-3:] if len(breaks) >= 3 else breaks
        bullish_count = sum(1 for b in recent if b.direction == StructureDirection.BULLISH)
        bearish_count = sum(1 for b in recent if b.direction == StructureDirection.BEARISH)

        if bullish_count > bearish_count:
            return StructureDirection.BULLISH
        elif bearish_count > bullish_count:
            return StructureDirection.BEARISH
        return StructureDirection.NEUTRAL
