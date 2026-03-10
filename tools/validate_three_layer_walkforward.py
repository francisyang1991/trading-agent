#!/usr/bin/env python3
"""
Hold-to-end walk-forward validation (no TP/SL).

Thin wrapper around validate_three_layer_walkforward_tpsl.py with TP=0, SL=0.
Outputs to walkforward_report.md (not _tpsl.md) for backward compatibility.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPSL_SCRIPT = ROOT / "tools" / "validate_three_layer_walkforward_tpsl.py"


def main() -> None:
    # Build args: TP=0, SL=0 (hold to end), non-tpsl output paths
    cmd = [
        sys.executable,
        str(TPSL_SCRIPT),
        "--take-profit-pct", "0",
        "--stop-loss-pct", "0",
        "--output-windows-csv", "results/picker/walkforward_windows_report.csv",
        "--output-trades-csv", "results/picker/walkforward_trades_report.csv",
        "--output-md", "results/picker/walkforward_report.md",
    ]
    # Forward user args, skipping any --take-profit-pct / --stop-loss-pct / --output-*
    skip_next = False
    for i, arg in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--take-profit-pct", "--stop-loss-pct",
                   "--output-windows-csv", "--output-trades-csv", "--output-md"):
            skip_next = True
            continue
        cmd.append(arg)
    sys.exit(subprocess.run(cmd, cwd=str(ROOT)).returncode)


if __name__ == "__main__":
    main()
