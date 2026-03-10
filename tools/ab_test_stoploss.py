#!/usr/bin/env python3
"""
A/B sweep for stop loss parameters.
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


def run_variant(symbols, period, config, args):
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
        "stop_loss_ema9_pct": config.stop_loss_ema9_pct,
        "stop_loss_atr_mult": config.stop_loss_atr_mult,
        "patterns": metrics["total"],
        "win_rate": metrics["win_rate"],
        "weighted_win_rate": metrics["weighted_win_rate"],
        "weighted_ev": metrics["weighted_ev"],
        "config": asdict(config),
    }


def main():
    parser = argparse.ArgumentParser(description="Stop loss A/B sweep")
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
    parser.add_argument("--ema9-pcts", type=str, default="0.95,0.96,0.97,0.98")
    parser.add_argument("--atr-mults", type=str, default="0.2,0.3,0.4")
    parser.add_argument("--export", type=str, default="results/ab_test_stoploss.csv")
    parser.add_argument("--report-md", type=str, default="results/ab_test_stoploss.md")
    args = parser.parse_args()

    symbols = load_universe(Path(args.universe_file))
    ema9_pcts = [float(x) for x in args.ema9_pcts.split(",")]
    atr_mults = [float(x) for x in args.atr_mults.split(",")]

    results = []
    for ema9_pct in ema9_pcts:
        for atr_mult in atr_mults:
            config = VolumePullbackConfig(
                min_confidence=args.min_confidence,
                stop_loss_ema9_pct=ema9_pct,
                stop_loss_atr_mult=atr_mult,
            )
            results.append(run_variant(symbols, args.period, config, args))

    df = pd.DataFrame(results).sort_values(["weighted_ev", "win_rate"], ascending=False)
    Path(args.export).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.export, index=False)

    md_lines = [
        "# Stop Loss A/B Sweep",
        "",
        f"- Universe: `{args.universe_file}`",
        f"- Period: `{args.period}`",
        "",
        "| EMA9 % | ATR Mult | Patterns | Win Rate % | Weighted Win % | Weighted EV % |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in df.iterrows():
        md_lines.append(
            f"| {row['stop_loss_ema9_pct']:.2f} | {row['stop_loss_atr_mult']:.2f} | "
            f"{int(row['patterns'])} | {row['win_rate']:.2f} | {row['weighted_win_rate']:.2f} | "
            f"{row['weighted_ev']:.2f} |"
        )
    report_path = Path(args.report_md)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(md_lines))
    print(f"✅ Exported stop-loss sweep to: {args.export}")
    print(f"✅ Exported report to: {args.report_md}")


if __name__ == "__main__":
    main()
