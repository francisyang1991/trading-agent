#!/usr/bin/env python3
"""
SHORT Strategy Backtest
=======================
Tests the three SHORT signal variants on historical data:
  1. SHORT_POPUP      — Downtrend + RSI>55 + near EMA resistance
  2. SHORT_OVERBOUGHT — Downtrend + RSI>70 (wide rule — under review)
  3. SHORT_MEAN_REVERT — Sideways + RSI>65 + >3% above EMA21

Produces per-signal-type + combined results with:
  - Win rate, avg PnL, profit factor, max drawdown
  - Trade-by-trade log (CSV)
  - Summary report (markdown)

Usage:
    python tools/short_strategy_backtest.py
    python tools/short_strategy_backtest.py --years 3
    python tools/short_strategy_backtest.py --signal SHORT_POPUP
    python tools/short_strategy_backtest.py --tickers INTC BA NKE PFE PYPL
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

# ─── Test Universe ────────────────────────────────────────────────────
# Diverse mix: mega-cap, growth, value, beaten-down, sector rotation
DEFAULT_UNIVERSE = [
    # Tech / former high-flyers (likely downtrends at times)
    "INTC", "BA", "NKE", "PFE", "PYPL",
    "PARA", "SNAP", "RIVN", "LCID", "MRNA",
    "ZM", "DOCU", "HOOD", "SOFI", "PATH",
    "COIN", "ROKU", "PINS", "CHWY", "PLUG",
    # Mid-cap / volatile
    "MARA", "RIOT", "AFRM", "UPST", "DKNG",
    "SMCI", "ARM", "IONQ", "RGTI", "RKLB",
    # Blue chips (for sideways regime testing)
    "AAPL", "MSFT", "AMZN", "GOOGL", "META",
    "NVDA", "TSLA", "JPM", "V", "UNH",
    # Sector rotation / cyclicals
    "XOM", "CVX", "FCX", "FSLR", "ENPH",
    "SQ", "SHOP", "CRM", "ABNB", "UBER",
]


# ─── Technical Helpers ────────────────────────────────────────────────

def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def classify_regime(momentum_6m: float) -> str:
    if momentum_6m > 100:
        return "PARABOLIC"
    elif momentum_6m > 50:
        return "STRONG_UP"
    elif momentum_6m > 20:
        return "MODERATE_UP"
    elif momentum_6m > 0:
        return "WEAK_UP"
    elif momentum_6m > -20:
        return "SIDEWAYS"
    else:
        return "DOWNTREND"


def classify_vol(volatility: float) -> str:
    if volatility > 80:
        return "ULTRA_HIGH"
    elif volatility > 50:
        return "HIGH"
    elif volatility > 30:
        return "MODERATE"
    else:
        return "LOW"


# ─── Trade Data ───────────────────────────────────────────────────────

@dataclass
class ShortTrade:
    symbol: str
    signal_type: str        # SHORT_POPUP | SHORT_OVERBOUGHT | SHORT_MEAN_REVERT
    entry_date: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    holding_days: int = 0
    regime: str = ""
    rsi_at_entry: float = 0.0
    dist_ema21_at_entry: float = 0.0
    momentum_6m_at_entry: float = 0.0


# ─── Core Backtest Engine ─────────────────────────────────────────────

class ShortStrategyBacktest:
    """
    Day-by-day simulation of SHORT signals.

    For each bar:
    1. Check open positions for stop/target hits
    2. Check for new SHORT entry signals
    3. Record metrics
    """

    MAX_HOLD_DAYS = 30          # Force close after 30 days
    MAX_CONCURRENT = 5          # Max concurrent shorts
    POSITION_SIZE = 0.10        # 10% of capital per trade
    INITIAL_CAPITAL = 100_000

    # Configurable: which signal types to test
    SIGNAL_TYPES = {"SHORT_POPUP", "SHORT_OVERBOUGHT", "SHORT_MEAN_REVERT"}

    def __init__(self, signal_filter: Optional[str] = None):
        if signal_filter:
            self.SIGNAL_TYPES = {signal_filter}
        self.trades: List[ShortTrade] = []
        self.open_positions: Dict[str, ShortTrade] = {}  # symbol -> trade

    def run(self, universe: List[str], years: int = 2) -> List[ShortTrade]:
        """Run backtest over historical data."""
        print(f"\n{'='*70}")
        print(f"SHORT Strategy Backtest")
        print(f"Signals: {', '.join(sorted(self.SIGNAL_TYPES))}")
        print(f"Universe: {len(universe)} stocks, {years}y lookback")
        print(f"{'='*70}\n")

        # Download data
        print("Downloading historical data...")
        data = self._download_data(universe, years)
        print(f"  Got data for {len(data)} / {len(universe)} stocks\n")

        if not data:
            print("ERROR: No data downloaded.")
            return []

        # Get common date range
        all_dates = set()
        for df in data.values():
            all_dates.update(df.index)
        all_dates = sorted(all_dates)

        # Need at least 126 days (6 months) of warmup
        if len(all_dates) < 130:
            print("ERROR: Not enough history.")
            return []

        trade_dates = all_dates[126:]  # Skip first 6 months for indicators
        print(f"Trading period: {trade_dates[0].date()} to {trade_dates[-1].date()}")
        print(f"Scanning {len(trade_dates)} trading days...\n")

        # Pre-compute indicators for all stocks
        print("Computing indicators...")
        indicators = {}
        for symbol, df in data.items():
            indicators[symbol] = self._compute_indicators(df)

        # Day-by-day simulation
        signals_found = 0
        for i, date in enumerate(trade_dates):
            # 1) Check open positions
            self._check_exits(date, data)

            # 2) Scan for new SHORT entries
            if len(self.open_positions) < self.MAX_CONCURRENT:
                for symbol in universe:
                    if symbol in self.open_positions:
                        continue
                    if symbol not in indicators:
                        continue

                    ind = indicators[symbol]
                    if date not in ind.index:
                        continue

                    row = ind.loc[date]
                    signal = self._check_short_signal(symbol, row, date, data[symbol])
                    if signal:
                        self.open_positions[symbol] = signal
                        signals_found += 1

                        if len(self.open_positions) >= self.MAX_CONCURRENT:
                            break

            # Progress
            if (i + 1) % 50 == 0:
                print(f"  Day {i+1}/{len(trade_dates)}: "
                      f"{signals_found} signals, {len(self.open_positions)} open, "
                      f"{len(self.trades)} closed")

        # Force-close remaining positions at end
        last_date = trade_dates[-1]
        for symbol in list(self.open_positions.keys()):
            self._force_close(symbol, last_date, data, "END_OF_BACKTEST")

        print(f"\nDone. Total trades: {len(self.trades)}")
        return self.trades

    def _download_data(self, universe: List[str], years: int) -> Dict[str, pd.DataFrame]:
        """Download OHLCV data for all symbols."""
        data = {}
        end = datetime.now()
        start = end - timedelta(days=years * 365 + 180)  # Extra 6M for warmup

        for symbol in universe:
            try:
                df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
                if df is not None and len(df) > 130:
                    # Flatten MultiIndex columns if present
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    # Normalize column names to lowercase
                    df.columns = [c.lower() for c in df.columns]
                    data[symbol] = df
            except Exception as e:
                print(f"  Warning: Failed to download {symbol}: {e}")

        return data

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pre-compute all needed indicators for a stock."""
        close = df['close']
        high = df['high']
        low = df['low']

        ind = pd.DataFrame(index=df.index)
        ind['close'] = close
        ind['high'] = high
        ind['low'] = low
        ind['rsi'] = calculate_rsi(close)
        ind['ema9'] = calculate_ema(close, 9)
        ind['ema21'] = calculate_ema(close, 21)
        ind['ema50'] = calculate_ema(close, 50)
        ind['atr'] = calculate_atr(high, low, close)

        # Momentum (6M and 3M)
        ind['mom_6m'] = (close / close.shift(126) - 1) * 100
        ind['mom_3m'] = (close / close.shift(63) - 1) * 100

        # Distance from EMA21 (%)
        ind['dist_ema21'] = (close / ind['ema21'] - 1) * 100
        ind['dist_ema50'] = (close / ind['ema50'] - 1) * 100

        # Annualized volatility
        ind['volatility'] = close.pct_change().rolling(60).std() * np.sqrt(252) * 100

        return ind.dropna()

    def _check_short_signal(
        self, symbol: str, row: pd.Series, date, df: pd.DataFrame
    ) -> Optional[ShortTrade]:
        """Check if a SHORT signal fires on this date for this symbol."""
        price = row['close']
        rsi = row['rsi']
        ema21 = row['ema21']
        ema50 = row['ema50']
        atr = row['atr']
        mom_6m = row['mom_6m']
        dist_ema21 = row['dist_ema21']
        volatility = row['volatility']

        regime = classify_regime(mom_6m)
        vol_cat = classify_vol(volatility)

        # Guard: No shorts in ultra-high volatility
        if vol_cat == "ULTRA_HIGH":
            return None

        # Guard: ATR must be positive
        if atr <= 0 or np.isnan(atr):
            return None

        signal_type = None
        stop_loss = 0.0
        target_1 = 0.0
        target_2 = 0.0

        # ── Signal 1: SHORT_POPUP (Downtrend + RSI>55 + near EMA) ──
        if regime == "DOWNTREND" and "SHORT_POPUP" in self.SIGNAL_TYPES:
            is_near_resistance = (
                abs(dist_ema21) < 3.0 or
                (ema50 > 0 and abs((price - ema50) / ema50 * 100) < 4.0)
            )
            if rsi > 55 and is_near_resistance:
                signal_type = "SHORT_POPUP"
                stop_loss = price + 2.5 * atr
                target_1 = price - 1.5 * atr
                target_2 = price - 3.0 * atr

        # ── Signal 2: SHORT_OVERBOUGHT (Downtrend + RSI>70) ──
        if signal_type is None and regime == "DOWNTREND" and "SHORT_OVERBOUGHT" in self.SIGNAL_TYPES:
            if rsi > 70:
                signal_type = "SHORT_OVERBOUGHT"
                stop_loss = price + 2.0 * atr
                target_1 = price - 2.0 * atr
                target_2 = price - 4.0 * atr

        # ── Signal 3: SHORT_MEAN_REVERT (Sideways + RSI>65 + >3% above EMA21) ──
        if signal_type is None and regime == "SIDEWAYS" and "SHORT_MEAN_REVERT" in self.SIGNAL_TYPES:
            if rsi > 65 and dist_ema21 > 3.0:
                signal_type = "SHORT_MEAN_REVERT"
                stop_loss = price + 2.0 * atr
                target_1 = ema21           # Revert to mean
                target_2 = price - 2.5 * atr

        if signal_type is None:
            return None

        return ShortTrade(
            symbol=symbol,
            signal_type=signal_type,
            entry_date=str(date.date()) if hasattr(date, 'date') else str(date)[:10],
            entry_price=price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            regime=regime,
            rsi_at_entry=rsi,
            dist_ema21_at_entry=dist_ema21,
            momentum_6m_at_entry=mom_6m,
        )

    def _check_exits(self, date, data: Dict[str, pd.DataFrame]):
        """Check stop-loss, targets, and time exits for open positions."""
        to_close = []

        for symbol, trade in self.open_positions.items():
            if symbol not in data or date not in data[symbol].index:
                continue

            bar = data[symbol].loc[date]
            high = bar['high']
            low = bar['low']
            close = bar['close']
            entry_date = pd.Timestamp(trade.entry_date)
            holding = (date - entry_date).days

            # For SHORT: stop is triggered when price goes UP (high > stop)
            #            target is hit when price goes DOWN (low < target)

            exit_reason = None
            exit_price = 0.0

            # Check stop loss (price went too high)
            if high >= trade.stop_loss:
                exit_reason = "STOP_LOSS"
                exit_price = trade.stop_loss  # Assume filled at stop

            # Check target 1 (price dropped to target)
            elif low <= trade.target_1:
                exit_reason = "TARGET_1"
                exit_price = trade.target_1

            # Check target 2 (deeper target)
            elif low <= trade.target_2:
                exit_reason = "TARGET_2"
                exit_price = trade.target_2

            # Time-based exit
            elif holding >= self.MAX_HOLD_DAYS:
                exit_reason = "TIME_EXIT"
                exit_price = close

            if exit_reason:
                trade.exit_date = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
                trade.exit_price = exit_price
                trade.exit_reason = exit_reason
                trade.holding_days = holding
                # SHORT P&L: profit when price drops
                trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100
                to_close.append(symbol)

        for symbol in to_close:
            trade = self.open_positions.pop(symbol)
            self.trades.append(trade)

    def _force_close(self, symbol: str, date, data: Dict[str, pd.DataFrame], reason: str):
        """Force-close a position at market close."""
        trade = self.open_positions.pop(symbol, None)
        if trade is None:
            return

        if symbol in data and date in data[symbol].index:
            close = data[symbol].loc[date]['close']
        else:
            close = trade.entry_price  # Fallback

        entry_date = pd.Timestamp(trade.entry_date)
        trade.exit_date = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
        trade.exit_price = close
        trade.exit_reason = reason
        trade.holding_days = (date - entry_date).days
        trade.pnl_pct = (trade.entry_price - close) / trade.entry_price * 100
        self.trades.append(trade)


