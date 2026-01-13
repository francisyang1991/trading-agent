#!/usr/bin/env python3
"""
UNIFIED STOCK SCANNER
=====================
Combines: universe_scanner + strategy_selector + monday_plan

Features:
1. Scan universe and classify stocks by regime/volatility
2. Select optimal strategy from matrix
3. Generate actionable trading plans with:
   - Buy zones & invalidation
   - Expected value (EV) calculation
   - Position sizing (Kelly, vol-targeting)
   - Risk/reward analysis
4. Output to formatted file (default) or console

Usage:
    # Quick scan (outputs to results/scan_YYYYMMDD_HHMMSS.txt)
    python tools/scanner.py AAPL NVDA IREN
    
    # Scan theme
    python tools/scanner.py --theme crypto
    python tools/scanner.py --theme wilson
    
    # Full universe
    python tools/scanner.py --all
    
    # Monday plan (detailed)
    python tools/scanner.py --plan IREN FLNC HOOD
    
    # Custom output file
    python tools/scanner.py AAPL NVDA --output my_scan.txt
    
    # Print to console instead of file
    python tools/scanner.py AAPL NVDA --console
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# ENUMS
# ============================================================================

class Regime(Enum):
    PARABOLIC = "PARABOLIC"      # >100% 6M momentum
    STRONG_UP = "STRONG_UP"      # 50-100%
    MODERATE_UP = "MODERATE_UP"  # 20-50%
    WEAK_UP = "WEAK_UP"          # 0-20%
    SIDEWAYS = "SIDEWAYS"        # -20% to 0%
    DOWNTREND = "DOWNTREND"      # <-20%


class VolCategory(Enum):
    ULTRA_HIGH = "ULTRA_HIGH"    # >80%
    HIGH = "HIGH"                # 50-80%
    MODERATE = "MODERATE"        # 30-50%
    LOW = "LOW"                  # <30%


class Strategy(Enum):
    BUY_HOLD = "Buy & Hold"
    TRAILING_STOP = "Trailing Stop"
    TREND_FOLLOWING = "Trend Following"
    SWING_TRADE = "Swing Trade"
    MEAN_REVERSION = "Mean Reversion"
    STAY_CASH = "Stay Cash"


class Action(Enum):
    STRONG_BUY = "🟢 STRONG BUY"
    BUY = "🟡 BUY"
    WAIT = "⚪ WAIT"
    AVOID = "🔴 AVOID"


# ============================================================================
# STRATEGY MATRIX
# ============================================================================

STRATEGY_MATRIX = {
    # (Regime, VolCategory) -> (Strategy, position_size, stop_loss, take_profit)
    (Regime.PARABOLIC, VolCategory.LOW): (Strategy.BUY_HOLD, 0.15, 0.10, 0.50),
    (Regime.PARABOLIC, VolCategory.MODERATE): (Strategy.TRAILING_STOP, 0.12, 0.12, 0.40),
    (Regime.PARABOLIC, VolCategory.HIGH): (Strategy.TRAILING_STOP, 0.08, 0.15, 0.35),
    (Regime.PARABOLIC, VolCategory.ULTRA_HIGH): (Strategy.TRAILING_STOP, 0.05, 0.20, 0.30),
    
    (Regime.STRONG_UP, VolCategory.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.08, 0.25),
    (Regime.STRONG_UP, VolCategory.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.10, 0.25),
    (Regime.STRONG_UP, VolCategory.HIGH): (Strategy.SWING_TRADE, 0.08, 0.12, 0.20),
    (Regime.STRONG_UP, VolCategory.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.15, 0.20),
    
    (Regime.MODERATE_UP, VolCategory.LOW): (Strategy.TREND_FOLLOWING, 0.15, 0.06, 0.15),
    (Regime.MODERATE_UP, VolCategory.MODERATE): (Strategy.TREND_FOLLOWING, 0.12, 0.08, 0.15),
    (Regime.MODERATE_UP, VolCategory.HIGH): (Strategy.SWING_TRADE, 0.08, 0.10, 0.15),
    (Regime.MODERATE_UP, VolCategory.ULTRA_HIGH): (Strategy.SWING_TRADE, 0.05, 0.12, 0.15),
    
    (Regime.WEAK_UP, VolCategory.LOW): (Strategy.SWING_TRADE, 0.12, 0.05, 0.10),
    (Regime.WEAK_UP, VolCategory.MODERATE): (Strategy.SWING_TRADE, 0.10, 0.06, 0.10),
    (Regime.WEAK_UP, VolCategory.HIGH): (Strategy.MEAN_REVERSION, 0.08, 0.08, 0.10),
    (Regime.WEAK_UP, VolCategory.ULTRA_HIGH): (Strategy.MEAN_REVERSION, 0.05, 0.10, 0.10),
    
    (Regime.SIDEWAYS, VolCategory.LOW): (Strategy.MEAN_REVERSION, 0.12, 0.04, 0.08),
    (Regime.SIDEWAYS, VolCategory.MODERATE): (Strategy.MEAN_REVERSION, 0.10, 0.05, 0.08),
    (Regime.SIDEWAYS, VolCategory.HIGH): (Strategy.MEAN_REVERSION, 0.06, 0.08, 0.10),
    (Regime.SIDEWAYS, VolCategory.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    
    (Regime.DOWNTREND, VolCategory.LOW): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.MODERATE): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
    (Regime.DOWNTREND, VolCategory.ULTRA_HIGH): (Strategy.STAY_CASH, 0.00, 0.00, 0.00),
}


# ============================================================================
# DATA CLASS
# ============================================================================

@dataclass
class StockScan:
    # Basic info
    symbol: str
    price: float
    
    # Classification
    regime: str
    vol_category: str
    strategy: str
    
    # Metrics
    momentum_6m: float
    momentum_3m: float
    volatility: float
    rsi: float
    atr_pct: float
    dist_ema21: float
    
    # Entry/Exit (for trading plan)
    buy_zone_low: float
    buy_zone_high: float
    stop_loss: float
    target_1: float
    target_2: float
    
    # Expected value analysis
    win_rate: float
    avg_win: float
    avg_loss: float
    expected_value: float
    risk_reward: float
    
    # Position sizing
    position_size_pct: float
    kelly_pct: float
    
    # Action
    action: str
    entry_signal: str
    reasoning: str
    score: float


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    return data.ewm(span=period, adjust=False).mean()


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


def estimate_distribution(data: pd.DataFrame, forward_days: int = 5) -> Dict:
    """Estimate forward return distribution."""
    close = data['Close']
    forward_returns = (close.shift(-forward_days) / close - 1) * 100
    forward_returns = forward_returns.dropna()
    
    if len(forward_returns) < 50:
        return {'win_rate': 0.5, 'avg_win': 5, 'avg_loss': 5}
    
    wins = forward_returns[forward_returns > 0]
    losses = forward_returns[forward_returns <= 0]
    
    return {
        'win_rate': len(wins) / len(forward_returns),
        'avg_win': wins.mean() if len(wins) > 0 else 0,
        'avg_loss': abs(losses.mean()) if len(losses) > 0 else 0
    }


def calculate_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Expected Value = P(win)*E(win) - P(loss)*E(loss)"""
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def calculate_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kelly Criterion: f* = (p*b - q) / b"""
    if avg_loss == 0:
        return 0
    b = avg_win / avg_loss
    q = 1 - win_rate
    kelly = (win_rate * b - q) / b
    return max(0, min(0.25, kelly))


def load_universe() -> Dict:
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'stock_universe.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ============================================================================
# MAIN SCAN FUNCTION
# ============================================================================

def scan_stock(symbol: str) -> Optional[StockScan]:
    """Comprehensive stock scan - combines all analysis."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1y")
        
        if data.empty or len(data) < 100:
            return None
        
        close = data['Close']
        high = data['High']
        low = data['Low']
        
        price = close.iloc[-1]
        
        # Technical indicators
        rsi = calculate_rsi(close).iloc[-1]
        atr = calculate_atr(high, low, close).iloc[-1]
        atr_pct = atr / price * 100
        
        ema9 = calculate_ema(close, 9).iloc[-1]
        ema21 = calculate_ema(close, 21).iloc[-1]
        ema50 = calculate_ema(close, 50).iloc[-1]
        
        dist_ema21 = (price / ema21 - 1) * 100
        
        # Momentum
        momentum_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if len(close) >= 126 else 0
        momentum_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if len(close) >= 63 else 0
        
        # Volatility (annualized)
        volatility = close.pct_change().std() * np.sqrt(252) * 100
        
        # Classify regime
        if momentum_6m > 100:
            regime = Regime.PARABOLIC
        elif momentum_6m > 50:
            regime = Regime.STRONG_UP
        elif momentum_6m > 20:
            regime = Regime.MODERATE_UP
        elif momentum_6m > 0:
            regime = Regime.WEAK_UP
        elif momentum_6m > -20:
            regime = Regime.SIDEWAYS
        else:
            regime = Regime.DOWNTREND
        
        # Classify volatility
        if volatility > 80:
            vol_cat = VolCategory.ULTRA_HIGH
        elif volatility > 50:
            vol_cat = VolCategory.HIGH
        elif volatility > 30:
            vol_cat = VolCategory.MODERATE
        else:
            vol_cat = VolCategory.LOW
        
        # Get strategy from matrix
        strategy, base_size, stop_pct, tp_pct = STRATEGY_MATRIX.get(
            (regime, vol_cat), (Strategy.STAY_CASH, 0, 0, 0)
        )
        
        # Calculate entry zones
        if regime in [Regime.PARABOLIC, Regime.STRONG_UP]:
            buy_zone_high = price
            buy_zone_low = max(ema9 * 0.98, price - 1.5 * atr)
            stop_loss = price - 2.5 * atr
        elif regime in [Regime.MODERATE_UP, Regime.WEAK_UP]:
            buy_zone_high = ema21 * 1.02
            buy_zone_low = ema21 * 0.98
            stop_loss = ema50 * 0.95
        else:
            buy_zone_high = ema21 * 0.98
            buy_zone_low = ema21 * 0.95
            stop_loss = ema21 * 0.90
        
        # Targets
        target_1 = price + 1.5 * atr
        target_2 = price + 3 * atr
        
        # Expected value
        dist = estimate_distribution(data)
        ev = calculate_ev(dist['win_rate'], dist['avg_win'], dist['avg_loss'])
        kelly = calculate_kelly(dist['win_rate'], dist['avg_win'], dist['avg_loss'])
        
        # Risk/reward
        entry = (buy_zone_high + buy_zone_low) / 2
        risk = entry - stop_loss
        reward = target_1 - entry
        rr = reward / risk if risk > 0 else 0
        
        # Position sizing (vol-adjusted)
        vol_adj_size = min(base_size, 0.15 / (volatility / 100)) if volatility > 0 else base_size
        position_size = min(vol_adj_size, kelly, 0.15)
        
        # Determine action and entry signal
        # STRICTER CONDITIONS - Be cautious, preserve capital
        
        # 1. AVOID: Downtrend or negative expected value
        if regime == Regime.DOWNTREND:
            action = Action.AVOID.value
            entry_signal = "NO_ENTRY"
            reasoning = f"Downtrend ({momentum_6m:+.1f}% 6M). Wait for regime change."
        elif ev < 0:
            action = Action.AVOID.value
            entry_signal = "NEG_EV"
            reasoning = f"Negative EV ({ev:.2f}%). Risk/reward unfavorable."
        
        # 2. WAIT: Overextended (lower thresholds for caution)
        elif rsi >= 70 or dist_ema21 > 10:
            action = Action.WAIT.value
            entry_signal = "OVEREXTENDED"
            reasoning = f"Overextended (RSI {rsi:.0f}, {dist_ema21:+.1f}% from EMA21). Wait for pullback."
        
        # 3. WAIT: Below buy zone (potential falling knife - DON'T chase)
        elif price < buy_zone_low:
            action = Action.WAIT.value
            entry_signal = "BELOW_ZONE"
            reasoning = f"Below buy zone - wait for stabilization. Could be falling knife."
        
        # 4. WAIT: Sideways regime with weak metrics
        elif regime == Regime.SIDEWAYS and (ev < 0.5 or rr < 1.5):
            action = Action.WAIT.value
            entry_signal = "WEAK_SETUP"
            reasoning = f"Sideways regime with weak EV ({ev:.2f}%) or R:R ({rr:.1f}x)."
        
        # 5. In buy zone - apply strict criteria
        elif price >= buy_zone_low and price <= buy_zone_high:
            # STRONG BUY: High conviction - strong trend + good EV + good R:R + healthy RSI
            if (ev > 1.5 and rr > 2.5 and 
                momentum_6m > 10 and 
                30 <= rsi <= 65 and 
                regime in [Regime.PARABOLIC, Regime.STRONG_UP, Regime.MODERATE_UP]):
                action = Action.STRONG_BUY.value
                entry_signal = "BUY_NOW"
                reasoning = f"High conviction: EV {ev:.2f}%, R:R {rr:.1f}x, {regime.value}."
            
            # BUY: Good setup - requires meaningful EV AND R:R
            elif ev > 0.5 and rr > 1.5:
                action = Action.BUY.value
                entry_signal = "BUY_NOW"
                reasoning = f"Good setup: EV {ev:.2f}%, R:R {rr:.1f}x."
            
            # WAIT: In zone but metrics too weak
            else:
                action = Action.WAIT.value
                entry_signal = "WEAK_METRICS"
                reasoning = f"In zone but weak EV ({ev:.2f}%) or R:R ({rr:.1f}x). Need EV>0.5%, R:R>1.5x."
        
        # 6. Above buy zone - wait for pullback
        else:
            action = Action.WAIT.value
            entry_signal = "ABOVE_ZONE"
            reasoning = f"Above buy zone. Wait for pullback to ${buy_zone_high:.2f}."
        
        # Score (for ranking)
        score = 50
        score += min(20, momentum_6m / 5) if momentum_6m > 0 else max(-20, momentum_6m / 5)
        score += 10 if 30 < rsi < 70 else -5
        score += ev * 2
        score = max(0, min(100, score))
        
        return StockScan(
            symbol=symbol,
            price=price,
            regime=regime.value,
            vol_category=vol_cat.value,
            strategy=strategy.value,
            momentum_6m=momentum_6m,
            momentum_3m=momentum_3m,
            volatility=volatility,
            rsi=rsi,
            atr_pct=atr_pct,
            dist_ema21=dist_ema21,
            buy_zone_low=buy_zone_low,
            buy_zone_high=buy_zone_high,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            win_rate=dist['win_rate'] * 100,
            avg_win=dist['avg_win'],
            avg_loss=dist['avg_loss'],
            expected_value=ev,
            risk_reward=rr,
            position_size_pct=position_size * 100,
            kelly_pct=kelly * 100,
            action=action,
            entry_signal=entry_signal,
            reasoning=reasoning,
            score=score
        )
        
    except Exception as e:
        print(f"   ⚠️ Error: {symbol} - {e}")
        return None


