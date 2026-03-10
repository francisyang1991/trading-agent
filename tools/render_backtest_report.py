#!/usr/bin/env python3
"""
Render a human-readable backtest report from CSV results.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import pandas as pd


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Render backtest report (Markdown)")
    parser.add_argument("--input", type=str, required=True, help="Input CSV (pattern_backtest_*.csv)")
    parser.add_argument("--output", type=str, default="results/backtest_report.md")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    if df.empty:
        raise SystemExit("No rows found in input CSV.")

    total = len(df)
    wins = (df["outcome"] == "SUCCESS").sum()
    losses = (df["outcome"] == "FAILURE").sum()
    win_rate = wins / total * 100 if total else 0.0

    total_weight = df["size_factor"].sum()
    weighted_win_rate = (
        df.loc[df["outcome"] == "SUCCESS", "size_factor"].sum() / total_weight * 100
        if total_weight
        else 0.0
    )
    weighted_ev = df["weighted_outcome_pct"].sum() / total_weight if total_weight else 0.0

    avg_exit = df["exit_pct"].mean() if "exit_pct" in df.columns else 0.0
    avg_win = df.loc[df["outcome"] == "SUCCESS", "exit_pct"].mean()
    avg_loss = df.loc[df["outcome"] == "FAILURE", "exit_pct"].mean()

    top_wins = df.sort_values("exit_pct", ascending=False).head(10)
    top_losses = df.sort_values("exit_pct", ascending=True).head(10)
    recent = df.sort_values("entry_date", ascending=False).head(10)

    lines = [
        "# Backtest Report",
        "",
        f"- Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Source: `{args.input}`",
        "",
        "## Summary",
        "",
        f"- Patterns: **{total}**",
        f"- Win rate: **{win_rate:.1f}%**",
        f"- Weighted win rate: **{weighted_win_rate:.1f}%**",
        f"- Weighted EV: **{weighted_ev:.2f}%**",
        f"- Avg exit %: **{avg_exit:.2f}%**",
        f"- Avg win %: **{avg_win:.2f}%**",
        f"- Avg loss %: **{avg_loss:.2f}%**",
        "",
        "## Recent 10 Signals",
        "",
        "| Entry Date | Symbol | Outcome | Exit % | Exit Reason |",
        "|---|---|---|---:|---|",
    ]
    for _, row in recent.iterrows():
        lines.append(
            f"| {row['entry_date']} | {row['symbol']} | {row['outcome']} | "
            f"{row['exit_pct']:.2f} | {row['exit_reason']} |"
        )

    lines += [
        "",
        "## Top 10 Wins",
        "",
        "| Entry Date | Symbol | Exit % | Exit Reason |",
        "|---|---|---:|---|",
    ]
    for _, row in top_wins.iterrows():
        lines.append(
            f"| {row['entry_date']} | {row['symbol']} | {row['exit_pct']:.2f} | {row['exit_reason']} |"
        )

    lines += [
        "",
        "## Top 10 Losses",
        "",
        "| Entry Date | Symbol | Exit % | Exit Reason |",
        "|---|---|---:|---|",
    ]
    for _, row in top_losses.iterrows():
        lines.append(
            f"| {row['entry_date']} | {row['symbol']} | {row['exit_pct']:.2f} | {row['exit_reason']} |"
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    print(f"✅ Wrote report: {output_path}")


if __name__ == "__main__":
    main()
