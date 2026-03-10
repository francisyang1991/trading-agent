"""
Shared logic for A/B testing Volume Pullback strategy variants.

Used by tools/ab_test_runner.py, ab_test_stoploss.py, ab_test_targets.py, ab_test_range_filter.py.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.tools.pattern_backtest import clean_symbols, scan_symbol
from src.signals.entry.volume_pullback_entry import VolumePullbackConfig, VolumePullbackEntrySignal


def load_universe(path: Path) -> List[str]:
    """Load symbol list from YAML universe file."""
    with open(path, "r") as f:
        universe = yaml.safe_load(f)
    return clean_symbols(universe.get("all_symbols", []))


def run_pattern_sweep(
    symbols: List[str],
    period: str,
    config: VolumePullbackConfig,
    min_confidence: float,
    min_days_to_outcome: int,
    include_wait: bool,
    max_hold_bars: int,
    fallback_win_gain: float,
    fallback_loss: float,
    score_full: float,
    score_half: float,
    score_quarter: float,
    score_min: float,
    earnings_window_days: int,
    max_workers: int = 8,
    min_avg_volume: float = 8.0,
) -> tuple[List[Any], Dict[str, float]]:
    """
    Run pattern backtest across symbols with given config.

    Returns:
        (filtered_patterns, metrics_dict)
        metrics_dict: win_rate, weighted_win_rate, weighted_ev, total, wins
    """
    signal_gen = VolumePullbackEntrySignal(config=config)
    patterns: List[Any] = []
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
                min_avg_volume,
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
    total_weight = sum(p.size_factor for p in filtered) or 0.0
    win_rate = (wins / total * 100) if total else 0.0
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
    metrics = {
        "win_rate": round(win_rate, 2),
        "weighted_win_rate": round(weighted_win_rate, 2),
        "weighted_ev": round(weighted_ev, 2),
        "total": total,
        "wins": wins,
    }
    return filtered, metrics
