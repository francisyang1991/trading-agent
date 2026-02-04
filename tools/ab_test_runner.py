#!/usr/bin/env python3
"""
A/B test runner for strategy changes (targets + partial exits).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yaml
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.pattern_backtest import scan_symbol, clean_symbols
from src.signals.entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig



def load_universe(path: Path) -> List[str]:
    with open(path, "r") as f:
        universe = yaml.safe_load(f)
    symbols = universe.get("all_symbols", [])
    return clean_symbols(symbols)


def run_variant(
    name: str,
    symbols: List[str],
    period: str,
    config: VolumePullbackConfig,
    min_confidence: float,
    include_wait: bool,
    min_days_to_outcome: int,
    score_full: float,
    score_half: float,
    score_quarter: float,
    score_min: float,
    earnings_window_days: int,
    max_hold_bars: int,
    fallback_win_gain: float,
    fallback_loss: float,
    max_workers: int,
) -> Dict:
    signal_gen = VolumePullbackEntrySignal(config=config)
    patterns = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                scan_symbol,
                s,
                period,
                signal_gen,
                min_confidence,
                5,
                max_hold_bars,
                include_wait,
                0.0,
                fallback_win_gain,
                fallback_loss,
                True,
                score_full,
                score_half,
                score_quarter,
                score_min,
                earnings_window_days,
                30,
                False,
                8.0,
            ): s
            for s in symbols
        }
        for future in as_completed(futures):
            _, rows = future.result()
            patterns.extend(rows)
    filtered = [
        p for p in patterns
        if p.outcome in ("SUCCESS", "FAILURE") and p.days_to_outcome >= min_days_to_outcome
    ]
    total = len(filtered)
    wins = sum(1 for p in filtered if p.outcome == "SUCCESS")
    win_rate = (wins / total * 100) if total else 0.0
    total_weight = sum(p.size_factor for p in filtered) or 0.0
    weighted_win_rate = (
        sum(p.size_factor for p in filtered if p.outcome == "SUCCESS") / total_weight * 100
        if total_weight
        else 0.0
    )
    weighted_ev = (
        sum(p.weighted_outcome_pct for p in filtered) / total_weight
        if total_weight
        else 0.0
    )
    return {
        "variant": name,
        "patterns": total,
        "win_rate": round(win_rate, 2),
        "weighted_win_rate": round(weighted_win_rate, 2),
        "weighted_ev": round(weighted_ev, 2),
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
                min_confidence=args.min_confidence,
                include_wait=args.include_wait,
                min_days_to_outcome=args.min_days_to_outcome,
                score_full=args.score_full,
                score_half=args.score_half,
                score_quarter=args.score_quarter,
                score_min=args.score_min,
                earnings_window_days=args.earnings_window_days,
                max_hold_bars=args.max_hold_bars,
                fallback_win_gain=args.fallback_win_gain,
                fallback_loss=args.fallback_loss,
                max_workers=args.max_workers,
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
