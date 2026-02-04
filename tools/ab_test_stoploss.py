#!/usr/bin/env python3
"""
A/B sweep for stop loss parameters.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import List
import sys

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.pattern_backtest import scan_symbol, clean_symbols
from src.signals.entry.volume_pullback_entry import VolumePullbackEntrySignal, VolumePullbackConfig
from concurrent.futures import ThreadPoolExecutor, as_completed


def load_universe(path: Path) -> List[str]:
    with open(path, "r") as f:
        universe = yaml.safe_load(f)
    return clean_symbols(universe.get("all_symbols", []))


def run_variant(symbols, period, config, args):
    signal_gen = VolumePullbackEntrySignal(config=config)
    patterns = []
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(
                scan_symbol,
                s,
                period,
                signal_gen,
                args.min_confidence,
                5,
                args.max_hold_bars,
                args.include_wait,
                0.0,
                args.fallback_win_gain,
                args.fallback_loss,
                True,
                args.score_full,
                args.score_half,
                args.score_quarter,
                args.score_min,
                args.earnings_window_days,
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
        if p.outcome in ("SUCCESS", "FAILURE") and p.days_to_outcome >= args.min_days_to_outcome
    ]
    total = len(filtered)
    wins = sum(1 for p in filtered if p.outcome == "SUCCESS")
    win_rate = (wins / total * 100) if total else 0.0
    total_weight = sum(p.size_factor for p in filtered) or 0.0
    weighted_win_rate = (
        sum(p.size_factor for p in filtered if p.outcome == "SUCCESS") / total_weight * 100
        if total_weight else 0.0
    )
    weighted_ev = (
        sum(p.weighted_outcome_pct for p in filtered) / total_weight
        if total_weight else 0.0
    )
    return {
        "stop_loss_ema9_pct": config.stop_loss_ema9_pct,
        "stop_loss_atr_mult": config.stop_loss_atr_mult,
        "patterns": total,
        "win_rate": round(win_rate, 2),
        "weighted_win_rate": round(weighted_win_rate, 2),
        "weighted_ev": round(weighted_ev, 2),
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
