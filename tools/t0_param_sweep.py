#!/usr/bin/env python3
"""
Parameter sweep for intraday T0 strategy.
Tests combinations of z-score, vwap distance, satellite %, cooldown.
Uses hourly picker for stock selection, then runs 1m replay per combo.
Outputs: sweep_results.csv with all combos ranked by avg alpha.
"""

from __future__ import annotations

import argparse
import itertools
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _write_temp_config(base_config: dict, overrides: dict, out_path: Path) -> None:
    cfg = _deep_copy_dict(base_config)
    for k, v in overrides.items():
        parts = k.split(".")
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = v
    out_path.write_text(yaml.safe_dump(cfg, sort_keys=False))


def _deep_copy_dict(d: dict) -> dict:
    import copy
    return copy.deepcopy(d)


def _run_replay(symbol: str, config_path: str, days: int, core_qty: int, equity: float, out_dir: str) -> dict | None:
    cmd = [
        sys.executable, str(PROJECT_ROOT / "tools" / "replay_intraday_t0_1m.py"),
        "--symbol", symbol,
        "--days", str(days),
        "--core-qty", str(core_qty),
        "--initial-equity", str(equity),
        "--config", config_path,
        "--output-dir", out_dir,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(PROJECT_ROOT))
    except Exception:
        return None

    metrics_path = Path(out_dir) / "metrics_summary.csv"
    if not metrics_path.exists():
        return None
    df = pd.read_csv(metrics_path)
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="T0 parameter sweep")
    parser.add_argument("--symbols", default="AMD,TSLA,NVDA,PLTR,CAT", help="Comma-separated symbols (hourly-picked)")
    parser.add_argument("--days", type=int, default=8)
    parser.add_argument("--core-qty", type=int, default=50)
    parser.add_argument("--initial-equity", type=float, default=100000)
    parser.add_argument("--output", default="outputs/intraday_t0_replay/sweep_results.csv")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    base_config = yaml.safe_load((PROJECT_ROOT / "config" / "intraday_t0_config.yaml").read_text())

    # Parameter grid
    zscore_vals = [2.0, 2.4, 2.8, 3.2]
    vwap_dist_vals = [35.0, 55.0, 80.0, 100.0]
    satellite_pcts = [0.30, 0.40, 0.50, 0.60, 0.70]
    cooldown_hold = [
        (6, 4, 12),    # loose
        (10, 6, 8),    # medium
        (14, 8, 6),    # tight
    ]

    combos = list(itertools.product(zscore_vals, vwap_dist_vals, satellite_pcts, cooldown_hold))
    print(f"Total combos: {len(combos)} x {len(symbols)} symbols = {len(combos) * len(symbols)} runs")

    tmp_dir = Path(tempfile.mkdtemp(prefix="t0_sweep_"))
    results = []

    for ci, (zs, vd, sat, (cooldown, hold, max_trades)) in enumerate(combos):
        cfg_path = tmp_dir / f"cfg_{ci}.yaml"
        overrides = {
            "strategy.zscore_entry_buy": -zs,
            "strategy.zscore_entry_sell": zs,
            "strategy.zscore_exit_to_mean": max(0.5, zs * 0.35),
            "risk.satellite_max_pct_of_core": sat,
            "replay.min_vwap_distance_bps": vd,
            "replay.min_bars_between_trades": cooldown,
            "replay.min_hold_bars": hold,
            "replay.max_trades_per_day": max_trades,
            "replay.min_entry_rvol": 0.0,
        }
        _write_temp_config(base_config, overrides, cfg_path)

        combo_alphas, combo_sharpes, combo_trades, combo_dds = [], [], [], []
        for s in symbols:
            out_d = str(tmp_dir / f"run_{ci}_{s}")
            m = _run_replay(s, str(cfg_path), args.days, args.core_qty, args.initial_equity, out_d)
            if m is None:
                continue
            combo_alphas.append(float(m.get("alpha_pct", 0)))
            combo_sharpes.append(float(m.get("sharpe", 0)))
            combo_trades.append(int(m.get("trade_count", 0)))
            combo_dds.append(float(m.get("max_dd", 0)))

        if not combo_alphas:
            continue

        results.append({
            "zscore": zs,
            "vwap_dist_bps": vd,
            "satellite_pct": sat,
            "cooldown_bars": cooldown,
            "hold_bars": hold,
            "max_trades_day": max_trades,
            "avg_alpha_pct": np.mean(combo_alphas),
            "avg_sharpe": np.mean(combo_sharpes),
            "avg_trades": np.mean(combo_trades),
            "avg_max_dd": np.mean(combo_dds),
            "min_alpha": np.min(combo_alphas),
            "max_alpha": np.max(combo_alphas),
            "pct_positive_alpha": np.mean([a > 0 for a in combo_alphas]) * 100,
        })

        done = ci + 1
        if done % 10 == 0 or done == len(combos):
            print(f"  [{done}/{len(combos)}] combos done ...")

    df = pd.DataFrame(results)
    df = df.sort_values("avg_alpha_pct", ascending=False)
    out_path = PROJECT_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, float_format="%.4f")
    print(f"\nSweep done. {len(df)} combos. Results: {out_path}")
    print("\nTop 10 by avg alpha:")
    print(df.head(10).to_string(index=False))
    print("\nTop 5 balanced (alpha > 0, max_dd < 5, positive_alpha% >= 40):")
    balanced = df[(df["avg_alpha_pct"] > 0) & (df["avg_max_dd"] < 5) & (df["pct_positive_alpha"] >= 40)]
    if not balanced.empty:
        print(balanced.head(5).to_string(index=False))
    else:
        print("(none matched balanced criteria)")

    shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
