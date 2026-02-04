#!/usr/bin/env python3
"""
Prune symbols with no price data from a universe file.
Writes a new universe YAML with missing symbols removed.
"""
from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager
from src.tools.pattern_backtest import clean_symbols


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Prune missing symbols from universe")
    parser.add_argument("--input", type=str, default=str(ROOT / "config" / "stock_universe_large.yaml"))
    parser.add_argument("--output", type=str, default=str(ROOT / "config" / "stock_universe_large_pruned.yaml"))
    parser.add_argument("--period", type=str, default="2y")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    with open(input_path, "r") as f:
        universe = yaml.safe_load(f)

    symbols = clean_symbols(universe.get("all_symbols", []))
    dm = DataManager()
    keep = []
    missing = []
    for s in symbols:
        data = dm.get_daily_data(s, period=args.period)
        if data is None or data.empty:
            missing.append(s)
        else:
            keep.append(s)

    universe["all_symbols"] = keep
    if "themes" in universe:
        for theme, data in universe["themes"].items():
            theme_syms = clean_symbols(data.get("symbols", []))
            universe["themes"][theme]["symbols"] = [s for s in theme_syms if s in keep]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.safe_dump(universe, f, sort_keys=False)

    print(f"✅ Pruned universe written to: {output_path}")
    print(f"Removed {len(missing)} missing symbols")


if __name__ == "__main__":
    main()
