"""
Stock Picker - Systematic stock selection with multi-factor scoring.

The stock picker analyzes stocks based on multiple factors:
1. Momentum - 6M and 3M price momentum
2. Relative Strength - Performance vs SPY
3. Technical Setup - EMA alignment, base patterns
4. Volume Analysis - Accumulation/distribution
5. Risk/Reward - Entry, stop, target analysis

Each factor contributes to an overall score (0-100) which determines
the stock's grade and suitability for trading.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from ..core.types import (
    PickScore, Regime, VolatilityLevel, Strategy,
    get_regime_from_momentum, get_volatility_level, calculate_grade
)

logger = logging.getLogger(__name__)


# =============================================================================
# FACTOR WEIGHTS
# =============================================================================

DEFAULT_WEIGHTS = {
    'momentum': 0.20,
    'relative_strength': 0.20,
    'technical': 0.20,
    'volume': 0.15,
    'base_pattern': 0.15,
    'risk_reward': 0.10,
}


# =============================================================================
# STRATEGY MATRIX
# =============================================================================

STRATEGY_MATRIX = {
    # (Regime, VolatilityLevel) -> (Strategy, base_position_pct, stop_pct, target_pct)
    (Regime.PARABOLIC, VolatilityLevel.LOW): (Strategy.BUY_HOLD, 0.15, 0.10, 0.50),
    (Regime.PARABOLIC, VolatilityLevel.MODERATE): (Strategy.TRAILING_STOP, 0.12, 0.12, 0.40),
    (Regime.PARABOLIC, VolatilityLevel.HIGH): (Strategy.TRAILING_STOP, 0.08, 0.15, 0.35),
    (Regime.PARABOLIC, VolatilityLevel.ULTRA_HIGH): (Strategy.TRAILING_STOP, 0.05, 0.20, 0.30),
    
    (Regime.STRONG_UP, VolatilityLevel.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.08, 0.25),
    (Regime.STRONG_UP, VolatilityLevel.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.10, 0.25),
    (Regime.STRONG_UP, VolatilityLevel.HIGH): (Strategy.SWING_TRADE, 0.08, 0.12, 0.20),
    (Regime.STRONG_UP, VolatilityLevel.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.15, 0.20),
    
    (Regime.MODERATE_UP, VolatilityLevel.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.06, 0.15),
    (Regime.MODERATE_UP, VolatilityLevel.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.08, 0.15),
    (Regime.MODERATE_UP, VolatilityLevel.HIGH): (Strategy.SWING_TRADE, 0.08, 0.10, 0.15),
    (Regime.MODERATE_UP, VolatilityLevel.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.12, 0.15),
    
    (Regime.WEAK_UP, VolatilityLevel.LOW): (Strategy.SWING_TRADE, 0.12, 0.05, 0.10),
    (Regime.WEAK_UP, VolatilityLevel.MODERATE): (Strategy.SWING_TRADE, 0.10, 0.06, 0.10),
    (Regime.WEAK_UP, VolatilityLevel.HIGH): (Strategy.MEAN_REVERSION, 0.08, 0.08, 0.10),
    (Regime.WEAK_UP, VolatilityLevel.ULTRA_HIGH): (Strategy.MEAN_REVERSION, 0.05, 0.10, 0.10),
    
    (Regime.SIDEWAYS, VolatilityLevel.LOW): (Strategy.MEAN_REVERSION, 0.12, 0.04, 0.08),
    (Regime.SIDEWAYS, VolatilityLevel.MODERATE): (Strategy.MEAN_REVERSION, 0.10, 0.05, 0.08),
    (Regime.SIDEWAYS, VolatilityLevel.HIGH): (Strategy.MEAN_REVERSION, 0.06, 0.08, 0.10),
    (Regime.SIDEWAYS, VolatilityLevel.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    
    (Regime.DOWNTREND, VolatilityLevel.LOW): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolatilityLevel.MODERATE): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolatilityLevel.HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolatilityLevel.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    """Calculate exponential moving average."""
    return data.ewm(span=period, adjust=False).mean()


def calculate_sma(data: pd.Series, period: int) -> pd.Series:
    """Calculate simple moving average."""
    return data.rolling(window=period).mean()


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI."""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# =============================================================================
# FACTOR CALCULATIONS
# =============================================================================

