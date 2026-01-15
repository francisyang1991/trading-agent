"""
Filter functions for stock universe.
"""

from typing import List, Optional
from .universe_manager import StockInfo, MarketCap


def filter_by_market_cap(
    stocks: List[StockInfo],
    min_cap: float = 0,
    max_cap: float = float('inf'),
    cap_classes: Optional[List[MarketCap]] = None
) -> List[StockInfo]:
    """
    Filter stocks by market cap.
    
    Args:
        stocks: List of stocks
        min_cap: Minimum market cap
        max_cap: Maximum market cap  
        cap_classes: Specific market cap classes to include
        
    Returns:
        Filtered list
    """
    filtered = []
    
    for stock in stocks:
        if stock.market_cap < min_cap or stock.market_cap > max_cap:
            continue
        
        if cap_classes and stock.market_cap_class not in cap_classes:
            continue
        
        filtered.append(stock)
    
    return filtered


def filter_by_volume(
    stocks: List[StockInfo],
    min_volume: int = 100000,
    max_volume: int = None
) -> List[StockInfo]:
    """Filter stocks by average volume."""
    filtered = []
    
    for stock in stocks:
        if stock.avg_volume < min_volume:
            continue
        if max_volume and stock.avg_volume > max_volume:
            continue
        filtered.append(stock)
    
    return filtered


def filter_by_price(
    stocks: List[StockInfo],
    min_price: float = 1.0,
    max_price: float = float('inf')
) -> List[StockInfo]:
    """Filter stocks by price."""
    return [
        s for s in stocks 
        if min_price <= s.price <= max_price
    ]


def filter_tradable(
    stocks: List[StockInfo],
    min_price: float = 5.0,
    min_volume: int = 300000,
    min_market_cap: float = 300_000_000
) -> List[StockInfo]:
    """
    Filter for tradable stocks (reasonable liquidity).
    
    Args:
        stocks: List of stocks
        min_price: Minimum price ($5 avoids penny stocks)
        min_volume: Minimum daily volume (300K for liquidity)
        min_market_cap: Minimum market cap ($300M)
        
    Returns:
        Tradable stocks
    """
    filtered = []
    
    for stock in stocks:
        if stock.price < min_price:
            continue
        if stock.avg_volume < min_volume:
            continue
        if stock.market_cap < min_market_cap:
            continue
        filtered.append(stock)
    
    return filtered


def filter_by_sector(
    stocks: List[StockInfo],
    sectors: List[str]
) -> List[StockInfo]:
    """Filter stocks by sector."""
    sectors_lower = [s.lower() for s in sectors]
    return [
        s for s in stocks 
        if s.sector.lower() in sectors_lower
    ]


def filter_by_theme(
    stocks: List[StockInfo],
    themes: List[str]
) -> List[StockInfo]:
    """Filter stocks by theme tags."""
    return [
        s for s in stocks 
        if any(t in s.themes for t in themes)
    ]


def filter_recovery_plays(
    stocks: List[StockInfo],
    min_drawdown: float = 40.0,
    min_recovery: float = 30.0
) -> List[StockInfo]:
    """
    Filter for recovery play stocks.
    
    Args:
        stocks: List of stocks
        min_drawdown: Minimum drawdown from 52w high (%)
        min_recovery: Minimum recovery from 52w low (%)
        
    Returns:
        Recovery play candidates
    """
    return [
        s for s in stocks 
        if s.max_drawdown >= min_drawdown and s.recovery_from_low >= min_recovery
    ]


def filter_near_highs(
    stocks: List[StockInfo],
    max_dist: float = 10.0
) -> List[StockInfo]:
    """Filter for stocks near 52-week highs."""
    return [s for s in stocks if s.dist_from_high <= max_dist]


def filter_beaten_down(
    stocks: List[StockInfo],
    min_dist: float = 30.0
) -> List[StockInfo]:
    """Filter for beaten down stocks (far from highs)."""
    return [s for s in stocks if s.dist_from_high >= min_dist]


def filter_profitable(stocks: List[StockInfo]) -> List[StockInfo]:
    """Filter for profitable companies."""
    return [
        s for s in stocks 
        if s.profit_margin is not None and s.profit_margin > 0
    ]


def filter_growing(
    stocks: List[StockInfo],
    min_growth: float = 0.05
) -> List[StockInfo]:
    """Filter for growing companies."""
    return [
        s for s in stocks 
        if s.revenue_growth is not None and s.revenue_growth >= min_growth
    ]