def scan_symbols(symbols: List[str]) -> List[StockScan]:
    """Scan multiple symbols in parallel."""
    results = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scan_stock, s): s for s in symbols}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    
    results.sort(key=lambda x: x.score, reverse=True)
    return results


# ============================================================================
# OUTPUT FUNCTIONS
# ============================================================================

class OutputWriter:
    """Handles output to file or console with pretty formatting."""
    
    def __init__(self, output_file: Optional[str] = None, to_console: bool = False):
        self.lines = []
        self.output_file = output_file
        self.to_console = to_console
    
    def write(self, text: str = ""):
        """Add a line to output."""
        self.lines.append(text)
        if self.to_console:
            print(text)
    
    def save(self):
        """Save output to file."""
        if self.output_file and not self.to_console:
            os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(self.lines))
            print(f"\n✅ Scan results saved to: {self.output_file}")
    
    def get_content(self) -> str:
        """Get all content as string."""
        return '\n'.join(self.lines)


def format_header(writer: OutputWriter, title: str, scan_time: str, symbol_count: int):
    """Write report header."""
    writer.write("=" * 110)
    writer.write(f"  {title}")
    writer.write(f"  Generated: {scan_time}")
    writer.write(f"  Stocks Scanned: {symbol_count}")
    writer.write("=" * 110)


