"""
Universe Management Module - Scan and filter the entire US stock market.

This module provides:
- Full US market scanning
- Advanced fundamental analysis (ROIC, FCF, Piotroski, Altman Z)
- Recovery pattern detection (drawdown + bounce)
- Theme categorization
"""

from .universe_manager import (
    UniverseManager,
    get_all_us_stocks,
    get_tradable_universe,
    StockInfo,
    MarketCap,
    THEME_KEYWORDS,
)
from .screener import (
    FundamentalScreener,
    RecoveryScreener,
    MomentumScreener,
    UndervaluedScreener,
    ComprehensiveScreener,
    ScreenResult,
)
from .filters import (
    filter_by_market_cap,
    filter_by_volume,
    filter_by_price,
    filter_tradable,
    filter_recovery_plays,
    filter_by_theme,
)
from .fundamentals import (
    FundamentalAnalyzer,
    FundamentalMetrics,
    print_fundamental_report,
)

__all__ = [
    'UniverseManager',
    'get_all_us_stocks',
    'get_tradable_universe',
    'FundamentalScreener',
    'RecoveryScreener',
    'MomentumScreener',
    'UndervaluedScreener',
    'filter_by_market_cap',
    'filter_by_volume',
    'filter_by_price',
    'filter_tradable',
]
