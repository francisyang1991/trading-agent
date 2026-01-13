#!/usr/bin/env python3
"""
STOCK PICKING ANALYZER
=======================
Find stocks with potential for significant gains over 60-120 days.

Criteria for Long-Term Winners:
1. Base Formation: Consolidation after uptrend (building energy)
2. Volume Accumulation: Smart money buying (up days > down days volume)
3. Relative Strength: Outperforming SPY
4. Momentum Building: Positive but not overextended
5. Technical Setup: Near support with room to run
6. Low Risk Entry: Defined stop loss with good R:R

Usage:
    # Scan full universe for potential winners
    python tools/stock_picker.py --all
    
    # Scan specific theme
    python tools/stock_picker.py --theme ai_chips
    
    # Scan specific symbols
    python tools/stock_picker.py NVDA PLTR IONQ RKLB
    
    # Filter by min score
    python tools/stock_picker.py --all --min-score 70
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StockPick:
    """A potential stock pick with analysis."""
    symbol: str
    price: float
    
    # Scoring components (0-100 each)
    base_score: float           # Base formation quality
    volume_score: float         # Volume accumulation
    rs_score: float             # Relative strength vs SPY
    momentum_score: float       # Momentum quality (not overextended)
    technical_score: float      # Technical setup quality
    risk_reward_score: float    # Risk/reward ratio
    
    # Overall
    total_score: float
    grade: str                  # A, B, C, D, F
    
    # Trade setup
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    target_60d: float
    target_120d: float
    risk_pct: float
    reward_pct: float
    
    # Analysis
    pattern: str
    catalyst: str
    reasoning: str
    
    # Metrics
    rs_vs_spy: float            # Relative strength number
    volume_ratio: float         # Current volume vs average
    atr_pct: float              # ATR as % of price
    days_in_base: int           # Days consolidating
    dist_from_high: float       # Distance from 52-week high


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    return data.ewm(span=period, adjust=False).mean()


def calculate_sma(data: pd.Series, period: int) -> pd.Series:
    return data.rolling(window=period).mean()


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def get_spy_data(period: str = "1y") -> Optional[pd.DataFrame]:
    """Get SPY benchmark data."""
    try:
        return yf.Ticker("SPY").history(period=period)
    except:
        return None


def load_universe() -> Dict:
    """Load stock universe config."""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'stock_universe.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ============================================================================
# PATTERN DETECTION
# ============================================================================

def detect_base_pattern(data: pd.DataFrame) -> Tuple[str, int, float]:
    """
    Detect base/consolidation patterns.
    Returns: (pattern_name, days_in_base, base_score)
    """
    if len(data) < 60:
        return "INSUFFICIENT_DATA", 0, 0
    
    close = data['Close']
    high = data['High']
    low = data['Low']
    
    # Recent 30 days range
    recent_high = high.tail(30).max()
    recent_low = low.tail(30).min()
    recent_range = (recent_high - recent_low) / recent_low * 100
    
    # Previous 30 days range
    prev_high = high.iloc[-60:-30].max()
    prev_low = low.iloc[-60:-30].min()
    prev_range = (prev_high - prev_low) / prev_low * 100
    
    # 3-month high
    high_3m = high.tail(63).max()
    dist_from_high = (high_3m - close.iloc[-1]) / high_3m * 100
    
    # Current price position
    price = close.iloc[-1]
    ema21 = calculate_ema(close, 21).iloc[-1]
    ema50 = calculate_ema(close, 50).iloc[-1]
    
    # Detect patterns
    score = 50  # Base score
    pattern = "NO_PATTERN"
    days_in_base = 0
    
    # 1. Tight consolidation (volatility contraction)
    if recent_range < prev_range * 0.6 and recent_range < 15:
        pattern = "TIGHT_CONSOLIDATION"
        score = 80
        # Count days in tight range
        avg_price = close.tail(20).mean()
        for i in range(1, min(60, len(close))):
            if abs(close.iloc[-i] - avg_price) / avg_price < 0.05:
                days_in_base += 1
            else:
                break
    
    # 2. Cup with handle (rounded bottom)
    elif dist_from_high < 15 and price > ema21 and price > ema50:
        # Check for cup shape
        mid_point = close.iloc[-30:-15].min()
        if mid_point < close.iloc[-45:-30].mean() * 0.95:
            pattern = "CUP_WITH_HANDLE"
            score = 85
            days_in_base = 30
    
    # 3. Bull flag (small consolidation after run-up)
    elif recent_range < 10 and close.iloc[-30] > close.iloc[-60] * 1.15:
        pattern = "BULL_FLAG"
        score = 75
        days_in_base = 15
    
    # 4. Ascending base (higher lows)
    low_10d = low.tail(10).min()
    low_20d = low.iloc[-20:-10].min()
    low_30d = low.iloc[-30:-20].min()
    if low_10d > low_20d > low_30d:
        pattern = "ASCENDING_BASE"
        score = 70
        days_in_base = 30
    
    # 5. Near 50-day MA support
    elif abs(price - ema50) / ema50 < 0.03 and price > ema50:
        pattern = "50MA_SUPPORT"
        score = 65
        days_in_base = 10
    
    # Bonus for being near highs
    if dist_from_high < 10:
        score += 10
    elif dist_from_high > 25:
        score -= 20
    
    return pattern, days_in_base, min(100, max(0, score))


def calculate_volume_accumulation(data: pd.DataFrame) -> Tuple[float, float]:
    """
    Calculate volume accumulation/distribution.
    Returns: (volume_score, volume_ratio)
    """
    if len(data) < 50:
        return 50, 1.0
    
    close = data['Close']
    volume = data['Volume']
    
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
    
    # Accumulation ratio
    if down_vol > 0:
        acc_ratio = up_vol / down_vol
    else:
        acc_ratio = 2.0
    
    # Score based on accumulation
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
    
    # Bonus for increasing volume near breakout
    if vol_ratio > 1.3:
        score += 15
    elif vol_ratio < 0.7:
        score -= 10
    
    return min(100, max(0, score)), vol_ratio


def calculate_relative_strength(stock_data: pd.DataFrame, spy_data: pd.DataFrame, periods: List[int] = [21, 63, 126]) -> Tuple[float, float]:
    """
    Calculate relative strength vs SPY.
    Returns: (rs_score, rs_value)
    """
    if stock_data is None or spy_data is None:
        return 50, 0
    
    stock_close = stock_data['Close']
    spy_close = spy_data['Close']
    
    # Align dates
    min_len = min(len(stock_close), len(spy_close))
    stock_close = stock_close.tail(min_len)
    spy_close = spy_close.tail(min_len)
    
    # Calculate RS for multiple periods
    rs_values = []
    for period in periods:
        if len(stock_close) >= period:
            stock_return = (stock_close.iloc[-1] / stock_close.iloc[-period] - 1) * 100
            spy_return = (spy_close.iloc[-1] / spy_close.iloc[-period] - 1) * 100
            rs = stock_return - spy_return
            rs_values.append(rs)
    
    if not rs_values:
        return 50, 0
    
    avg_rs = np.mean(rs_values)
    
    # Score based on RS
    score = 50
    if avg_rs > 30:
        score = 90
    elif avg_rs > 20:
        score = 80
    elif avg_rs > 10:
        score = 70
    elif avg_rs > 0:
        score = 60
    elif avg_rs > -10:
        score = 40
    else:
        score = 30
    
    return score, avg_rs


def calculate_momentum_quality(data: pd.DataFrame) -> float:
    """
    Calculate momentum quality (strong but not overextended).
    Returns: momentum_score
    """
    if len(data) < 60:
        return 50
    
    close = data['Close']
    
    # Momentum metrics
    mom_1m = (close.iloc[-1] / close.iloc[-21] - 1) * 100
    mom_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else mom_1m * 3
    
    # RSI
    rsi = calculate_rsi(close).iloc[-1]
    
    # Distance from EMAs
    ema21 = calculate_ema(close, 21).iloc[-1]
    dist_ema21 = (close.iloc[-1] / ema21 - 1) * 100
    
    # Scoring
    score = 50
    
    # Positive momentum but not overextended
    if 5 < mom_1m < 15 and 15 < mom_3m < 50:
        score = 85  # Ideal: building momentum
    elif 0 < mom_1m < 5 and 10 < mom_3m < 30:
        score = 75  # Good: consolidating gains
    elif mom_1m > 20 or dist_ema21 > 15:
        score = 40  # Overextended
    elif mom_1m < 0 and mom_3m > 0:
        score = 65  # Pullback in uptrend
    elif mom_3m < 0:
        score = 30  # Downtrend
    
    # RSI adjustment
    if 40 <= rsi <= 60:
        score += 10  # Neutral RSI is good for entry
    elif rsi > 70:
        score -= 15  # Overbought
    elif rsi < 30:
        score += 5   # Oversold bounce potential
    
    return min(100, max(0, score))


def calculate_technical_setup(data: pd.DataFrame) -> Tuple[float, float, float, float]:
    """
    Calculate technical setup quality.
    Returns: (tech_score, entry_low, entry_high, stop_loss)
    """
    if len(data) < 50:
        return 50, 0, 0, 0
    
    close = data['Close']
    high = data['High']
    low = data['Low']
    
    price = close.iloc[-1]
    atr = calculate_atr(high, low, close).iloc[-1]
    
    ema9 = calculate_ema(close, 9).iloc[-1]
    ema21 = calculate_ema(close, 21).iloc[-1]
    ema50 = calculate_ema(close, 50).iloc[-1]
    
    # EMA alignment score
    score = 50
    
    # Bullish alignment: price > ema9 > ema21 > ema50
    if price > ema9 > ema21 > ema50:
        score = 85
        entry_low = ema9 * 0.99
        entry_high = price
        stop_loss = ema21 * 0.97
    # Above 50 MA
    elif price > ema50 and price > ema21:
        score = 70
        entry_low = ema21 * 0.98
        entry_high = ema21 * 1.02
        stop_loss = ema50 * 0.95
    # Near 50 MA support
    elif abs(price - ema50) / ema50 < 0.03:
        score = 60
        entry_low = ema50 * 0.98
        entry_high = ema50 * 1.02
        stop_loss = ema50 * 0.92
    # Below key MAs
    elif price < ema21 < ema50:
        score = 30
        entry_low = price * 0.95
        entry_high = ema21 * 0.98
        stop_loss = low.tail(20).min() * 0.98
    else:
        entry_low = price - atr
        entry_high = price
        stop_loss = price - 2 * atr
    
    return score, entry_low, entry_high, stop_loss


def calculate_risk_reward(price: float, stop_loss: float, target: float) -> Tuple[float, float, float]:
    """
    Calculate risk/reward metrics.
    Returns: (rr_score, risk_pct, reward_pct)
    """
    risk_pct = (price - stop_loss) / price * 100 if price > stop_loss else 5
    reward_pct = (target - price) / price * 100 if target > price else 10
    
    rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 2
    
    # Score based on R:R
    if rr_ratio >= 4:
        score = 95
    elif rr_ratio >= 3:
        score = 85
    elif rr_ratio >= 2:
        score = 75
    elif rr_ratio >= 1.5:
        score = 60
    else:
        score = 40
    
    # Penalize high risk
    if risk_pct > 15:
        score -= 20
    elif risk_pct > 10:
        score -= 10
    
    return score, risk_pct, reward_pct


# ============================================================================
# MAIN ANALYSIS
# ============================================================================

def analyze_stock(symbol: str, spy_data: pd.DataFrame) -> Optional[StockPick]:
    """Comprehensive stock analysis for long-term potential."""
    
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1y")
        
        if data.empty or len(data) < 100:
            return None
        
        close = data['Close']
        high = data['High']
        low = data['Low']
        
        price = close.iloc[-1]
        atr = calculate_atr(high, low, close).iloc[-1]
        atr_pct = atr / price * 100
        
        # 52-week high
        high_52w = high.max()
        dist_from_high = (high_52w - price) / high_52w * 100
        
        # 1. Base pattern analysis
        pattern, days_in_base, base_score = detect_base_pattern(data)
        
        # 2. Volume accumulation
        volume_score, volume_ratio = calculate_volume_accumulation(data)
        
        # 3. Relative strength
        rs_score, rs_vs_spy = calculate_relative_strength(data, spy_data)
        
        # 4. Momentum quality
        momentum_score = calculate_momentum_quality(data)
        
        # 5. Technical setup
        technical_score, entry_low, entry_high, stop_loss = calculate_technical_setup(data)
        
        # 6. Targets and risk/reward
        target_60d = price * (1 + atr_pct / 100 * 3)  # 3 ATR target for 60 days
        target_120d = price * (1 + atr_pct / 100 * 5)  # 5 ATR target for 120 days
        
        rr_score, risk_pct, reward_pct = calculate_risk_reward(price, stop_loss, target_60d)
        
        # Total score (weighted average)
        total_score = (
            base_score * 0.20 +
            volume_score * 0.15 +
            rs_score * 0.20 +
            momentum_score * 0.20 +
            technical_score * 0.15 +
            rr_score * 0.10
        )
        
        # Grade
        if total_score >= 80:
            grade = "A"
        elif total_score >= 70:
            grade = "B"
        elif total_score >= 60:
            grade = "C"
        elif total_score >= 50:
            grade = "D"
        else:
            grade = "F"
        
        # Catalyst / reasoning
        catalysts = []
        if pattern != "NO_PATTERN":
            catalysts.append(f"Pattern: {pattern}")
        if rs_vs_spy > 20:
            catalysts.append("Strong RS leader")
        if volume_ratio > 1.3:
            catalysts.append("Volume surge")
        if dist_from_high < 10:
            catalysts.append("Near highs")
        
        catalyst = "; ".join(catalysts) if catalysts else "Technical setup"
        
        # Build reasoning
        reasoning_parts = []
        if base_score >= 70:
            reasoning_parts.append(f"Quality base formation ({pattern})")
        if volume_score >= 70:
            reasoning_parts.append("Accumulation detected")
        if rs_score >= 70:
            reasoning_parts.append(f"RS leader ({rs_vs_spy:+.1f}% vs SPY)")
        if momentum_score >= 70:
            reasoning_parts.append("Healthy momentum")
        if technical_score >= 70:
            reasoning_parts.append("Strong technical setup")
        
        reasoning = ". ".join(reasoning_parts) if reasoning_parts else "Mixed signals"
        
        return StockPick(
            symbol=symbol,
            price=price,
            base_score=base_score,
            volume_score=volume_score,
            rs_score=rs_score,
            momentum_score=momentum_score,
            technical_score=technical_score,
            risk_reward_score=rr_score,
            total_score=total_score,
            grade=grade,
            entry_zone_low=entry_low,
            entry_zone_high=entry_high,
            stop_loss=stop_loss,
            target_60d=target_60d,
            target_120d=target_120d,
            risk_pct=risk_pct,
            reward_pct=reward_pct,
            pattern=pattern,
            catalyst=catalyst,
            reasoning=reasoning,
            rs_vs_spy=rs_vs_spy,
            volume_ratio=volume_ratio,
            atr_pct=atr_pct,
            days_in_base=days_in_base,
            dist_from_high=dist_from_high
        )
        
    except Exception as e:
        print(f"   ⚠️ Error analyzing {symbol}: {e}")
        return None


def scan_for_picks(symbols: List[str], min_score: float = 60) -> List[StockPick]:
    """Scan symbols for potential picks."""
    
    print(f"\n🔍 Analyzing {len(symbols)} stocks for potential winners...")
    
    # Get SPY data
    spy_data = get_spy_data()
    
    picks = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(analyze_stock, s, spy_data): s for s in symbols}
        for future in as_completed(futures):
            result = future.result()
            if result and result.total_score >= min_score:
                picks.append(result)
    
    # Sort by score
    picks.sort(key=lambda x: x.total_score, reverse=True)
    
    return picks


# ============================================================================
# OUTPUT
# ============================================================================

def print_picks(picks: List[StockPick]):
    """Print stock picks."""
    
    if not picks:
        print("\n❌ No stocks meet the criteria")
        return
    
    print(f"\n{'='*120}")
    print(f"  POTENTIAL LONG-TERM WINNERS ({len(picks)} stocks)")
    print(f"  Time Horizon: 60-120 days")
    print(f"{'='*120}")
    
    # Summary table
    print(f"\n{'Symbol':<8} {'Grade':>6} {'Score':>6} {'Price':>10} {'Entry Zone':>18} {'Stop':>10} {'Target60':>10} {'Target120':>10} {'R:R':>6}")
    print("-" * 120)
    
    for p in picks[:20]:  # Top 20
        rr = p.reward_pct / p.risk_pct if p.risk_pct > 0 else 0
        print(f"{p.symbol:<8} {p.grade:>6} {p.total_score:>5.0f} ${p.price:>9.2f} "
              f"${p.entry_zone_low:>.2f}-${p.entry_zone_high:.2f} "
              f"${p.stop_loss:>9.2f} ${p.target_60d:>9.2f} ${p.target_120d:>9.2f} {rr:>5.1f}x")
    
    # Detailed analysis for top picks
    print(f"\n{'='*120}")
    print(f"  DETAILED ANALYSIS - TOP PICKS")
    print(f"{'='*120}")
    
    for p in picks[:5]:  # Top 5 detailed
        print(f"\n{'─'*80}")
        print(f"  {p.symbol} - Grade {p.grade} (Score: {p.total_score:.0f}/100)")
        print(f"{'─'*80}")
        print(f"  Price: ${p.price:.2f} | {p.dist_from_high:.1f}% from 52W high")
        print(f"  Pattern: {p.pattern} ({p.days_in_base} days)")
        print(f"  Catalyst: {p.catalyst}")
        print(f"\n  SCORE BREAKDOWN:")
        print(f"    Base Formation:    {p.base_score:>5.0f}/100")
        print(f"    Volume Accum:      {p.volume_score:>5.0f}/100")
        print(f"    Relative Strength: {p.rs_score:>5.0f}/100 (RS: {p.rs_vs_spy:+.1f}%)")
        print(f"    Momentum Quality:  {p.momentum_score:>5.0f}/100")
        print(f"    Technical Setup:   {p.technical_score:>5.0f}/100")
        print(f"    Risk/Reward:       {p.risk_reward_score:>5.0f}/100")
        print(f"\n  TRADE SETUP:")
        print(f"    Entry Zone:  ${p.entry_zone_low:.2f} - ${p.entry_zone_high:.2f}")
        print(f"    Stop Loss:   ${p.stop_loss:.2f} ({p.risk_pct:.1f}% risk)")
        print(f"    Target 60D:  ${p.target_60d:.2f} ({(p.target_60d/p.price-1)*100:+.1f}%)")
        print(f"    Target 120D: ${p.target_120d:.2f} ({(p.target_120d/p.price-1)*100:+.1f}%)")
        print(f"\n  REASONING: {p.reasoning}")
    
    # Category breakdown
    print(f"\n{'='*120}")
    print(f"  PICKS BY GRADE")
    print(f"{'='*120}")
    
    for grade in ['A', 'B', 'C', 'D']:
        grade_picks = [p for p in picks if p.grade == grade]
        if grade_picks:
            symbols = ', '.join([p.symbol for p in grade_picks[:10]])
            print(f"  Grade {grade}: {len(grade_picks)} stocks - {symbols}")


def save_picks(picks: List[StockPick], output_file: str):
    """Save picks to file."""
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 120 + "\n")
        f.write(f"  STOCK PICKS - POTENTIAL LONG-TERM WINNERS\n")
        f.write(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"  Time Horizon: 60-120 days\n")
        f.write(f"  Total Picks: {len(picks)}\n")
        f.write("=" * 120 + "\n\n")
        
        f.write(f"{'Symbol':<8} {'Grade':>6} {'Score':>6} {'Price':>10} {'Stop':>10} {'T60':>10} {'T120':>10} {'Pattern':<20}\n")
        f.write("-" * 100 + "\n")
        
        for p in picks:
            f.write(f"{p.symbol:<8} {p.grade:>6} {p.total_score:>5.0f} ${p.price:>9.2f} "
                   f"${p.stop_loss:>9.2f} ${p.target_60d:>9.2f} ${p.target_120d:>9.2f} {p.pattern:<20}\n")
        
        f.write("\n" + "=" * 120 + "\n")
        f.write("  DETAILED BREAKDOWN\n")
        f.write("=" * 120 + "\n")
        
        for p in picks[:10]:
            f.write(f"\n{p.symbol} - Grade {p.grade}\n")
            f.write(f"  Score: {p.total_score:.0f} | Price: ${p.price:.2f}\n")
            f.write(f"  Entry: ${p.entry_zone_low:.2f}-${p.entry_zone_high:.2f}\n")
            f.write(f"  Stop: ${p.stop_loss:.2f} | Target60: ${p.target_60d:.2f} | Target120: ${p.target_120d:.2f}\n")
            f.write(f"  Reasoning: {p.reasoning}\n")
    
    print(f"\n✅ Picks saved to: {output_file}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Stock Picking Analyzer")
    parser.add_argument("symbols", nargs="*", help="Symbols to analyze")
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument("--min-score", type=float, default=60, help="Minimum score threshold")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    
    args = parser.parse_args()
    
    universe = load_universe()
    
    # Get symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.theme:
        if args.theme in universe['themes']:
            symbols = universe['themes'][args.theme]['symbols']
            print(f"📊 Scanning theme: {universe['themes'][args.theme].get('name', args.theme)}")
        else:
            print(f"❌ Theme '{args.theme}' not found!")
            return
    elif args.all:
        symbols = universe.get('all_symbols', [])
        print(f"📊 Scanning full universe")
    else:
        # Default: momentum leaders + high conviction
        symbols = universe.get('high_conviction', []) + universe.get('volatile_momentum', [])
        symbols = list(set(symbols))
        print(f"📊 Scanning high conviction + momentum stocks")
    
    # Run analysis
    picks = scan_for_picks(symbols, args.min_score)
    
    # Print results
    print_picks(picks)
    
    # Save results
    if args.output:
        save_picks(picks, args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(os.path.dirname(__file__), '..', 'results', f'picks_{timestamp}.txt')
        save_picks(picks, output_file)


if __name__ == "__main__":
    main()
