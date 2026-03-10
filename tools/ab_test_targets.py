#!/usr/bin/env python3
"""
A/B test for target calculation methods.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.signals.entry.volume_pullback_entry import VolumePullbackConfig
from src.tools.ab_test_shared import load_universe, run_pattern_sweep


def run_variant(symbols, period, config, args, label):
    _, metrics = run_pattern_sweep(
        symbols=symbols,
        period=period,
        config=config,
        min_confidence=args.min_confidence,
        min_days_to_outcome=args.min_days_to_outcome,
        include_wait=args.include_wait,
        max_hold_bars=args.max_hold_bars,
        fallback_win_gain=args.fallback_win_gain,
        fallback_loss=args.fallback_loss,
        score_full=args.score_full,
        score_half=args.score_half,
        score_quarter=args.score_quarter,
        score_min=args.score_min,
        earnings_window_days=args.earnings_window_days,
        max_workers=args.max_workers,
        min_avg_volume=getattr(args, "min_avg_volume", 8.0),
    )
    return {
        "variant": label,
        "target_method": config.target_method,
        "patterns": metrics["total"],
        "win_rate": metrics["win_rate"],
        "weighted_win_rate": metrics["weighted_win_rate"],
        "weighted_ev": metrics["weighted_ev"],
        "config": asdict(config),
    }


def main():
    parser = argparse.ArgumentParser(description="Target method A/B test")
    parser.add_argument("--universe-file", type=str, default=str(ROOT / "config" / "test_universe.yaml"))
    parser.add_argument("--period", type=str, default="2y")
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--include-wait", action="store_true")
    parser.add_argument("--min-days-to-outcome", type=int, default=1)
    parser.add_argument("--score-full", type=float, default=85.0)
    parser.add_argument("--score-half", type=float, default=70.0)
    parser.add_argument("--score-quarter", type=float, default=55.0)
    parser.add_argument("--score-min", type=float, default=55.0)
    parser.add_argument("--earnings-window-days", type=int, default=5)
    parser.add_argument("--max-hold-bars", type=int, default=20)
    parser.add_argument("--fallback-win-gain", type=float, default=5.0)
    parser.add_argument("--fallback-loss", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--min-avg-volume", type=float, default=1_000_000)
    parser.add_argument("--export", type=str, default="results/ab_test_targets.csv")
    parser.add_argument("--report-md", type=str, default="results/ab_test_targets.md")
    args = parser.parse_args()

    symbols = load_universe(Path(args.universe_file))
    methods = ["resistance", "measured_move", "fib", "auto"]
    results = []
    for method in methods:
        config = VolumePullbackConfig(
            min_confidence=args.min_confidence,
            target_method=method,
        )
        results.append(run_variant(symbols, args.period, config, args, method))

    df = pd.DataFrame(results)
    Path(args.export).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.export, index=False)

    md_lines = [
        "# Target Method A/B",
        "",
        f"- Universe: `{args.universe_file}`",
        f"- Period: `{args.period}`",
        "",
        "| Method | Patterns | Win Rate % | Weighted Win % | Weighted EV % |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, row in df.iterrows():
        md_lines.append(
            f"| {row['target_method']} | {int(row['patterns'])} | {row['win_rate']:.2f} | "
            f"{row['weighted_win_rate']:.2f} | {row['weighted_ev']:.2f} |"
        )
    report_path = Path(args.report_md)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(md_lines))
    print(f"✅ Exported target method A/B to: {args.export}")
    print(f"✅ Exported report to: {args.report_md}")


if __name__ == "__main__":
    main()
