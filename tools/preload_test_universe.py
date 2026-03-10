#!/usr/bin/env python3
"""
Preload cached price data for the test universe.
Uses DataManager SQLite cache to speed up A/B runs.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_manager import DataManager
from src.tools.pattern_backtest import clean_symbols


def load_symbols(path: Path):
    with open(path, "r") as f:
        universe = yaml.safe_load(f)
    return clean_symbols(universe.get("all_symbols", []))


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Preload test universe data cache")
    parser.add_argument("--universe-file", type=str, default=str(ROOT / "config" / "test_universe.yaml"))
    parser.add_argument("--period", type=str, default="2y")
    args = parser.parse_args()

    symbols = load_symbols(Path(args.universe_file))
    dm = DataManager()
    dm.preload_symbols(symbols, period=args.period)

    meta = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d"),
        "universe_file": str(Path(args.universe_file)),
        "period": args.period,
        "symbols": len(symbols),
    }
    out_path = ROOT / "data" / "universe_snapshots" / "test_universe_cache.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        yaml.safe_dump(meta, f, sort_keys=False)
    print(f"✅ Preloaded {len(symbols)} symbols to cache")
    print(f"✅ Wrote cache metadata to {out_path}")


if __name__ == "__main__":
    main()
