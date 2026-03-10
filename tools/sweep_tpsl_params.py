#!/usr/bin/env python3
"""
Sweep TP/SL parameters to find combinations achieving >65% win rate and <5% drawdown.
Uses existing walk-forward validation logic without re-running picker.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/picker_config_relaxed.yaml")
    parser.add_argument("--months", default="3,6,9,12")
    parser.add_argument("--tp-range", default="10,12,15,18,20,25")
    parser.add_argument("--sl-range", default="-4,-5,-6,-8,-10")
    args = parser.parse_args()

    tp_vals = [float(x) for x in args.tp_range.split(",")]
    sl_vals = [float(x) for x in args.sl_range.split(",")]

    best = None
    results = []

    for tp in tp_vals:
        for sl in sl_vals:
            cmd = [
                sys.executable,
                str(ROOT / "tools" / "validate_three_layer_walkforward_tpsl.py"),
                "--config", args.config,
                "--months-back", args.months,
                "--db-only-prices",
                "--take-profit-pct", str(tp),
                "--stop-loss-pct", str(sl),
                "--output-md", f"results/picker/walkforward_report_tpsl_tp{tp}_sl{sl}.md",
                "--output-windows-csv", f"results/picker/walkforward_windows_tp{tp}_sl{sl}.csv",
                "--output-trades-csv", f"results/picker/walkforward_trades_tp{tp}_sl{sl}.csv",
            ]
            rc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
            if rc.returncode != 0:
                print(f"  TP={tp} SL={sl}: FAILED")
                continue

            # Parse report for win rate and drawdown
            md_path = ROOT / f"results/picker/walkforward_report_tpsl_tp{tp}_sl{sl}.md"
            if not md_path.exists():
                continue
            text = md_path.read_text()
            wr = None
            dd = None
            for line in text.splitlines():
                if "Win Rate" in line and "`" in line:
                    try:
                        pct = line.split("`")[1].replace("%", "")
                        wr = float(pct)
                    except (IndexError, ValueError):
                        pass
                if "max drawdown" in line.lower() and "`" in line:
                    try:
                        pct = line.split("`")[1].replace("%", "")
                        dd = float(pct)
                    except (IndexError, ValueError):
                        pass

            if wr is not None:
                results.append((tp, sl, wr, dd or 0))
                status = "✓" if wr >= 65 and (dd is None or dd < 5) else ""
                print(f"  TP={tp}% SL={sl}% -> WR={wr:.1f}% DD={dd or 0:.1f}% {status}")
                if wr >= 65 and (best is None or (dd or 99) < (best[3] or 99)):
                    best = (tp, sl, wr, dd)

    print("\n--- Best ---")
    if best:
        print(f"  TP={best[0]}% SL={best[1]}% -> WR={best[2]:.1f}% DD={best[3] or 0:.1f}%")
    else:
        print("  No combination achieved WR>=65% with DD<5%")
        # Show top 3 by win rate
        for r in sorted(results, key=lambda x: (-x[2], x[3] or 99))[:5]:
            print(f"  TP={r[0]}% SL={r[1]}% -> WR={r[2]:.1f}% DD={r[3] or 0:.1f}%")


if __name__ == "__main__":
    main()