# ─── Results Analysis ─────────────────────────────────────────────────

def analyze_trades(trades: List[ShortTrade], label: str = "ALL") -> Dict:
    """Analyze a list of trades and return stats."""
    if not trades:
        return {"label": label, "total": 0}

    pnls = [t.pnl_pct for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    gross_profit = sum(winners) if winners else 0
    gross_loss = abs(sum(losers)) if losers else 0

    # Simulate equity curve for max drawdown
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    for pnl in pnls:
        equity *= (1 + pnl / 100)
        peak = max(peak, equity)
        dd = (equity - peak) / peak * 100
        max_dd = min(max_dd, dd)

    return {
        "label": label,
        "total": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": len(winners) / len(trades) * 100,
        "avg_pnl": np.mean(pnls),
        "median_pnl": np.median(pnls),
        "avg_win": np.mean(winners) if winners else 0,
        "avg_loss": np.mean(losers) if losers else 0,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float('inf'),
        "total_return": (equity - 100),
        "max_drawdown": max_dd,
        "avg_holding_days": np.mean([t.holding_days for t in trades]),
        "stop_losses": sum(1 for t in trades if t.exit_reason == "STOP_LOSS"),
        "target_1_hits": sum(1 for t in trades if t.exit_reason == "TARGET_1"),
        "target_2_hits": sum(1 for t in trades if t.exit_reason == "TARGET_2"),
        "time_exits": sum(1 for t in trades if t.exit_reason == "TIME_EXIT"),
    }


def print_results(stats: Dict):
    """Pretty-print results for a signal type."""
    label = stats["label"]
    total = stats["total"]

    if total == 0:
        print(f"\n  {label}: No trades generated.")
        return

    print(f"\n  {'─'*55}")
    print(f"  {label} ({total} trades)")
    print(f"  {'─'*55}")
    print(f"  Win Rate:        {stats['win_rate']:.1f}%  ({stats['winners']}W / {stats['losers']}L)")
    print(f"  Avg P&L:         {stats['avg_pnl']:+.2f}%")
    print(f"  Median P&L:      {stats['median_pnl']:+.2f}%")
    print(f"  Avg Win:         {stats['avg_win']:+.2f}%")
    print(f"  Avg Loss:        {stats['avg_loss']:+.2f}%")
    print(f"  Best / Worst:    {stats['best_trade']:+.2f}% / {stats['worst_trade']:+.2f}%")
    print(f"  Profit Factor:   {stats['profit_factor']:.2f}")
    print(f"  Total Return:    {stats['total_return']:+.2f}%")
    print(f"  Max Drawdown:    {stats['max_drawdown']:.2f}%")
    print(f"  Avg Hold Days:   {stats['avg_holding_days']:.1f}")
    print(f"  Exits: Stop={stats['stop_losses']}  T1={stats['target_1_hits']}  T2={stats['target_2_hits']}  Time={stats['time_exits']}")


def write_report(all_stats: List[Dict], trades: List[ShortTrade], output_dir: Path):
    """Write markdown report + CSV trades."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write trades CSV
    csv_path = output_dir / "short_backtest_trades.csv"
    df = pd.DataFrame([asdict(t) for t in trades])
    df.to_csv(csv_path, index=False, float_format="%.4f")
    print(f"\n  Wrote trades: {csv_path}")

    # Write report
    report_path = output_dir / "short_backtest_report.md"
    lines = [
        "# SHORT Strategy Backtest Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Signal Type Comparison",
        "",
        "| Signal | Trades | Win% | Avg P&L | PF | MaxDD | Avg Hold |",
        "|--------|-------:|-----:|--------:|---:|------:|---------:|",
    ]

    for s in all_stats:
        if s["total"] == 0:
            lines.append(f"| {s['label']} | 0 | — | — | — | — | — |")
        else:
            lines.append(
                f"| {s['label']} | {s['total']} | {s['win_rate']:.1f}% | "
                f"{s['avg_pnl']:+.2f}% | {s['profit_factor']:.2f} | "
                f"{s['max_drawdown']:.1f}% | {s['avg_holding_days']:.0f}d |"
            )

    lines += [
        "",
        "## Exit Distribution",
        "",
        "| Signal | Stop Loss | Target 1 | Target 2 | Time Exit |",
        "|--------|----------:|---------:|---------:|----------:|",
    ]
    for s in all_stats:
        if s["total"] > 0:
            lines.append(
                f"| {s['label']} | {s['stop_losses']} ({s['stop_losses']/s['total']*100:.0f}%) | "
                f"{s['target_1_hits']} ({s['target_1_hits']/s['total']*100:.0f}%) | "
                f"{s['target_2_hits']} ({s['target_2_hits']/s['total']*100:.0f}%) | "
                f"{s['time_exits']} ({s['time_exits']/s['total']*100:.0f}%) |"
            )

    # Add key observations
    lines += [
        "",
        "## Key Observations",
        "",
    ]

    for s in all_stats:
        if s["total"] > 0:
            verdict = "PROFITABLE" if s["avg_pnl"] > 0 else "UNPROFITABLE"
            risk = "HIGH" if s["max_drawdown"] < -15 else ("MODERATE" if s["max_drawdown"] < -8 else "LOW")
            lines.append(f"- **{s['label']}**: {verdict} (avg {s['avg_pnl']:+.2f}%), "
                        f"win rate {s['win_rate']:.0f}%, risk {risk}")

    lines += [
        "",
        "## Recommendation",
        "",
        "Based on the backtest results above, review which SHORT signal types",
        "have positive expectancy (profit factor > 1.0) before deploying live.",
        "Signals with win rate < 45% or profit factor < 1.0 should be disabled",
        "or tightened with additional filters.",
    ]

    report_path.write_text("\n".join(lines))
    print(f"  Wrote report: {report_path}")


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SHORT strategy backtest")
    parser.add_argument("--tickers", nargs="*", default=None, help="Custom ticker list")
    parser.add_argument("--years", type=int, default=2, help="Years of history (default: 2)")
    parser.add_argument("--signal", type=str, default=None,
                        choices=["SHORT_POPUP", "SHORT_OVERBOUGHT", "SHORT_MEAN_REVERT"],
                        help="Test single signal type")
    parser.add_argument("--output", type=str, default="results/short_backtest",
                        help="Output directory")
    args = parser.parse_args()

    universe = args.tickers or DEFAULT_UNIVERSE
    output_dir = Path(args.output)

    # Run combined backtest (all signal types)
    bt = ShortStrategyBacktest(signal_filter=args.signal)
    all_trades = bt.run(universe, years=args.years)

    if not all_trades:
        print("\nNo trades generated. Check your universe/timeframe.")
        return

    # Analyze per signal type
    signal_types = sorted(set(t.signal_type for t in all_trades))
    all_stats = []

    print(f"\n{'='*60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*60}")

    # Per signal type
    for st in signal_types:
        st_trades = [t for t in all_trades if t.signal_type == st]
        stats = analyze_trades(st_trades, st)
        all_stats.append(stats)
        print_results(stats)

    # Combined
    combined = analyze_trades(all_trades, "ALL COMBINED")
    all_stats.append(combined)
    print_results(combined)

    # Per regime
    print(f"\n\n{'='*60}")
    print(f"  BY REGIME")
    print(f"{'='*60}")
    for regime in sorted(set(t.regime for t in all_trades)):
        regime_trades = [t for t in all_trades if t.regime == regime]
        stats = analyze_trades(regime_trades, f"Regime: {regime}")
        print_results(stats)

    # RSI distribution analysis
    print(f"\n\n{'='*60}")
    print(f"  RSI AT ENTRY ANALYSIS")
    print(f"{'='*60}")
    rsi_bins = [(50, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 80), (80, 100)]
    for lo, hi in rsi_bins:
        bin_trades = [t for t in all_trades if lo <= t.rsi_at_entry < hi]
        if bin_trades:
            avg_pnl = np.mean([t.pnl_pct for t in bin_trades])
            wr = sum(1 for t in bin_trades if t.pnl_pct > 0) / len(bin_trades) * 100
            print(f"  RSI {lo}-{hi}: {len(bin_trades)} trades, "
                  f"win rate {wr:.0f}%, avg P&L {avg_pnl:+.2f}%")

    # Write report
    print()
    write_report(all_stats, all_trades, output_dir)

    print(f"\n{'='*60}")
    print(f"  Backtest complete. See {output_dir}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