class FactorCalculator:
    """Calculate individual factors for stock scoring."""
    
    def __init__(self, spy_data: Optional[pd.DataFrame] = None):
        """
        Initialize factor calculator.
        
        Args:
            spy_data: SPY data for relative strength calculation
        """
        self.spy_data = spy_data
    
    def momentum_score(self, data: pd.DataFrame) -> Tuple[float, Dict]:
        """
        Calculate momentum score (0-100).
        
        Based on:
        - 6-month momentum
        - 3-month momentum
        - 1-month momentum
        - Momentum trend (accelerating/decelerating)
        """
        if len(data) < 126:
            return 50.0, {'momentum_6m': 0, 'momentum_3m': 0}
        
        close = data['close']
        
        mom_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100
        mom_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
        mom_1m = (close.iloc[-1] / close.iloc[-21] - 1) * 100
        
        # Score based on 6M momentum
        if mom_6m > 100:
            score = 95
        elif mom_6m > 50:
            score = 85
        elif mom_6m > 20:
            score = 70
        elif mom_6m > 10:
            score = 60
        elif mom_6m > 0:
            score = 50
        elif mom_6m > -10:
            score = 40
        elif mom_6m > -20:
            score = 30
        else:
            score = 20
        
        # Bonus for accelerating momentum
        if mom_1m > mom_3m / 3:  # Recent strength
            score += 5
        
        return min(100, max(0, score)), {
            'momentum_6m': mom_6m,
            'momentum_3m': mom_3m,
            'momentum_1m': mom_1m
        }
    
    def relative_strength_score(self, data: pd.DataFrame) -> Tuple[float, Dict]:
        """
        Calculate relative strength vs SPY (0-100).
        """
        if self.spy_data is None or len(data) < 63:
            return 50.0, {'rs_vs_spy': 0}
        
        close = data['close']
        spy_close = self.spy_data['close']
        
        # Align dates
        min_len = min(len(close), len(spy_close))
        close = close.tail(min_len)
        spy_close = spy_close.tail(min_len)
        
        # Calculate RS for multiple periods
        rs_values = []
        for period in [21, 63, 126]:
            if len(close) >= period:
                stock_return = (close.iloc[-1] / close.iloc[-period] - 1) * 100
                spy_return = (spy_close.iloc[-1] / spy_close.iloc[-period] - 1) * 100
                rs_values.append(stock_return - spy_return)
        
        if not rs_values:
            return 50.0, {'rs_vs_spy': 0}
        
        avg_rs = np.mean(rs_values)
        
        # Score based on RS
        if avg_rs > 40:
            score = 95
        elif avg_rs > 25:
            score = 85
        elif avg_rs > 15:
            score = 75
        elif avg_rs > 5:
            score = 65
        elif avg_rs > 0:
            score = 55
        elif avg_rs > -10:
            score = 40
        elif avg_rs > -20:
            score = 30
        else:
            score = 20
        
        return score, {'rs_vs_spy': avg_rs}
    
    def technical_score(self, data: pd.DataFrame) -> Tuple[float, Dict]:
        """
        Calculate technical setup score (0-100).
        
        Based on EMA alignment and price structure.
        """
        if len(data) < 50:
            return 50.0, {}
        
        close = data['close']
        price = close.iloc[-1]
        
        ema9 = calculate_ema(close, 9).iloc[-1]
        ema21 = calculate_ema(close, 21).iloc[-1]
        ema50 = calculate_ema(close, 50).iloc[-1]
        
        dist_ema21 = (price / ema21 - 1) * 100
        rsi = calculate_rsi(close).iloc[-1]
        
        # Score EMA alignment
        score = 50
        
        # Bullish alignment: price > ema9 > ema21 > ema50
        if price > ema9 > ema21 > ema50:
            score = 85
        elif price > ema21 > ema50:
            score = 70
        elif price > ema50:
            score = 55
        elif price < ema21 < ema50:
            score = 30
        elif price < ema50:
            score = 25
        
        # RSI adjustment
        if 40 <= rsi <= 60:
            score += 5  # Neutral is good for entry
        elif rsi > 75:
            score -= 10  # Overbought
        elif rsi < 25:
            score += 3  # Oversold bounce potential
        
        return min(100, max(0, score)), {
            'dist_ema21': dist_ema21,
            'rsi': rsi,
            'ema_alignment': 'bullish' if price > ema9 > ema21 else 'mixed'
        }
    
    def volume_score(self, data: pd.DataFrame) -> Tuple[float, Dict]:
        """
        Calculate volume accumulation score (0-100).
        """
        if len(data) < 50:
            return 50.0, {}
        
        close = data['close']
        volume = data['volume']
        
        # Volume moving average
        vol_ma = volume.rolling(50).mean()
        current_vol = volume.tail(5).mean()
        vol_ratio = current_vol / vol_ma.iloc[-1] if vol_ma.iloc[-1] > 0 else 1.0
        
        # Up volume vs down volume (last 20 days)
        up_vol = 0
        down_vol = 0
        for i in range(-20, 0):
            if close.iloc[i] > close.iloc[i-1]:
                up_vol += volume.iloc[i]
            else:
                down_vol += volume.iloc[i]
        
        acc_ratio = up_vol / down_vol if down_vol > 0 else 2.0
        
        # Score
        score = 50
        if acc_ratio > 1.5:
            score = 80
        elif acc_ratio > 1.2:
            score = 70
        elif acc_ratio > 1.0:
            score = 60
        elif acc_ratio > 0.8:
            score = 40
        else:
            score = 30
        
        # Volume surge bonus
        if vol_ratio > 1.5:
            score += 10
        elif vol_ratio < 0.7:
            score -= 5
        
        return min(100, max(0, score)), {
            'volume_ratio': vol_ratio,
            'acc_ratio': acc_ratio
        }
    
    def base_pattern_score(self, data: pd.DataFrame) -> Tuple[float, str, int]:
        """
        Detect and score base/consolidation patterns.
        
        Returns: (score, pattern_name, days_in_base)
        """
        if len(data) < 60:
            return 50.0, "INSUFFICIENT_DATA", 0
        
        close = data['close']
        high = data['high']
        low = data['low']
        
        # Recent 30 days range
        recent_high = high.tail(30).max()
        recent_low = low.tail(30).min()
        recent_range = (recent_high - recent_low) / recent_low * 100
        
        # Previous 30 days range
        prev_high = high.iloc[-60:-30].max()
        prev_low = low.iloc[-60:-30].min()
        prev_range = (prev_high - prev_low) / prev_low * 100
        
        # Distance from high
        high_3m = high.tail(63).max()
        dist_from_high = (high_3m - close.iloc[-1]) / high_3m * 100
        
        price = close.iloc[-1]
        ema21 = calculate_ema(close, 21).iloc[-1]
        ema50 = calculate_ema(close, 50).iloc[-1]
        
        score = 50
        pattern = "NO_PATTERN"
        days_in_base = 0
        
        # Tight consolidation (volatility contraction)
        if recent_range < prev_range * 0.6 and recent_range < 15:
            pattern = "TIGHT_CONSOLIDATION"
            score = 80
            avg_price = close.tail(20).mean()
            for i in range(1, min(60, len(close))):
                if abs(close.iloc[-i] - avg_price) / avg_price < 0.05:
                    days_in_base += 1
                else:
                    break
        
        # Cup with handle
        elif dist_from_high < 15 and price > ema21 and price > ema50:
            mid_point = close.iloc[-30:-15].min()
            if mid_point < close.iloc[-45:-30].mean() * 0.95:
                pattern = "CUP_WITH_HANDLE"
                score = 85
                days_in_base = 30
        
        # Bull flag
        elif recent_range < 10 and close.iloc[-30] > close.iloc[-60] * 1.15:
            pattern = "BULL_FLAG"
            score = 75
            days_in_base = 15
        
        # Ascending base
        low_10d = low.tail(10).min()
        low_20d = low.iloc[-20:-10].min()
        low_30d = low.iloc[-30:-20].min()
        if low_10d > low_20d > low_30d:
            pattern = "ASCENDING_BASE"
            score = 70
            days_in_base = 30
        
        # Near highs bonus
        if dist_from_high < 10:
            score += 10
        elif dist_from_high > 25:
            score -= 15
        
        return min(100, max(0, score)), pattern, days_in_base
    
    def risk_reward_score(
        self,
        data: pd.DataFrame,
        entry_price: float,
        stop_price: float,
        target_price: float
    ) -> Tuple[float, Dict]:
        """
        Calculate risk/reward score.
        """
        risk_pct = (entry_price - stop_price) / entry_price * 100
        reward_pct = (target_price - entry_price) / entry_price * 100
        
        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 2.0
        
        if rr_ratio >= 4:
            score = 95
        elif rr_ratio >= 3:
            score = 85
        elif rr_ratio >= 2:
            score = 70
        elif rr_ratio >= 1.5:
            score = 55
        elif rr_ratio >= 1:
            score = 40
        else:
            score = 25
        
        # Penalize high risk
        if risk_pct > 15:
            score -= 20
        elif risk_pct > 10:
            score -= 10
        
        return min(100, max(0, score)), {
            'risk_pct': risk_pct,
            'reward_pct': reward_pct,
            'rr_ratio': rr_ratio
        }


