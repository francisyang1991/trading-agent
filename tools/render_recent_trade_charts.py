#!/usr/bin/env python3
"""
Render charts for recent trades based on portfolio backtest and pattern CSV.
"""
from __future__ import annotations

from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.pattern_backtest import PatternInstance, render_examples


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Render recent trade charts")
    parser.add_argument("--portfolio-csv", type=str, default="results/portfolio_backtest_2024_2025.csv")
    parser.add_argument("--pattern-csv", type=str, default="results/pattern_backtest_large_all_score.csv")
    parser.add_argument("--output-dir", type=str, default="results/portfolio_recent_examples")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--period", type=str, default="2y")
    args = parser.parse_args()

    portfolio = pd.read_csv(args.portfolio_csv).sort_values("entry_date", ascending=False).head(args.limit)
    patterns = pd.read_csv(args.pattern_csv)

    examples = []
    for _, trade in portfolio.iterrows():
        match = patterns[
            (patterns["symbol"] == trade["symbol"])
            & (patterns["entry_date"] == trade["entry_date"])
        ].head(1)
        if match.empty:
            continue
        examples.append(PatternInstance(**match.iloc[0].to_dict()))

    if not examples:
        raise SystemExit("No matching patterns found for recent trades.")

    render_examples(
        patterns=examples,
        period=args.period,
        output_dir=args.output_dir,
        examples=examples,
        lookback_bars=90,
        forward_bars=30,
    )
    print(f"✅ Rendered {len(examples)} trade charts to {args.output_dir}")


if __name__ == "__main__":
    main()
