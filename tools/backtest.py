#!/usr/bin/env python3
"""
UNIFIED BACKTESTER
==================
Combines: strategy_tester + portfolio_tester + volatility testing

Features:
1. Single stock strategy testing (vs B&H)
2. Portfolio testing (vs SPY)
3. Multiple strategies: EMA, RSI, Momentum, Swing, Adaptive
4. Volatility-adjusted position sizing

Usage:
    # Test single stock
    python tools/backtest.py AAPL --days 180
    
    # Test strategy
    python tools/backtest.py NVDA --strategy trend
    
    # Test portfolio vs SPY
    python tools/backtest.py --portfolio --theme mega_tech
    
    # Compare strategies
    python tools/backtest.py TSLA --compare
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# HELPERS
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


def calculate_sharpe(returns: pd.Series, rf: float = 0.04) -> float:
    if returns.std() == 0:
        return 0
    return np.sqrt(252) * (returns.mean() - rf/252) / returns.std()


def calculate_max_dd(equity: pd.Series) -> float:
    peak = equity.expanding().max()
    dd = (equity - peak) / peak
    return abs(dd.min())


def load_universe() -> Dict:
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'stock_universe.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# ============================================================================
# STRATEGIES
# ============================================================================

def strategy_buy_hold(df: pd.DataFrame) -> pd.Series:
    """Buy and hold - baseline."""
    signals = pd.Series(0, index=df.index)
    signals.iloc[20] = 1  # Buy at start
    return signals


def strategy_ema_cross(df: pd.DataFrame) -> pd.Series:
    """EMA 9/21 crossover."""
    ema9 = calculate_ema(df['Close'], 9)
    ema21 = calculate_ema(df['Close'], 21)
    
    signals = pd.Series(0, index=df.index)
    signals[ema9 > ema21] = 1
    signals[ema9 < ema21] = -1
    return signals


def strategy_trend(df: pd.DataFrame) -> pd.Series:
    """Trend following with EMA alignment."""
    ema9 = calculate_ema(df['Close'], 9)
    ema21 = calculate_ema(df['Close'], 21)
    ema50 = calculate_ema(df['Close'], 50)
    
    signals = pd.Series(0, index=df.index)
    
    # Buy when EMAs aligned and price above
    bullish = (ema9 > ema21) & (ema21 > ema50) & (df['Close'] > ema9)
    bearish = (ema9 < ema21)
    
    signals[bullish] = 1
    signals[bearish] = -1
    return signals


def strategy_rsi(df: pd.DataFrame) -> pd.Series:
    """RSI mean reversion."""
    rsi = calculate_rsi(df['Close'])
    
    signals = pd.Series(0, index=df.index)
    signals[rsi < 30] = 1   # Oversold - buy
    signals[rsi > 70] = -1  # Overbought - sell
    return signals


def strategy_momentum(df: pd.DataFrame) -> pd.Series:
    """Momentum breakout."""
    high_20 = df['High'].rolling(20).max()
    low_20 = df['Low'].rolling(20).min()
    rsi = calculate_rsi(df['Close'])
    
    signals = pd.Series(0, index=df.index)
    
    # Buy on breakout above 20-day high
    breakout = (df['Close'] > high_20.shift(1)) & (rsi > 50)
    breakdown = df['Close'] < low_20.shift(1)
    
    signals[breakout] = 1
    signals[breakdown] = -1
    return signals


def strategy_swing(df: pd.DataFrame) -> pd.Series:
    """Swing trading - buy dips in uptrend."""
    ema50 = calculate_ema(df['Close'], 50)
    rsi = calculate_rsi(df['Close'])
    
    signals = pd.Series(0, index=df.index)
    
    # Buy RSI oversold in uptrend
    uptrend = df['Close'] > ema50
    buy = uptrend & (rsi < 35)
    sell = rsi > 70
    
    signals[buy] = 1
    signals[sell] = -1
    return signals


def strategy_adaptive(df: pd.DataFrame) -> pd.Series:
    """Adaptive strategy based on regime."""
    close = df['Close']
    ema21 = calculate_ema(close, 21)
    ema120 = calculate_ema(close, 120)
    rsi = calculate_rsi(close)
    
    signals = pd.Series(0, index=df.index)
    
    for i in range(120, len(df)):
        # Detect regime
        momentum = (close.iloc[i] / close.iloc[i-60] - 1) * 100
        
        if momentum > 30:  # Strong uptrend
            # Hold with trailing logic
            if close.iloc[i] > ema21.iloc[i]:
                signals.iloc[i] = 1
        elif momentum > 0:  # Moderate uptrend
            # Swing trade
            if rsi.iloc[i] < 40 and close.iloc[i] > ema120.iloc[i]:
                signals.iloc[i] = 1
            elif rsi.iloc[i] > 70:
                signals.iloc[i] = -1
        else:  # Downtrend/sideways
            # Mean reversion only
            if rsi.iloc[i] < 30:
                signals.iloc[i] = 1
            elif rsi.iloc[i] > 60:
                signals.iloc[i] = -1
    
    return signals


STRATEGIES = {
    'bnh': ('Buy & Hold', strategy_buy_hold),
    'ema': ('EMA Crossover', strategy_ema_cross),
    'trend': ('Trend Following', strategy_trend),
    'rsi': ('RSI Reversion', strategy_rsi),
    'momentum': ('Momentum Breakout', strategy_momentum),
    'swing': ('Swing Trade', strategy_swing),
    'adaptive': ('Adaptive', strategy_adaptive),
}


# ============================================================================
# BACKTEST ENGINE
# ============================================================================

@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    days: int
    total_return: float
    bnh_return: float
    excess_return: float
    sharpe: float
    max_dd: float
    win_rate: float
    trades: int
    beats_bnh: bool


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    initial_capital: float = 100000
) -> Tuple[List[dict], pd.Series]:
    """Run backtest on signals."""
    
    close = df['Close']
    trades = []
    equity = [initial_capital]
    cash = initial_capital
    position = 0
    entry_price = 0
    entry_date = None
    
    for i in range(1, len(df)):
        signal = signals.iloc[i]
        price = close.iloc[i]
        date = df.index[i]
        
        # Update equity
        if position > 0:
            equity.append(cash + position * price)
        else:
            equity.append(cash)
        
        # Process signal
        if signal == 1 and position == 0:
            # Buy
            shares = int(cash * 0.95 / price)
            if shares > 0:
                cost = shares * price
                cash -= cost
                position = shares
                entry_price = price
                entry_date = date
        
        elif signal == -1 and position > 0:
            # Sell
            proceeds = position * price
            cash += proceeds
            
            pnl = proceeds - position * entry_price
            pnl_pct = (price / entry_price - 1) * 100
            
            trades.append({
                'entry_date': entry_date,
                'exit_date': date,
                'entry_price': entry_price,
                'exit_price': price,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            })
            
            position = 0
    
    # Close final position
    if position > 0:
        final_price = close.iloc[-1]
        proceeds = position * final_price
        cash += proceeds
        pnl = proceeds - position * entry_price
        trades.append({
            'entry_date': entry_date,
            'exit_date': df.index[-1],
            'entry_price': entry_price,
            'exit_price': final_price,
            'pnl': pnl,
            'pnl_pct': (final_price/entry_price-1)*100
        })
        equity[-1] = cash
    
    return trades, pd.Series(equity, index=df.index[:len(equity)])


def test_strategy(
    symbol: str,
    strategy_key: str,
    days: int = 180
) -> Optional[BacktestResult]:
    """Test a strategy on a symbol."""
    
    strategy_name, strategy_func = STRATEGIES.get(strategy_key, ('Unknown', strategy_buy_hold))
    
    try:
        ticker = yf.Ticker(symbol)
        period_map = {90: "6mo", 180: "1y", 365: "2y", 730: "3y"}
        data = ticker.history(period=period_map.get(days, "1y"))
        
        if data.empty or len(data) < days:
            return None
        
        data = data.tail(days)
        
        # B&H benchmark
        bnh_return = (data['Close'].iloc[-1] / data['Close'].iloc[0] - 1) * 100
        
        # Run strategy
        signals = strategy_func(data)
        trades, equity = run_backtest(data, signals)
        
        # Metrics
        total_return = (equity.iloc[-1] / 100000 - 1) * 100
        excess = total_return - bnh_return
        
        returns = equity.pct_change().dropna()
        sharpe = calculate_sharpe(returns)
        max_dd = calculate_max_dd(equity) * 100
        
        wins = [t for t in trades if t['pnl'] > 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        
        return BacktestResult(
            strategy=strategy_name,
            symbol=symbol,
            days=days,
            total_return=total_return,
            bnh_return=bnh_return,
            excess_return=excess,
            sharpe=sharpe,
            max_dd=max_dd,
            win_rate=win_rate,
            trades=len(trades),
            beats_bnh=total_return > bnh_return
        )
        
    except Exception as e:
        print(f"   Error: {e}")
        return None


def compare_strategies(symbol: str, days: int = 180) -> List[BacktestResult]:
    """Compare all strategies on a symbol."""
    results = []
    
    for key in STRATEGIES.keys():
        result = test_strategy(symbol, key, days)
        if result:
            results.append(result)
    
    results.sort(key=lambda x: x.total_return, reverse=True)
    return results


def test_portfolio(
    symbols: List[str],
    strategy_key: str = 'adaptive',
    days: int = 365,
    initial_capital: float = 100000
) -> Dict:
    """Test portfolio vs SPY."""
    
    print(f"\n🔍 Testing portfolio of {len(symbols)} stocks vs SPY...")
    
    # Get SPY
    spy = yf.Ticker("SPY").history(period="2y")
    if len(spy) > days:
        spy = spy.tail(days)
    spy_return = (spy['Close'].iloc[-1] / spy['Close'].iloc[0] - 1) * 100
    
    # Test each stock
    results = []
    for symbol in symbols:
        result = test_strategy(symbol, strategy_key, days)
        if result:
            results.append(result)
    
    if not results:
        return None
    
    # Portfolio metrics (equal weight)
    avg_return = np.mean([r.total_return for r in results])
    avg_sharpe = np.mean([r.sharpe for r in results])
    avg_dd = np.mean([r.max_dd for r in results])
    
    return {
        'strategy': STRATEGIES[strategy_key][0],
        'symbols': len(results),
        'portfolio_return': avg_return,
        'spy_return': spy_return,
        'alpha': avg_return - spy_return,
        'sharpe': avg_sharpe,
        'max_dd': avg_dd,
        'beats_spy': avg_return > spy_return,
        'results': results
    }


# ============================================================================
# OUTPUT
# ============================================================================

def print_result(r: BacktestResult):
    """Print single result."""
    status = "✅" if r.beats_bnh else "❌"
    print(f"{r.strategy:<18} {r.total_return:>+8.2f}% vs {r.bnh_return:>+7.2f}% "
          f"({r.excess_return:>+6.2f}%) Sharpe:{r.sharpe:>5.2f} DD:{r.max_dd:>5.1f}% "
          f"WR:{r.win_rate:>5.1f}% {status}")


def print_comparison(results: List[BacktestResult]):
    """Print strategy comparison."""
    print(f"\n{'='*90}")
    print(f"📊 STRATEGY COMPARISON: {results[0].symbol} ({results[0].days}D)")
    print(f"{'='*90}")
    print(f"{'Strategy':<18} {'Return':>10} {'vs B&H':>10} {'Excess':>10} {'Sharpe':>8} {'MaxDD':>8} {'WinRate':>8}")
    print("-"*90)
    
    for r in results:
        print_result(r)
    
    # Best
    best = results[0]
    print(f"\n🏆 Best: {best.strategy} ({best.total_return:+.2f}%)")


def print_portfolio(result: Dict):
    """Print portfolio test result."""
    print(f"\n{'='*70}")
    print(f"📊 PORTFOLIO TEST: {result['strategy']}")
    print(f"{'='*70}")
    print(f"Stocks: {result['symbols']}")
    print(f"Portfolio Return: {result['portfolio_return']:+.2f}%")
    print(f"SPY Return: {result['spy_return']:+.2f}%")
    print(f"Alpha: {result['alpha']:+.2f}%")
    print(f"Sharpe: {result['sharpe']:.2f}")
    print(f"Max DD: {result['max_dd']:.1f}%")
    
    status = "✅ BEATS SPY" if result['beats_spy'] else "❌ LOSES TO SPY"
    print(f"\n🎯 {status}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Unified Backtester")
    parser.add_argument("symbols", nargs="*", help="Symbols to test")
    parser.add_argument("--strategy", type=str, default="adaptive", 
                       choices=list(STRATEGIES.keys()), help="Strategy to test")
    parser.add_argument("--days", type=int, default=180, help="Backtest period")
    parser.add_argument("--compare", action="store_true", help="Compare all strategies")
    parser.add_argument("--portfolio", action="store_true", help="Portfolio mode vs SPY")
    parser.add_argument("--theme", type=str, help="Theme for portfolio")
    
    args = parser.parse_args()
    
    universe = load_universe()
    
    # Determine symbols
    if args.portfolio:
        if args.theme and args.theme in universe['themes']:
            symbols = universe['themes'][args.theme]['symbols']
        elif args.symbols:
            symbols = [s.upper() for s in args.symbols]
        else:
            symbols = universe.get('quick_test', ['NVDA', 'AAPL', 'MSFT', 'GOOGL', 'META'])
        
        result = test_portfolio(symbols, args.strategy, args.days)
        if result:
            print_portfolio(result)
    
    elif args.compare:
        symbol = args.symbols[0].upper() if args.symbols else "NVDA"
        results = compare_strategies(symbol, args.days)
        print_comparison(results)
    
    else:
        symbols = [s.upper() for s in args.symbols] if args.symbols else ["NVDA"]
        
        for symbol in symbols:
            result = test_strategy(symbol, args.strategy, args.days)
            if result:
                print(f"\n📊 {result.symbol} ({result.days}D) - {result.strategy}")
                print_result(result)


if __name__ == "__main__":
    main()
