"""
Post-processing for picker results: share-class dedup and sector diversification.

Usage:
    from src.picker.post_processor import deduplicate_share_classes, diversify_by_industry

    deduped = deduplicate_share_classes(picks_df)
    diversified = diversify_by_industry(deduped, max_per_industry=2)
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Share-class families
# ---------------------------------------------------------------------------

_KNOWN_ROOTS = {
    "GOOG": ["GOOGL", "GOOG"],
    "BRK": ["BRK-A", "BRK-B", "BRK.A", "BRK.B"],
    "HEI": ["HEI", "HEI-A", "HEI.A"],
    "BEL": ["BELFB", "BELFA"],
    "FOX": ["FOX", "FOXA"],
    "LSXM": ["LSXMA", "LSXMB", "LSXMK"],
    "LEN": ["LEN", "LEN-B", "LEN.B"],
    "NEWS": ["NWSA", "NWS"],
    "DISC": ["DISCA", "DISCB", "DISCK"],
}

_TICKER_TO_ROOT: Dict[str, str] = {}
for _root, _variants in _KNOWN_ROOTS.items():
    for _v in _variants:
        _TICKER_TO_ROOT[_v.upper()] = _root


def _canonical_root(ticker: str) -> str:
    """Map a ticker to its canonical company root, collapsing share classes."""
    t = ticker.upper().strip()
    if t in _TICKER_TO_ROOT:
        return _TICKER_TO_ROOT[t]
    # Heuristic: strip trailing single letter after dot/dash  (e.g. HEI.A -> HEI)
    m = re.match(r"^([A-Z]{2,})[\.\-]([A-Z])$", t)
    if m:
        return m.group(1)
    return t


def deduplicate_share_classes(
    df: pd.DataFrame,
    score_col: str = "composite",
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Keep only the highest-scoring share class per company.

    Returns:
        (deduped_df, list_of_removed_tickers)
    """
    df = df.copy()
    df["_root"] = df["ticker"].apply(_canonical_root)
    idx = df.groupby("_root")[score_col].idxmax()
    deduped = df.loc[idx].drop(columns=["_root"]).reset_index(drop=True)
    removed = sorted(set(df["ticker"]) - set(deduped["ticker"]))
    return deduped, removed


def diversify_by_industry(
    df: pd.DataFrame,
    industry_map: Dict[str, str],
    max_per_industry: int = 2,
    score_col: str = "composite",
) -> Tuple[pd.DataFrame, List[Tuple[str, str]]]:
    """
    Cap the number of picks per industry.

    Args:
        df: DataFrame with 'ticker' and score_col columns.
        industry_map: ticker -> industry string mapping.
        max_per_industry: maximum picks per industry.
        score_col: column to rank by.

    Returns:
        (diversified_df, list_of (removed_ticker, industry) tuples)
    """
    df = df.sort_values(score_col, ascending=False).copy()
    kept_rows = []
    removed: List[Tuple[str, str]] = []
    counts: Dict[str, int] = defaultdict(int)

    for _, row in df.iterrows():
        ind = industry_map.get(row["ticker"], "Unknown")
        if counts[ind] < max_per_industry:
            kept_rows.append(row)
            counts[ind] += 1
        else:
            removed.append((row["ticker"], ind))

    return pd.DataFrame(kept_rows).reset_index(drop=True), removed
