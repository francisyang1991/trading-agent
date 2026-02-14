#!/usr/bin/env python3
"""
No-Lookahead Portfolio Backtest (Volume Pullback EMA9)
=======================================================
This is a walk-forward simulation that does NOT use future bars
for exits. It uses the original VolumePullbackEntrySignal and
manages positions day-by-day.

Key differences vs pattern_backtest.py:
1) Entries are generated at each day using generate_at_index (no lookahead)
2) Exits are evaluated daily using only that day's OHLC
3) Max positions enforced (default 5) with equal-weight allocation
4) Equity curve is computed and plotted
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager
from src.signals.entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig


DATA_MANAGER = DataManager()


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_idx: int
    entry_price: float
    shares_total: int
    shares_remaining: int
    stop_loss: float
    target_1: float
    target_2: float
    partial_size: float
    runner_size: float
    partial_taken: bool
    stop_for_runner: float
    max_gain_pct: float
    max_loss_pct: float
    cost_basis: float
    realized_pnl: float
    outcome: str = "PENDING"
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""


def load_universe(config_path: Optional[Path] = None) -> Dict:
    import yaml
    if config_path is None:
        config_path = ROOT / "config" / "stock_universe.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def clean_symbols(symbols: List[str]) -> List[str]:
    import re
    pattern = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")
    cleaned = []
    for s in symbols:
        if not isinstance(s, str):
            continue
        sym = s.strip().upper()
        if sym.startswith("$"):
            continue
        if not pattern.match(sym):
            continue
        cleaned.append(sym)
    return sorted(set(cleaned))


def build_symbol_list(
    universe_file: Optional[Path],
    symbols: Optional[List[str]],
    theme: Optional[str],
    scan_all: bool,
    max_symbols: int,
) -> List[str]:
    data = load_universe(universe_file)
    if symbols:
        raw = [s.upper() for s in symbols]
    elif theme:
        raw = data.get("themes", {}).get(theme, {}).get("symbols", [])
    elif scan_all:
        raw = data.get("all_symbols", [])
    else:
        raw = list(
            set(
                data.get("volatile_momentum", [])
                + data.get("themes", {}).get("ultra_volatile", {}).get("symbols", [])
            )
        )
    cleaned = clean_symbols(raw)
    if max_symbols and len(cleaned) > max_symbols:
        return cleaned[:max_symbols]
    return cleaned


def _get_price_at(data: pd.DataFrame, date: str) -> Tuple[float, float, float]:
    """Return (high, low, close) for a date or zeros if missing."""
    try:
        idx = data.index.get_indexer([pd.to_datetime(date)], method="ffill")[0]
    except Exception:
        return 0.0, 0.0, 0.0
    if idx < 0 or idx >= len(data):
        return 0.0, 0.0, 0.0
    row = data.iloc[idx]
    return float(row["High"]), float(row["Low"]), float(row["Close"])


def main():
    parser = argparse.ArgumentParser(description="No-lookahead Volume Pullback backtest")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2025-12-31")
    parser.add_argument("--capital", type=float, default=100000.0)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--max-symbols", type=int, default=500)
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to scan")
    parser.add_argument("--theme", type=str, help="Theme from universe")
    parser.add_argument("--all", action="store_true", help="Scan full universe")
    parser.add_argument(
        "--universe-file",
        type=str,
        default=str(ROOT / "config" / "stock_universe.yaml"),
        help="Universe YAML file (default: config/stock_universe.yaml)",
    )
    parser.add_argument("--period", type=str, default="2y")
    parser.add_argument("--min-avg-volume", type=float, default=1_000_000)
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument("--cooldown-bars", type=int, default=5)
    parser.add_argument("--max-hold", type=int, default=20)
    parser.add_argument("--fallback-win", type=float, default=5.0)
    parser.add_argument("--fallback-loss", type=float, default=8.0)
    parser.add_argument(
        "--target-method",
        type=str,
        default="resistance",
        choices=["resistance", "measured_move", "fib", "auto"],
    )
    parser.add_argument("--output-dir", type=str, default="results/no_lookahead")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols = build_symbol_list(
        universe_file=Path(args.universe_file),
        symbols=args.symbols,
        theme=args.theme,
        scan_all=args.all,
        max_symbols=args.max_symbols,
    )
    print(f"Universe: {len(symbols)} symbols")

    # Load price data for each symbol (2y)
    price_data: Dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = DATA_MANAGER.get_daily_data(s, period=args.period)
        if df is not None and not df.empty:
            avg_volume = float(df["Volume"].tail(60).mean())
            if avg_volume >= args.min_avg_volume:
                price_data[s] = df

    # Use SPY as master calendar; fallback to largest available symbol
    spy = DATA_MANAGER.get_daily_data("SPY", period="2y")
    if spy is None or spy.empty:
        spy = max(price_data.values(), key=lambda d: len(d), default=None)
    if spy is None or spy.empty:
        raise RuntimeError("Calendar data missing; cannot build calendar")

    all_days = [d.strftime("%Y-%m-%d") for d in spy.index]
    all_days = [d for d in all_days if args.start <= d <= args.end]
    print(f"Trading days: {len(all_days)}")

    signal_gen = VolumePullbackEntrySignal(
        config=VolumePullbackConfig(
            min_green_candles=3,
            max_pullback_bars=7,
            ema9_tolerance_pct=10.0,
            max_pullback_pct=18.0,
            pullback_vol_ratio=0.85,
            min_breakout_vol_ratio=1.1,
            min_confidence=args.min_confidence,
            require_prior_resistance=True,
            min_breakout_pct=1.0,
            support_hold_tolerance_pct=8.0,
            max_below_ema9_pct=4.0,
            target_method=args.target_method,
        )
    )

    # Precompute indicators for each symbol
    indicators_map: Dict[str, Dict[str, pd.Series]] = {}
    index_map: Dict[str, Dict[str, int]] = {}
    for s, df in price_data.items():
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]
        indicators_map[s] = {
            "ema9": close.ewm(span=signal_gen.config.ema_fast, adjust=False).mean(),
            "ema21": close.ewm(span=signal_gen.config.ema_medium, adjust=False).mean(),
            "ema50": close.ewm(span=signal_gen.config.ema_trend, adjust=False).mean(),
            "volume_ma": volume.rolling(window=20).mean(),
            "atr": signal_gen._calculate_atr(high, low, close, 14),
        }
        index_map[s] = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(df.index)}

    cash = args.capital
    positions: Dict[str, Position] = {}
    trades: List[Dict] = []
    equity_rows: List[Dict] = []
    last_signal_idx: Dict[str, int] = {s: -args.cooldown_bars for s in symbols}

    for day in all_days:
        # 1) Update open positions
        to_close = []
        for sym, pos in positions.items():
            df = price_data.get(sym)
            if df is None:
                continue
            hi, lo, close = _get_price_at(df, day)
            if close <= 0:
                continue

            # Track max gain/loss
            gain = (hi - pos.entry_price) / pos.entry_price * 100
            loss = (pos.entry_price - lo) / pos.entry_price * 100
            pos.max_gain_pct = max(pos.max_gain_pct, gain)
            pos.max_loss_pct = max(pos.max_loss_pct, loss)

            # Partial exit at T1
            if not pos.partial_taken and hi >= pos.target_1 and pos.target_1 > pos.entry_price:
                sell_shares = int(pos.shares_total * pos.partial_size)
                if sell_shares > 0 and sell_shares <= pos.shares_remaining:
                    cash += sell_shares * pos.target_1
                    pos.realized_pnl += (pos.target_1 - pos.entry_price) * sell_shares
                    pos.shares_remaining -= sell_shares
                    pos.partial_taken = True
                    if signal_gen.config.move_stop_to_entry_on_partial:
                        pos.stop_for_runner = max(pos.stop_for_runner, pos.entry_price)

            # Runner exit logic
            if pos.shares_remaining > 0:
                if lo <= pos.stop_for_runner:
                    pos.exit_price = pos.stop_for_runner
                    pos.exit_date = day
                    pos.exit_reason = "target_1_partial+stop" if pos.partial_taken else "stop_loss"
                    pos.outcome = "SUCCESS" if pos.partial_taken else "FAILURE"
                    to_close.append(sym)
                elif hi >= pos.target_2:
                    pos.exit_price = pos.target_2
                    pos.exit_date = day
                    pos.exit_reason = "target_2"
                    pos.outcome = "SUCCESS"
                    to_close.append(sym)

            # Fallback after max_hold bars (no lookahead: exit at close)
            if sym not in to_close:
                days_held = (pd.to_datetime(day) - pd.to_datetime(pos.entry_date)).days
                if days_held >= args.max_hold:
                    pos.exit_price = close
                    pos.exit_date = day
                    if pos.max_gain_pct > args.fallback_win:
                        pos.exit_reason = "fallback_win"
                        pos.outcome = "SUCCESS"
                    elif pos.max_loss_pct > args.fallback_loss:
                        pos.exit_reason = "fallback_loss"
                        pos.outcome = "FAILURE"
                    else:
                        pos.exit_reason = "time_exit"
                        pos.outcome = "SUCCESS" if close >= pos.entry_price else "FAILURE"
                    to_close.append(sym)

        # Close positions
        for sym in to_close:
            pos = positions.pop(sym)
            cash += pos.shares_remaining * pos.exit_price
            pos.realized_pnl += (pos.exit_price - pos.entry_price) * pos.shares_remaining
            exit_pct = (pos.realized_pnl / pos.cost_basis) * 100 if pos.cost_basis > 0 else 0.0
            trades.append({
                "symbol": pos.symbol,
                "entry_date": pos.entry_date,
                "entry_price": pos.entry_price,
                "exit_date": pos.exit_date,
                "exit_price": pos.exit_price,
                "exit_pct": exit_pct,
                "exit_reason": pos.exit_reason,
                "outcome": pos.outcome,
                "shares": pos.shares_total,
                "position_value": pos.cost_basis,
                "pnl": pos.realized_pnl,
            })

        # 2) Generate signals for today
        candidates: List[Tuple[float, str, float, float, float, float, int]] = []
        for sym in symbols:
            df = price_data.get(sym)
            if df is None:
                continue
            idx = index_map[sym].get(day)
            if idx is None:
                continue
            if idx - last_signal_idx[sym] < args.cooldown_bars:
                continue
            if idx < signal_gen.config.ema_trend + 20:
                continue

            signal = signal_gen.generate_at_index(sym, df, idx, indicators=indicators_map[sym])
            if signal is None or signal.confidence < args.min_confidence:
                continue

            metadata = signal.metadata or {}
            if not metadata.get("is_tradeable", False):
                continue

            entry_price = float(signal.price or df["Close"].iloc[idx])
            stop_loss = float(signal.stop_loss or metadata.get("stop_loss", 0.0))
            target_1 = float(metadata.get("target_1_50pct", signal.take_profit or 0.0))
            target_2 = float(metadata.get("target_2_full", target_1))
            score = float(metadata.get("score", signal.confidence * 100))

            candidates.append((score, sym, entry_price, stop_loss, target_1, target_2, idx))

        # 3) Open positions (highest score first)
        candidates.sort(key=lambda x: x[0], reverse=True)
        for score, sym, entry_price, stop_loss, target_1, target_2, idx in candidates:
            if sym in positions:
                continue
            if len(positions) >= args.max_positions:
                break
            allocation = (cash + sum(p.shares_remaining * _get_price_at(price_data[p.symbol], day)[2]
                                     for p in positions.values())) / args.max_positions
            shares = int(allocation / entry_price)
            if shares <= 0:
                continue
            cost = shares * entry_price
            if cost > cash:
                continue

            partial_size = signal_gen.config.partial_exit_size
            runner_size = 1.0 - partial_size
            positions[sym] = Position(
                symbol=sym,
                entry_date=day,
                entry_idx=idx,
                entry_price=entry_price,
                shares_total=shares,
                shares_remaining=shares,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                partial_size=partial_size,
                runner_size=runner_size,
                partial_taken=False,
                stop_for_runner=stop_loss,
                max_gain_pct=0.0,
                max_loss_pct=0.0,
                cost_basis=cost,
                realized_pnl=0.0,
            )
            cash -= cost
            last_signal_idx[sym] = idx

        # 4) Equity curve
        equity_today = cash
        for pos in positions.values():
            _, _, close = _get_price_at(price_data[pos.symbol], day)
            equity_today += pos.shares_remaining * close
        equity_rows.append({"date": day, "equity": equity_today})

    # Close remaining positions at end
    end_day = all_days[-1] if all_days else args.end
    for sym, pos in list(positions.items()):
        _, _, close = _get_price_at(price_data[pos.symbol], end_day)
        pos.exit_price = close
        pos.exit_date = end_day
        pos.exit_reason = "end_of_period"
        pos.outcome = "SUCCESS" if close >= pos.entry_price else "FAILURE"
        cash += pos.shares_remaining * close
        pos.realized_pnl += (pos.exit_price - pos.entry_price) * pos.shares_remaining
        exit_pct = (pos.realized_pnl / pos.cost_basis) * 100 if pos.cost_basis > 0 else 0.0
        trades.append({
            "symbol": pos.symbol,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "exit_date": pos.exit_date,
            "exit_price": pos.exit_price,
            "exit_pct": exit_pct,
            "exit_reason": pos.exit_reason,
            "outcome": pos.outcome,
            "shares": pos.shares_total,
            "position_value": pos.cost_basis,
            "pnl": pos.realized_pnl,
        })
        positions.pop(sym, None)

    # Save trades
    trades_df = pd.DataFrame(trades)
    trades_path = out_dir / "no_lookahead_trades.csv"
    trades_df.to_csv(trades_path, index=False)

    # Save equity curve + chart
    equity_df = pd.DataFrame(equity_rows)
    equity_df["peak"] = equity_df["equity"].cummax()
    equity_df["drawdown"] = (equity_df["equity"] - equity_df["peak"]) / equity_df["peak"] * 100
    equity_csv = out_dir / "no_lookahead_equity.csv"
    equity_df.to_csv(equity_csv, index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.figure(figsize=(12, 5))
    plt.plot(pd.to_datetime(equity_df["date"]), equity_df["equity"], color="blue", linewidth=1.5)
    plt.title(f"No-Lookahead Equity Curve ({args.start} – {args.end})")
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.tight_layout()
    chart_path = out_dir / "no_lookahead_equity.png"
    plt.savefig(chart_path, dpi=100)
    plt.close()

    # Summary
    final_equity = equity_df["equity"].iloc[-1] if not equity_df.empty else args.capital
    total_return = (final_equity - args.capital) / args.capital * 100 if args.capital > 0 else 0.0
    max_dd = equity_df["drawdown"].min() if not equity_df.empty else 0.0
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    win_rate = (len(wins) / len(trades_df) * 100) if len(trades_df) > 0 else 0.0

    print("\n=== No-Lookahead Backtest ===")
    print(f"Trades: {len(trades_df)} | Win Rate: {win_rate:.1f}%")
    print(f"Return: {total_return:.2f}% | Max DD: {max_dd:.2f}%")
    print(f"Saved trades: {trades_path}")
    print(f"Equity CSV: {equity_csv}")
    print(f"Equity chart: {chart_path}")


if __name__ == "__main__":
    main()