def format_quick_scan(writer: OutputWriter, results: List[StockScan]):
    """Quick summary table."""
    writer.write("")
    writer.write("=" * 110)
    writer.write("  SCAN RESULTS - QUICK VIEW")
    writer.write("=" * 110)
    writer.write("")
    writer.write(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Action':<15} {'Entry':>12}")
    writer.write("-" * 110)
    
    for r in results:
        icon = "[STRONG]" if "STRONG" in r.action else ("[BUY]" if "BUY" in r.action else ("[WAIT]" if "WAIT" in r.action else "[AVOID]"))
        writer.write(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
                     f"{r.expected_value:>+7.2f}% {icon:<15} {r.entry_signal:>12}")


def format_trading_plan(writer: OutputWriter, r: StockScan):
    """Detailed trading plan for one stock."""
    writer.write("")
    writer.write("=" * 70)
    writer.write(f"  {r.symbol} TRADING PLAN")
    writer.write("=" * 70)
    
    writer.write("")
    writer.write(f"  CURRENT STATUS")
    writer.write(f"  Price: ${r.price:.2f}")
    writer.write(f"  Regime: {r.regime}")
    writer.write(f"  Volatility: {r.volatility:.0f}%")
    writer.write(f"  RSI: {r.rsi:.0f}")
    writer.write(f"  6M Momentum: {r.momentum_6m:+.1f}%")
    writer.write(f"  Distance from EMA21: {r.dist_ema21:+.1f}%")
    
    writer.write("")
    writer.write(f"  ACTION: {r.action}")
    writer.write(f"  Reasoning: {r.reasoning}")
    
    writer.write("")
    writer.write("  ENTRY PARAMETERS")
    writer.write(f"  Buy Zone: ${r.buy_zone_low:.2f} - ${r.buy_zone_high:.2f}")
    writer.write(f"  Stop Loss: ${r.stop_loss:.2f}")
    writer.write(f"  Target 1: ${r.target_1:.2f} ({(r.target_1/r.price-1)*100:+.1f}%)")
    writer.write(f"  Target 2: ${r.target_2:.2f} ({(r.target_2/r.price-1)*100:+.1f}%)")
    
    writer.write("")
    writer.write("  EXPECTED VALUE ANALYSIS")
    writer.write(f"  Win Rate: {r.win_rate:.1f}%")
    writer.write(f"  Avg Win: +{r.avg_win:.2f}%")
    writer.write(f"  Avg Loss: -{r.avg_loss:.2f}%")
    ev_status = "POSITIVE" if r.expected_value > 0 else "NEGATIVE"
    writer.write(f"  EV: {r.expected_value:+.2f}% ({ev_status})")
    writer.write(f"  Risk/Reward: 1:{r.risk_reward:.1f}")
    
    writer.write("")
    writer.write("  POSITION SIZING")
    writer.write(f"  Recommended Size: {r.position_size_pct:.1f}% of portfolio")
    writer.write(f"  Kelly Criterion: {r.kelly_pct:.1f}%")
    writer.write(f"  Strategy: {r.strategy}")


def format_summary(writer: OutputWriter, results: List[StockScan]):
    """Summary with grouping by action."""
    buys = [r for r in results if "BUY" in r.action]
    waits = [r for r in results if "WAIT" in r.action]
    avoids = [r for r in results if "AVOID" in r.action]
    
    writer.write("")
    writer.write("=" * 110)
    writer.write(f"  SUMMARY: {len(buys)} BUY | {len(waits)} WAIT | {len(avoids)} AVOID")
    writer.write("=" * 110)
    
    if buys:
        writer.write("")
        writer.write(f"  ACTIONABLE OPPORTUNITIES ({len(buys)})")
        writer.write("-" * 90)
        writer.write(f"  {'Symbol':<8} {'Price':>10} {'Buy Zone':>22} {'Stop':>10} {'EV':>8} {'Size':>6}")
        writer.write("-" * 90)
        for r in buys:
            writer.write(f"  {r.symbol:<8} ${r.price:>9.2f} ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} "
                        f"${r.stop_loss:>9.2f} {r.expected_value:>+7.2f}% {r.position_size_pct:>5.1f}%")
    
    if waits:
        writer.write("")
        writer.write(f"  WAIT LIST ({len(waits)})")
        writer.write("-" * 90)
        for r in waits:
            writer.write(f"  {r.symbol:<8} - {r.reasoning}")
    
    if avoids:
        writer.write("")
        writer.write(f"  AVOID ({len(avoids)})")
        writer.write("-" * 90)
        for r in avoids:
            writer.write(f"  {r.symbol:<8} - {r.reasoning}")


def format_top_picks(writer: OutputWriter, results: List[StockScan]):
    """Format top picks section."""
    buys = [r for r in results if "BUY" in r.action]
    strong_buys = [r for r in buys if "STRONG" in r.action]
    
    writer.write("")
    writer.write("=" * 110)
    writer.write("  TOP PICKS")
    writer.write("=" * 110)
    
    if strong_buys:
        writer.write("")
        writer.write("  STRONG BUY (High Conviction)")
        for r in strong_buys[:5]:
            writer.write(f"  -> {r.symbol:<6} @ ${r.price:.2f} | EV: {r.expected_value:+.2f}% | R:R 1:{r.risk_reward:.1f}")
            writer.write(f"     Buy Zone: ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} | Stop: ${r.stop_loss:.2f}")
    
    regular_buys = [r for r in buys if "STRONG" not in r.action]
    if regular_buys:
        writer.write("")
        writer.write("  BUY (Good Setups)")
        for r in regular_buys[:5]:
            writer.write(f"  -> {r.symbol:<6} @ ${r.price:.2f} | EV: {r.expected_value:+.2f}% | R:R 1:{r.risk_reward:.1f}")


def format_regime_breakdown(writer: OutputWriter, results: List[StockScan]):
    """Format regime breakdown section."""
    writer.write("")
    writer.write("=" * 110)
    writer.write("  MARKET REGIME BREAKDOWN")
    writer.write("=" * 110)
    
    regime_counts = {}
    for r in results:
        regime_counts[r.regime] = regime_counts.get(r.regime, 0) + 1
    
    writer.write("")
    for regime, count in sorted(regime_counts.items(), key=lambda x: -x[1]):
        pct = count / len(results) * 100
        bar = "█" * int(pct / 2)
        writer.write(f"  {regime:<15} {count:>3} ({pct:>5.1f}%) {bar}")


# Legacy print functions for backwards compatibility
def print_quick_scan(results: List[StockScan]):
    """Quick summary table (console output)."""
    print(f"\n{'='*110}")
    print(f"{'Symbol':<8} {'Price':>10} {'Regime':<12} {'Vol':>6} {'RSI':>5} {'EV':>8} {'Action':<15} {'Entry':>12}")
    print("-"*110)
    
    for r in results:
        icon = "🟢" if "STRONG" in r.action else ("🟡" if "BUY" in r.action else ("⚪" if "WAIT" in r.action else "🔴"))
        print(f"{r.symbol:<8} ${r.price:>9.2f} {r.regime:<12} {r.volatility:>5.0f}% {r.rsi:>4.0f} "
              f"{r.expected_value:>+7.2f}% {icon} {r.action.split()[-1]:<12} {r.entry_signal:>12}")


def print_trading_plan(r: StockScan):
    """Detailed trading plan for one stock (console output)."""
    print(f"\n{'='*70}")
    print(f"📊 {r.symbol} TRADING PLAN")
    print(f"{'='*70}")
    
    print(f"\n💰 Price: ${r.price:.2f} | Regime: {r.regime} | Vol: {r.volatility:.0f}%")
    print(f"📊 RSI: {r.rsi:.0f} | 6M: {r.momentum_6m:+.1f}% | dEMA21: {r.dist_ema21:+.1f}%")
    
    print(f"\n🎯 ACTION: {r.action}")
    print(f"   {r.reasoning}")
    
    print(f"\n📍 ENTRY:")
    print(f"   Buy Zone: ${r.buy_zone_low:.2f} - ${r.buy_zone_high:.2f}")
    print(f"   Stop Loss: ${r.stop_loss:.2f}")
    print(f"   Target 1: ${r.target_1:.2f} ({(r.target_1/r.price-1)*100:+.1f}%)")
    print(f"   Target 2: ${r.target_2:.2f} ({(r.target_2/r.price-1)*100:+.1f}%)")
    
    print(f"\n📊 EXPECTED VALUE:")
    print(f"   Win Rate: {r.win_rate:.1f}%")
    print(f"   Avg Win: +{r.avg_win:.2f}% | Avg Loss: -{r.avg_loss:.2f}%")
    ev_icon = "✅" if r.expected_value > 0 else "❌"
    print(f"   EV: {r.expected_value:+.2f}% {ev_icon}")
    print(f"   R:R: 1:{r.risk_reward:.1f}")
    
    print(f"\n💼 POSITION:")
    print(f"   Recommended: {r.position_size_pct:.1f}% of portfolio")
    print(f"   Strategy: {r.strategy}")


def print_summary(results: List[StockScan]):
    """Summary with grouping by action (console output)."""
    buys = [r for r in results if "BUY" in r.action]
    waits = [r for r in results if "WAIT" in r.action]
    avoids = [r for r in results if "AVOID" in r.action]
    
    print(f"\n{'='*90}")
    print(f"📋 SUMMARY: {len(buys)} BUY | {len(waits)} WAIT | {len(avoids)} AVOID")
    print(f"{'='*90}")
    
    if buys:
        print(f"\n🟢 ACTIONABLE ({len(buys)}):")
        print(f"{'Symbol':<8} {'Price':>10} {'Buy Zone':>22} {'Stop':>10} {'EV':>8} {'Size':>6}")
        print("-"*70)
        for r in buys[:10]:
            print(f"{r.symbol:<8} ${r.price:>9.2f} ${r.buy_zone_low:.2f}-${r.buy_zone_high:.2f} "
                  f"${r.stop_loss:>9.2f} {r.expected_value:>+7.2f}% {r.position_size_pct:>5.1f}%")
    
    if waits:
        print(f"\n⚪ WAIT ({len(waits)}):")
        for r in waits[:5]:
            print(f"   {r.symbol:<6} - {r.reasoning}")
    
    if avoids:
        print(f"\n🔴 AVOID ({len(avoids)}):")
        for r in avoids[:5]:
            print(f"   {r.symbol:<6} - {r.reasoning}")


# ============================================================================
# MAIN
# ============================================================================

def generate_output_filename() -> str:
    """Generate default output filename with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(os.path.dirname(__file__), '..', 'results')
    return os.path.join(results_dir, f"scan_{timestamp}.txt")


def run_scan(
    symbols: List[str],
    output_file: Optional[str] = None,
    to_console: bool = False,
    plan_mode: bool = False,
    quick_mode: bool = False,
    theme_name: Optional[str] = None
) -> str:
    """
    Run scan and output to file or console.
    Returns the output file path.
    """
    # Default output file
    if output_file is None and not to_console:
        output_file = generate_output_filename()
    
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"\n🔍 Scanning {len(symbols)} stocks...")
    results = scan_symbols(symbols)
    
    if not results:
        print("❌ No results found!")
        return ""
    
    # Create output writer
    writer = OutputWriter(output_file, to_console)
    
    # Write header
    title = "STOCK SCANNER REPORT"
    if theme_name:
        title += f" - {theme_name.upper()}"
    format_header(writer, title, scan_time, len(symbols))
    
    if plan_mode:
        # Detailed trading plans
        for r in results:
            format_trading_plan(writer, r)
    elif quick_mode:
        # Quick scan only
        format_quick_scan(writer, results)
    else:
        # Full report
        format_quick_scan(writer, results)
        format_top_picks(writer, results)
        format_summary(writer, results)
        format_regime_breakdown(writer, results)
    
    # Footer
    writer.write("")
    writer.write("=" * 110)
    writer.write(f"  END OF REPORT - {len(results)} stocks analyzed")
    writer.write("=" * 110)
    
    # Save to file
    writer.save()
    
    return output_file if output_file else ""


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Unified Stock Scanner")
    parser.add_argument("symbols", nargs="*", help="Symbols to scan")
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument("--plan", nargs="*", help="Detailed plan for symbols")
    parser.add_argument("--quick", action="store_true", help="Quick output")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    parser.add_argument("--console", "-c", action="store_true", help="Print to console instead of file")
    
    args = parser.parse_args()
    
    universe = load_universe()
    theme_name = None
    
    # Determine symbols
    if args.plan:
        symbols = [s.upper() for s in args.plan]
        plan_mode = True
    else:
        plan_mode = False
        if args.symbols:
            symbols = [s.upper() for s in args.symbols]
        elif args.theme:
            if args.theme in universe['themes']:
                symbols = universe['themes'][args.theme]['symbols']
                theme_name = universe['themes'][args.theme].get('name', args.theme)
            else:
                print(f"❌ Theme '{args.theme}' not found!")
                print(f"   Available themes: {', '.join(universe['themes'].keys())}")
                return
        elif args.all:
            symbols = universe.get('all_symbols', [])
            theme_name = "FULL UNIVERSE"
        else:
            # Default: high conviction
            symbols = universe.get('high_conviction', ['NVDA', 'GOOGL', 'MSFT', 'PLTR', 'CRWD'])
            theme_name = "HIGH CONVICTION"
    
    # Run scan
    output_file = run_scan(
        symbols=symbols,
        output_file=args.output,
        to_console=args.console,
        plan_mode=plan_mode,
        quick_mode=args.quick,
        theme_name=theme_name
    )
    
    # If output to file, show preview
    if output_file and not args.console:
        print(f"\n📄 Preview of top results:")
        buys = []
        with open(output_file, 'r') as f:
            content = f.read()
            # Show first few actionable items
            lines = content.split('\n')
            in_actionable = False
            count = 0
            for line in lines:
                if 'ACTIONABLE' in line:
                    in_actionable = True
                if in_actionable and count < 8:
                    print(line)
                    count += 1


if __name__ == "__main__":
    main()
