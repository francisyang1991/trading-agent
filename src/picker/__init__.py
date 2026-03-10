"""
Stock Picker Module - Systematic stock selection.

This module provides:
- Multi-factor stock scoring
- Ranking and selection
- Entry zone calculation
- Expected value analysis
- Post-processing (dedup, diversification)
- Historical lookback (no-lookahead comparison)
"""

from .stock_picker import StockPicker, pick_stocks
from .ranking import RankingEngine, rank_picks
from .fundamentals_service import FundamentalSnapshotService
from .post_processor import deduplicate_share_classes, diversify_by_industry
from .lookback import run_picker_at_date, compare_across_dates
from .price_loader import stage_load_prices

__all__ = [
    'StockPicker',
    'pick_stocks',
    'RankingEngine',
    'rank_picks',
    'FundamentalSnapshotService',
    'deduplicate_share_classes',
    'diversify_by_industry',
    'run_picker_at_date',
    'compare_across_dates',
    'stage_load_prices',
]
