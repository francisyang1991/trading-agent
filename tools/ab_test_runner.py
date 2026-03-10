#!/usr/bin/env python3
"""
A/B test runner for strategy changes (targets + partial exits).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

import pandas as pd
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.signals.entry.volume_pullback_entry import VolumePullbackConfig
from src.tools.ab_test_shared import load_universe, run_pattern_sweep


def run_variant(
    name: str,
    symbols: List[str],
    period: str,
    config: VolumePullbackConfig,
    args,
) -> Dict:
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
    )
    return {
        "variant": name,
        "patterns": metrics["total"],
        "win_rate": metrics["win_rate"],
        "weighted_win_rate": metrics["weighted_win_rate"],
        "weighted_ev": metrics["weighted_ev"],
        "config": asdict(config),
    }


def main():
    parser = argparse.ArgumentParser(description="A/B test runner for strategy updates")
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
    parser.add_argument("--export", type=str, default="results/ab_test_summary.csv")
    parser.add_argument("--report-md", type=str, default="results/ab_test_summary.md")
    parser.add_argument("--max-target1-pct", type=float, default=12.0)
    args = parser.parse_args()

    symbols = load_universe(Path(args.universe_file))

    variants = [
        ("old_target_no_partial", None, False),
        ("old_target_partial", None, True),
        ("capped_target_no_partial", args.max_target1_pct, False),
        ("capped_target_partial", args.max_target1_pct, True),
    ]

    results = []
    for name, cap, partial in variants:
        config = VolumePullbackConfig(
            min_confidence=args.min_confidence,
            max_target1_pct=cap,
            partial_exit_at_target1=partial,
        )
        results.append(
            run_variant(
                name=name,
                symbols=symbols,
                period=args.period,
                config=config,
                args=args,
            )
        )

    df = pd.DataFrame(results)
    Path(args.export).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.export, index=False)
    print(df[["variant", "patterns", "win_rate", "weighted_win_rate", "weighted_ev"]])
    print(f"✅ Exported A/B summary to: {args.export}")
    
    if args.report_md:
        md_lines = [
            "# A/B Test Summary",
            "",
            f"- Universe: `{args.universe_file}`",
            f"- Period: `{args.period}`",
            f"- Min confidence: `{args.min_confidence}`",
            f"- Include WAIT: `{args.include_wait}`",
            f"- Min days to outcome: `{args.min_days_to_outcome}`",
            f"- Earnings window days: `{args.earnings_window_days}`",
            "",
            "## Results",
            "",
            "| Variant | Patterns | Win Rate % | Weighted Win % | Weighted EV % |",
            "|---|---:|---:|---:|---:|",
        ]
        for _, row in df.iterrows():
            md_lines.append(
                f"| {row['variant']} | {int(row['patterns'])} | {row['win_rate']:.2f} | "
                f"{row['weighted_win_rate']:.2f} | {row['weighted_ev']:.2f} |"
            )
        report_path = Path(args.report_md)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(md_lines))
        print(f"✅ Exported A/B markdown report to: {report_path}")


if __name__ == "__main__":
    main()
