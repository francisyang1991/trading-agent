#!/usr/bin/env python3
"""
Single entry point for trade diagnostics:
- Find most problematic trades
- Analyze entry conditions
- Summarize improvement ideas
- Render charts for selected trades
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))

try:
    from src.tools.pattern_backtest import PatternInstance, render_examples
except Exception:
    PatternInstance = None
    render_examples = None

DEFAULT_PORTFOLIO_CSV = "results/portfolio_backtest_large_2024_2025.csv"
DEFAULT_PATTERN_CSV = "results/pattern_backtest_large_all_score.csv"
DEFAULT_OUTPUT_DIR = "results/problematic_trades_charts"

DEFAULT_THRESHOLDS = {
    "min_confidence": 0.75,
    "min_volume_divergence_ratio": 1.1,
    "min_breakout_volume_ratio": 1.2,
    "min_breakout_pct": 3.0,
    "min_risk_reward": 1.0,
    "min_pullback_pct": 5.0,
    "max_pullback_pct": 12.0,
    "min_dist_to_ema9_pct": 0.5,
    "max_dist_to_ema9_pct": 3.0,
}


def load_trades(csv_file: str) -> pd.DataFrame:
    df = pd.read_csv(csv_file)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    df["exit_price"] = df["entry_price"] * (1 + df["exit_pct"] / 100)
    df["holding_days"] = (df["exit_date"] - df["entry_date"]).dt.days
    df["stop_distance"] = (df["entry_price"] - df["stop_loss"]).abs()
    df["target1_distance"] = (df["target_1"] - df["entry_price"]).abs()
    df["target2_distance"] = (df["target_2"] - df["entry_price"]).abs()
    df["risk_reward_t1"] = df["stop_distance"] / df["target1_distance"].replace(0, pd.NA)
    df["risk_reward_t2"] = df["stop_distance"] / df["target2_distance"].replace(0, pd.NA)
    return df


def print_trade_summary(df: pd.DataFrame) -> None:
    total = len(df)
    winners = len(df[df["pnl"] > 0])
    losers = len(df[df["pnl"] < 0])
    total_pnl = df["pnl"].sum()
    print(f"Total trades: {total}")
    print(f"Winning trades: {winners} ({winners / total * 100:.1f}%)")
    print(f"Losing trades: {losers} ({losers / total * 100:.1f}%)")
    print(f"Total P&L: ${total_pnl:.2f}")
    print()


def select_problematic_trades(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    largest_losses = (
        df[df["pnl"] < 0].sort_values("pnl").head(top_n).assign(problem_type="Largest Loss")
    )
    worst_rr = (
        df[df["pnl"] < 0]
        .sort_values("risk_reward_t1", ascending=False)
        .head(top_n)
        .assign(problem_type="Worst Risk-Reward (T1)")
    )
    quick_stops = (
        df[(df["holding_days"] <= 3) & (df["pnl"] < 0)]
        .sort_values("pnl")
        .head(top_n)
        .assign(problem_type="Quick Stop-Out")
    )
    long_fails = (
        df[(df["holding_days"] > 20) & (df["pnl"] < 0)]
        .sort_values("pnl")
        .head(top_n)
        .assign(problem_type="Long Hold Failure")
    )
    wide_stops = (
        df[df["risk_reward_t1"] > 2]
        .sort_values("risk_reward_t1", ascending=False)
        .head(top_n)
        .assign(problem_type="Very Wide Stop")
    )

    all_problematic = pd.concat(
        [largest_losses, worst_rr, quick_stops, long_fails, wide_stops], ignore_index=True
    ).drop_duplicates(subset=["symbol", "entry_date"])
    all_problematic = all_problematic.sort_values(
        ["pnl", "risk_reward_t1"], ascending=[True, False]
    )
    return all_problematic.head(top_n)


def format_problematic_trades(problematic: pd.DataFrame) -> List[Tuple[str, str]]:
    trades = []
    for _, row in problematic.iterrows():
        entry_date = row["entry_date"].strftime("%Y-%m-%d")
        trades.append((row["symbol"], entry_date))
        print(f"{row['symbol']} - {row['problem_type']}")
        print(f"  Entry: {entry_date} @ ${row['entry_price']:.2f}")
        print(
            f"  Exit: {row['exit_date'].strftime('%Y-%m-%d')} "
            f"@ ${row['exit_price']:.2f} ({row['exit_reason']})"
        )
        print(f"  P&L: ${row['pnl']:.2f}")
        print(f"  Return: {row['exit_pct']:.2f}%")
        print(f"  Risk-Reward T1: {row['risk_reward_t1']:.1f}")
        print(f"  Holding Days: {row['holding_days']:.0f}")
        print()
    return trades


def load_patterns(csv_file: str) -> pd.DataFrame:
    patterns = pd.read_csv(csv_file)
    patterns["entry_date"] = patterns["entry_date"].astype(str)
    return patterns


def find_pattern_row(
    patterns: pd.DataFrame, symbol: str, entry_date: str
) -> pd.Series | None:
    match = patterns[(patterns["symbol"] == symbol) & (patterns["entry_date"] == entry_date)].head(1)
    if match.empty:
        return None
    return match.iloc[0]


def entry_issues_and_improvements(
    pattern: pd.Series, thresholds: Dict[str, float]
) -> Tuple[List[str], List[str]]:
    issues: List[str] = []
    improvements: List[str] = []

    confidence = float(pattern["confidence"])
    volume_divergence_ratio = float(pattern["volume_divergence_ratio"])
    breakout_volume_ratio = float(pattern["breakout_volume_ratio"])
    pullback_pct = float(pattern["pullback_pct"])
    breakout_pct = float(pattern["breakout_pct"])
    dist_to_ema9_pct = float(pattern["dist_to_ema9_pct"])
    risk_reward = float(pattern["risk_reward"])
    blockers = pattern.get("blockers", "")

    if confidence < thresholds["min_confidence"]:
        issues.append("Low confidence signal")
        improvements.append(f"Require confidence >= {thresholds['min_confidence']:.2f}")
    if volume_divergence_ratio < thresholds["min_volume_divergence_ratio"]:
        issues.append("Weak volume divergence")
        improvements.append(
            f"Require volume_divergence_ratio >= {thresholds['min_volume_divergence_ratio']:.1f}"
        )
    if breakout_volume_ratio < thresholds["min_breakout_volume_ratio"]:
        issues.append("Weak breakout volume")
        improvements.append(
            f"Require breakout_volume_ratio >= {thresholds['min_breakout_volume_ratio']:.1f}"
        )
    if pullback_pct < thresholds["min_pullback_pct"]:
        issues.append("Shallow pullback")
        improvements.append(f"Require pullback_pct >= {thresholds['min_pullback_pct']:.1f}%")
    if pullback_pct > thresholds["max_pullback_pct"]:
        issues.append("Deep pullback")
        improvements.append(f"Limit pullback_pct <= {thresholds['max_pullback_pct']:.1f}%")
    if breakout_pct < thresholds["min_breakout_pct"]:
        issues.append("Weak breakout")
        improvements.append(f"Require breakout_pct >= {thresholds['min_breakout_pct']:.1f}%")
    if dist_to_ema9_pct < thresholds["min_dist_to_ema9_pct"]:
        issues.append("Too close to EMA9 (overbought)")
        improvements.append(
            f"Require dist_to_ema9_pct >= {thresholds['min_dist_to_ema9_pct']:.1f}%"
        )
    if dist_to_ema9_pct > thresholds["max_dist_to_ema9_pct"]:
        issues.append("Too far from EMA9 support")
        improvements.append(
            f"Require dist_to_ema9_pct <= {thresholds['max_dist_to_ema9_pct']:.1f}%"
        )
    if risk_reward < thresholds["min_risk_reward"]:
        issues.append("Poor risk-reward ratio")
        improvements.append(f"Require risk_reward >= {thresholds['min_risk_reward']:.1f}")
    if isinstance(blockers, str) and blockers.strip():
        issues.append(f"Pattern has blockers: {blockers}")
        improvements.append("Filter out patterns with blockers")

    return issues, improvements


def analyze_entry_conditions(
    patterns: pd.DataFrame,
    problematic_trades: Iterable[Tuple[str, str]],
    thresholds: Dict[str, float],
) -> Counter:
    issue_counts: Counter = Counter()
    print("ENTRY CONDITION ANALYSIS")
    print("=" * 80)
    for idx, (symbol, entry_date) in enumerate(problematic_trades, 1):
        print(f"\n{idx}. {symbol} ({entry_date})")
        print("-" * 60)
        pattern = find_pattern_row(patterns, symbol, entry_date)
        if pattern is None:
            print("  Pattern not found for this trade in pattern CSV.")
            continue

        print("Key Pattern Metrics:")
        print(f"  Confidence: {pattern['confidence']:.2f}")
        print(f"  Score: {pattern['score']:.1f}")
        print(f"  Setup Grade: {pattern['setup_grade']}")
        print(f"  Volume Divergence: {pattern['volume_divergence_ratio']:.2f}")
        print(f"  Breakout Volume: {pattern['breakout_volume_ratio']:.2f}")
        print(f"  Pullback %: {pattern['pullback_pct']:.1f}%")
        print(f"  Breakout %: {pattern['breakout_pct']:.1f}%")
        print(f"  Distance to EMA9: {pattern['dist_to_ema9_pct']:.1f}%")
        print(f"  Risk-Reward: {pattern['risk_reward']:.1f}x")
        print(f"  Blockers: {pattern['blockers']}")

        issues, improvements = entry_issues_and_improvements(pattern, thresholds)
        for issue in issues:
            issue_counts[issue] += 1

        print("\nIssues Identified:")
        if issues:
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("  - No major issues flagged by thresholds")

        print("\nSuggested Improvements:")
        if improvements:
            for improvement in improvements:
                print(f"  - {improvement}")
        else:
            print("  - Review chart for entry timing/structure")
    return issue_counts


def summarize_improvements(issue_counts: Counter, thresholds: Dict[str, float]) -> None:
    print("\nCONSOLIDATED IMPROVEMENT SUMMARY")
    print("=" * 80)
    if not issue_counts:
        print("No issues detected from the selected trades.")
        return
    print("Most common issues (count):")
    for issue, count in issue_counts.most_common():
        print(f"  - {issue} ({count})")

    print("\nRecommended default thresholds:")
    print(f"  - confidence >= {thresholds['min_confidence']:.2f}")
    print(f"  - volume_divergence_ratio >= {thresholds['min_volume_divergence_ratio']:.1f}")
    print(f"  - breakout_volume_ratio >= {thresholds['min_breakout_volume_ratio']:.1f}")
    print(f"  - breakout_pct >= {thresholds['min_breakout_pct']:.1f}%")
    print(f"  - risk_reward >= {thresholds['min_risk_reward']:.1f}")
    print(
        f"  - pullback_pct in [{thresholds['min_pullback_pct']:.1f}%, "
        f"{thresholds['max_pullback_pct']:.1f}%]"
    )
    print(
        f"  - dist_to_ema9_pct in [{thresholds['min_dist_to_ema9_pct']:.1f}%, "
        f"{thresholds['max_dist_to_ema9_pct']:.1f}%]"
    )
    print("  - filter out patterns with blockers")


def generate_problematic_charts(
    patterns: pd.DataFrame,
    problematic_trades: Iterable[Tuple[str, str]],
    output_dir: str,
    period: str,
    lookback_bars: int,
    forward_bars: int,
) -> None:
    if PatternInstance is None or render_examples is None:
        raise SystemExit(
            "Pattern chart rendering is unavailable. Ensure dependencies are installed."
        )
    examples: List[PatternInstance] = []
    for symbol, entry_date in problematic_trades:
        pattern = find_pattern_row(patterns, symbol, entry_date)
        if pattern is None:
            print(f"No pattern found for {symbol} on {entry_date}")
            continue
        examples.append(PatternInstance(**pattern.to_dict()))

    if not examples:
        print("No matching patterns found for chart rendering.")
        return

    render_examples(
        patterns=examples,
        period=period,
        output_dir=output_dir,
        examples=examples,
        lookback_bars=lookback_bars,
        forward_bars=forward_bars,
    )
    print(f"Rendered {len(examples)} charts to {output_dir}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trade analysis utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--portfolio-csv", default=DEFAULT_PORTFOLIO_CSV)
    common.add_argument("--pattern-csv", default=DEFAULT_PATTERN_CSV)
    common.add_argument("--top-n", type=int, default=10)

    subparsers.add_parser("problematic", parents=[common], help="List problematic trades")
    subparsers.add_parser("entries", parents=[common], help="Analyze entry conditions")
    subparsers.add_parser("summary", parents=[common], help="Summarize improvement ideas")

    charts = subparsers.add_parser("charts", parents=[common], help="Render charts")
    charts.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    charts.add_argument("--period", default="2y")
    charts.add_argument("--lookback-bars", type=int, default=90)
    charts.add_argument("--forward-bars", type=int, default=30)

    all_cmd = subparsers.add_parser("all", parents=[common], help="Run all steps")
    all_cmd.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    all_cmd.add_argument("--period", default="2y")
    all_cmd.add_argument("--lookback-bars", type=int, default=90)
    all_cmd.add_argument("--forward-bars", type=int, default=30)

    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    trades = load_trades(args.portfolio_csv)
    print_trade_summary(trades)
    problematic = select_problematic_trades(trades, args.top_n)
    print("TOP PROBLEMATIC TRADES")
    print("=" * 80)
    problematic_list = format_problematic_trades(problematic)

    if args.command == "problematic":
        return

    patterns = load_patterns(args.pattern_csv)
    issue_counts = Counter()

    if args.command in {"entries", "summary", "all"}:
        issue_counts = analyze_entry_conditions(patterns, problematic_list, DEFAULT_THRESHOLDS)

    if args.command in {"summary", "all"}:
        summarize_improvements(issue_counts, DEFAULT_THRESHOLDS)

    if args.command in {"charts", "all"}:
        generate_problematic_charts(
            patterns,
            problematic_list,
            output_dir=args.output_dir,
            period=args.period,
            lookback_bars=args.lookback_bars,
            forward_bars=args.forward_bars,
        )


if __name__ == "__main__":
    main()