# =============================================================================
# STOCK PICKER
# =============================================================================

class StockPicker:
    """
    Stock picker with multi-factor scoring and ranking.
    
    Example:
        picker = StockPicker()
        picks = picker.analyze(['NVDA', 'AAPL', 'MSFT', 'PLTR'])
        
        for pick in picks:
            if pick.grade in ['A', 'B']:
                print(f"{pick.symbol}: Score {pick.total_score}, {pick.strategy}")
    """
    
    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        min_score: float = 50.0,
        spy_data: Optional[pd.DataFrame] = None
    ):
        """
        Initialize stock picker.
        
        Args:
            weights: Factor weights (must sum to 1.0)
            min_score: Minimum score to include in results
            spy_data: SPY data for relative strength
        """
        self.weights = weights or DEFAULT_WEIGHTS
        self.min_score = min_score
        self.spy_data = spy_data
        self.factor_calc = FactorCalculator(spy_data)
    
    def set_spy_data(self, spy_data: pd.DataFrame):
        """Set SPY data for relative strength calculation."""
        self.spy_data = spy_data
        self.factor_calc = FactorCalculator(spy_data)
    
    def analyze_stock(self, symbol: str, data: pd.DataFrame) -> Optional[PickScore]:
        """
        Analyze a single stock.
        
        Args:
            symbol: Stock symbol
            data: OHLCV DataFrame
            
        Returns:
            PickScore or None if insufficient data
        """
        if data.empty or len(data) < 100:
            logger.warning(f"{symbol}: Insufficient data")
            return None
        
        try:
            close = data['close']
            high = data['high']
            low = data['low']
            
            price = close.iloc[-1]
            atr = calculate_atr(high, low, close).iloc[-1]
            atr_pct = atr / price * 100
            
            # Calculate factors
            mom_score, mom_metrics = self.factor_calc.momentum_score(data)
            rs_score, rs_metrics = self.factor_calc.relative_strength_score(data)
            tech_score, tech_metrics = self.factor_calc.technical_score(data)
            vol_score, vol_metrics = self.factor_calc.volume_score(data)
            base_score, pattern, days_in_base = self.factor_calc.base_pattern_score(data)
            
            # Calculate entry zones
            ema9 = calculate_ema(close, 9).iloc[-1]
            ema21 = calculate_ema(close, 21).iloc[-1]
            ema50 = calculate_ema(close, 50).iloc[-1]
            
            entry_zone_high = price
            entry_zone_low = max(ema9 * 0.98, price - 1.5 * atr)
            stop_loss = ema21 * 0.97 if price > ema21 else price - 2.5 * atr
            target_1 = price + 1.5 * atr
            target_2 = price + 3 * atr
            
            # Risk/reward score
            rr_score, rr_metrics = self.factor_calc.risk_reward_score(
                data, price, stop_loss, target_1
            )
            
            # Calculate total score
            total_score = (
                mom_score * self.weights.get('momentum', 0.20) +
                rs_score * self.weights.get('relative_strength', 0.20) +
                tech_score * self.weights.get('technical', 0.20) +
                vol_score * self.weights.get('volume', 0.15) +
                base_score * self.weights.get('base_pattern', 0.15) +
                rr_score * self.weights.get('risk_reward', 0.10)
            )
            
            grade = calculate_grade(total_score)
            
            # Classify regime and volatility
            volatility = close.pct_change().std() * np.sqrt(252) * 100
            regime = get_regime_from_momentum(mom_metrics.get('momentum_6m', 0))
            vol_level = get_volatility_level(volatility)
            
            # Get strategy from matrix
            strategy_info = STRATEGY_MATRIX.get(
                (regime, vol_level),
                (Strategy.STAY_CASH, 0.0, 0.0, 0.0)
            )
            strategy = strategy_info[0]
            base_position_pct = strategy_info[1]
            
            # Expected value estimation
            win_rate, avg_win, avg_loss = self._estimate_expected_value(data)
            expected_value = win_rate * avg_win - (1 - win_rate) * avg_loss
            
            # Kelly criterion
            if avg_loss > 0:
                b = avg_win / avg_loss
                kelly = max(0, (win_rate * b - (1 - win_rate)) / b)
                kelly_pct = min(kelly * 100, 25)
            else:
                kelly_pct = 0
            
            # Position sizing (vol-adjusted)
            position_size_pct = min(
                base_position_pct * 100,
                15 / (volatility / 100) if volatility > 0 else 15,
                kelly_pct
            )
            
            # Distance from 52-week high
            high_52w = high.max()
            dist_from_high = (high_52w - price) / high_52w * 100
            
            # Build catalysts list
            catalysts = []
            if pattern != "NO_PATTERN":
                catalysts.append(f"Pattern: {pattern}")
            if rs_metrics.get('rs_vs_spy', 0) > 20:
                catalysts.append("RS Leader")
            if vol_metrics.get('volume_ratio', 1) > 1.5:
                catalysts.append("Volume Surge")
            if dist_from_high < 10:
                catalysts.append("Near Highs")
            
            # Build reasoning
            reasoning_parts = []
            if mom_score >= 70:
                reasoning_parts.append(f"Strong momentum ({mom_metrics.get('momentum_6m', 0):+.1f}%)")
            if rs_score >= 70:
                reasoning_parts.append(f"RS leader")
            if tech_score >= 70:
                reasoning_parts.append("Bullish technical setup")
            if vol_score >= 70:
                reasoning_parts.append("Volume accumulation")
            if base_score >= 70:
                reasoning_parts.append(f"{pattern}")
            
            reasoning = ". ".join(reasoning_parts) if reasoning_parts else "Mixed signals"
            
            return PickScore(
                symbol=symbol,
                timestamp=datetime.now(),
                total_score=total_score,
                grade=grade,
                rank=0,  # Set later in ranking
                momentum_score=mom_score,
                relative_strength_score=rs_score,
                quality_score=50.0,  # Not calculated yet
                value_score=50.0,    # Not calculated yet
                technical_score=tech_score,
                volume_score=vol_score,
                momentum_6m=mom_metrics.get('momentum_6m', 0),
                momentum_3m=mom_metrics.get('momentum_3m', 0),
                rs_vs_spy=rs_metrics.get('rs_vs_spy', 0),
                volatility=volatility,
                rsi=tech_metrics.get('rsi', 50),
                dist_from_high=dist_from_high,
                regime=regime,
                volatility_level=vol_level,
                strategy=strategy,
                entry_zone_low=entry_zone_low,
                entry_zone_high=entry_zone_high,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss,
                expected_value=expected_value,
                risk_reward=rr_metrics.get('rr_ratio', 0),
                position_size_pct=position_size_pct,
                kelly_pct=kelly_pct,
                pattern=pattern,
                catalysts=catalysts,
                reasoning=reasoning
            )
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    def _estimate_expected_value(self, data: pd.DataFrame, forward_days: int = 5) -> Tuple[float, float, float]:
        """Estimate win rate, avg win, avg loss from historical data."""
        close = data['close']
        forward_returns = (close.shift(-forward_days) / close - 1) * 100
        forward_returns = forward_returns.dropna()
        
        if len(forward_returns) < 50:
            return 0.5, 5.0, 5.0
        
        wins = forward_returns[forward_returns > 0]
        losses = forward_returns[forward_returns <= 0]
        
        win_rate = len(wins) / len(forward_returns)
        avg_win = wins.mean() if len(wins) > 0 else 0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0
        
        return win_rate, avg_win, avg_loss
    
    def analyze(
        self,
        symbols: List[str],
        data_dict: Dict[str, pd.DataFrame],
        max_workers: int = 10
    ) -> List[PickScore]:
        """
        Analyze multiple stocks.
        
        Args:
            symbols: List of symbols
            data_dict: Dict mapping symbol to DataFrame
            max_workers: Max parallel workers
            
        Returns:
            List of PickScore sorted by score
        """
        picks = []
        
        for symbol in symbols:
            if symbol not in data_dict:
                continue
            
            pick = self.analyze_stock(symbol, data_dict[symbol])
            if pick and pick.total_score >= self.min_score:
                picks.append(pick)
        
        # Sort by score and assign ranks
        picks.sort(key=lambda x: x.total_score, reverse=True)
        for i, pick in enumerate(picks):
            pick.rank = i + 1
        
        return picks


def pick_stocks(
    symbols: List[str],
    data_dict: Dict[str, pd.DataFrame],
    spy_data: Optional[pd.DataFrame] = None,
    min_score: float = 50.0,
    weights: Optional[Dict[str, float]] = None
) -> List[PickScore]:
    """
    Convenience function to pick stocks.
    
    Args:
        symbols: List of symbols to analyze
        data_dict: Dict mapping symbol to OHLCV DataFrame
        spy_data: SPY data for relative strength
        min_score: Minimum score threshold
        weights: Factor weights
        
    Returns:
        List of PickScore sorted by score
    """
    picker = StockPicker(weights=weights, min_score=min_score, spy_data=spy_data)
    return picker.analyze(symbols, data_dict)
