#!/usr/bin/env python3
"""
Portfolio backtest using signal CSV (entry/exit dates and exit_pct).
Assumes max 5 concurrent positions, equal-weight allocation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict

import pandas as pd
import matplotlib.pyplot as plt

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_pct: float
    exit_reason: str
    shares: int
    position_value: float
    pnl: float
    target_1: float
    target_2: float
    stop_loss: float


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio backtest (max 5 positions)")
    parser.add_argument("--input", type=str, required=True, help="Performance CSV")
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end", type=str, default="2025-12-31")
    parser.add_argument("--capital", type=float, default=100000.0)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--output", type=str, default="results/portfolio_backtest_2024_2025.csv")
    parser.add_argument("--report", type=str, default="results/portfolio_backtest_2024_2025.md")
    parser.add_argument("--equity-csv", type=str, default="results/portfolio_backtest_2024_2025_equity.csv")
    parser.add_argument("--equity-png", type=str, default="results/portfolio_backtest_2024_2025_equity.png")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    df = df[(df["entry_date"] >= args.start) & (df["entry_date"] <= args.end)]
    df = df[df["outcome_date"].notna()]
    df = df.sort_values("entry_date")

    equity = args.capital
    max_positions = args.max_positions
    open_positions: Dict[str, Trade] = {}
    trades: List[Trade] = []
    equity_curve = []
    dm = DataManager()
    price_cache: Dict[str, pd.DataFrame] = {}

    def get_price(symbol: str, date: str) -> float:
        if symbol not in price_cache:
            price_cache[symbol] = dm.get_daily_data(symbol, period="2y")
        data = price_cache[symbol]
        if data is None or data.empty:
            return 0.0
        idx = data.index.get_indexer([pd.to_datetime(date)], method="ffill")[0]
        return float(data["Close"].iloc[idx])

    for _, row in df.iterrows():
        entry_date = row["entry_date"]
        exit_date = row["outcome_date"]
        symbol = row["symbol"]
        entry_price = float(row["entry_price"])
        target_1 = float(row.get("target_1", 0.0))
        target_2 = float(row.get("target_2", 0.0))
        stop_loss = float(row.get("stop_loss", 0.0))
        exit_pct = float(row["exit_pct"])
        exit_reason = row["exit_reason"]

        # Close any positions whose exit date is before current entry_date
        to_close = [s for s, t in open_positions.items() if t.exit_date <= entry_date]
        for s in to_close:
            t = open_positions.pop(s)
            equity += t.pnl
            equity_curve.append((t.exit_date, equity))
            trades.append(t)

        if symbol in open_positions:
            continue
        if len(open_positions) >= max_positions:
            continue

        allocation = equity / max_positions
        shares = int(allocation / entry_price)
        if shares <= 0:
            continue
        position_value = shares * entry_price
        pnl = position_value * (exit_pct / 100.0)

        open_positions[symbol] = Trade(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=exit_date,
            exit_pct=exit_pct,
            exit_reason=exit_reason,
            shares=shares,
            position_value=position_value,
            pnl=pnl,
            target_1=target_1,
            target_2=target_2,
            stop_loss=stop_loss,
        )

    # Close remaining positions at end date using their recorded exit date
    for s, t in list(open_positions.items()):
        equity += t.pnl
        equity_curve.append((t.exit_date, equity))
        trades.append(t)

    trades_df = pd.DataFrame([t.__dict__ for t in trades])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(args.output, index=False, float_format="%.2f")

    # Compute drawdown on event-based equity curve
    # Daily equity curve (mark-to-market)
    date_range = pd.date_range(args.start, args.end, freq="D")
    daily_rows = []
    cash = args.capital
    open_positions_daily: Dict[str, Trade] = {}
    entry_queue = trades_df.sort_values("entry_date").to_dict("records")
    entry_idx = 0
    for date in date_range:
        date_str = date.strftime("%Y-%m-%d")
        # Close positions
        to_close = [s for s, t in open_positions_daily.items() if t.exit_date <= date_str]
        for s in to_close:
            t = open_positions_daily.pop(s)
            cash += t.position_value + t.pnl
        # Open positions
        while entry_idx < len(entry_queue) and entry_queue[entry_idx]["entry_date"] == date_str:
            row = entry_queue[entry_idx]
            entry_idx += 1
            if row["symbol"] in open_positions_daily:
                continue
            if len(open_positions_daily) >= max_positions:
                continue
            shares = int(row["shares"])
            position_value = float(row["position_value"])
            if shares <= 0 or position_value <= 0:
                continue
            pnl = position_value * (row["exit_pct"] / 100.0)
            cash -= position_value
            open_positions_daily[row["symbol"]] = Trade(
                symbol=row["symbol"],
                entry_date=row["entry_date"],
                entry_price=row["entry_price"],
                exit_date=row["exit_date"],
                exit_pct=row["exit_pct"],
                exit_reason=row["exit_reason"],
                shares=shares,
                position_value=position_value,
                pnl=pnl,
                target_1=row.get("target_1", 0.0),
                target_2=row.get("target_2", 0.0),
                stop_loss=row.get("stop_loss", 0.0),
            )
        equity_today = cash
        for p in open_positions_daily.values():
            price = get_price(p.symbol, date_str)
            equity_today += p.shares * price
        daily_rows.append({"date": date_str, "equity": equity_today})
    
    curve_df = pd.DataFrame(daily_rows)
    curve_df["peak"] = curve_df["equity"].cummax()
    curve_df["drawdown"] = (curve_df["equity"] - curve_df["peak"]) / curve_df["peak"] * 100
    max_dd = curve_df["drawdown"].min() if not curve_df.empty else 0.0
    Path(args.equity_csv).parent.mkdir(parents=True, exist_ok=True)
    curve_df.to_csv(args.equity_csv, index=False)

    # Plot equity curve
    plt.figure(figsize=(10, 5))
    plt.plot(pd.to_datetime(curve_df["date"]), curve_df["equity"], color="blue", linewidth=1.5)
    plt.title("Portfolio Equity Curve (2024–2025)")
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.tight_layout()
    plt.savefig(args.equity_png)

    total_return = (equity - args.capital) / args.capital * 100
    years = max(1.0, (pd.to_datetime(args.end) - pd.to_datetime(args.start)).days / 365.0)
    cagr = ((equity / args.capital) ** (1 / years) - 1) * 100

    report_lines = [
        "# Portfolio Backtest (2024–2025)",
        "",
        f"- Start capital: **${args.capital:,.0f}**",
        f"- End equity: **${equity:,.0f}**",
        f"- Total return: **{total_return:.2f}%**",
        f"- CAGR (approx): **{cagr:.2f}%**",
        f"- Max drawdown (daily mark-to-market): **{max_dd:.2f}%**",
        f"- Trades: **{len(trades)}**",
        "",
        "## Recent Trades",
        "",
        "| Entry Date | Symbol | Entry | T1 | T2 | Shares | Exit Date | Exit % | PnL | Exit Reason |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for _, row in trades_df.sort_values("entry_date", ascending=False).head(10).iterrows():
        report_lines.append(
            f"| {row['entry_date']} | {row['symbol']} | {row['entry_price']:.2f} | "
            f"{row.get('target_1', 0.0):.2f} | {row.get('target_2', 0.0):.2f} | "
            f"{int(row['shares'])} | {row['exit_date']} | {row['exit_pct']:.2f} | "
            f"{row['pnl']:.2f} | {row['exit_reason']} |"
        )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines))
    print(f"✅ Wrote trades: {args.output}")
    print(f"✅ Wrote report: {args.report}")
    print(f"✅ Wrote equity curve: {args.equity_csv}")
    print(f"✅ Wrote equity chart: {args.equity_png}")


if __name__ == "__main__":
    main()
