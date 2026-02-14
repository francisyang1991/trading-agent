"""Filter functions for stock universe."""

from typing import List, Optional
import pandas as pd
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


def technical_filter(
    factor_df: pd.DataFrame,
    rs_threshold: float = 80.0,
    near_high_threshold: float = 0.85,
) -> pd.DataFrame:
    """
    Technical layer:
    - close above 200MA
    - near 52-week high
    - RS rank threshold
    """
    if factor_df is None or factor_df.empty:
        return pd.DataFrame()
    df = factor_df.copy()
    required = {"adj_close", "rs_rank", "above_200ma", "near_52w"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"technical_filter missing columns: {sorted(missing)}")

    if "near_52w" not in df.columns and {"adj_close", "high_52w"}.issubset(df.columns):
        df["near_52w"] = df["adj_close"] >= (df["high_52w"] * near_high_threshold)

    return df[
        (df["above_200ma"].astype(bool))
        & (df["near_52w"].astype(bool))
        & (df["rs_rank"] >= rs_threshold)
    ].copy()


def fundamental_filter(
    factor_df: pd.DataFrame,
    min_eps_yoy: float = 0.25,
    min_revenue_growth: float = 0.10,
    min_revenue_acceleration: Optional[float] = None,
    require_surprise_non_negative: bool = False,
) -> pd.DataFrame:
    """
    Fundamental layer:
    - EPS YoY growth
    - revenue growth
    - optional analyst surprise floor
    """
    if factor_df is None or factor_df.empty:
        return pd.DataFrame()
    df = factor_df.copy()
    for col in ("eps_yoy", "revenue_growth"):
        if col not in df.columns:
            raise ValueError(f"fundamental_filter missing column: {col}")

    out = df[(df["eps_yoy"] >= min_eps_yoy) & (df["revenue_growth"] >= min_revenue_growth)].copy()
    if min_revenue_acceleration is not None:
        if "revenue_acceleration" not in out.columns:
            raise ValueError("fundamental_filter missing column: revenue_acceleration")
        out = out[out["revenue_acceleration"] >= min_revenue_acceleration]
    if require_surprise_non_negative and "earnings_surprise" in out.columns:
        out = out[out["earnings_surprise"] >= 0]
    return out


def moat_quality_filter(
    factor_df: pd.DataFrame,
    min_roe: float = 0.10,
    min_gm_rank: float = 60.0,
    max_debt_to_equity: Optional[float] = None,
) -> pd.DataFrame:
    """
    Moat/quality layer:
    - ROE floor
    - gross margin percentile floor
    - optional debt ceiling
    """
    if factor_df is None or factor_df.empty:
        return pd.DataFrame()
    df = factor_df.copy()
    for col in ("roe", "gm_rank"):
        if col not in df.columns:
            raise ValueError(f"moat_quality_filter missing column: {col}")
    out = df[(df["roe"] >= min_roe) & (df["gm_rank"] >= min_gm_rank)].copy()
    if max_debt_to_equity is not None and "debt_to_equity" in out.columns:
        de = pd.to_numeric(out["debt_to_equity"], errors="coerce")
        # Some providers report D/E as percent-like values (e.g., 16 means 0.16x).
        de = de.where(de <= 5, de / 100.0)
        out = out[de <= max_debt_to_equity]
    return out


def build_technical_base_from_prices(
    prices_by_symbol: dict,
    benchmark_return_252: float,
    near_52w_ratio: float = 0.85,
    min_bars: int = 260,
    as_of_date: Optional["date"] = None,
) -> pd.DataFrame:
    """
    Build base technical feature table from OHLCV dict.

    Args:
        prices_by_symbol: {ticker: DataFrame with Close column}
        benchmark_return_252: SPY 252-day return for RS computation.
        near_52w_ratio: fraction of 52w high to qualify as "near high".
        min_bars: minimum price bars required.
        as_of_date: if set, truncate each price series to this date
                    (for historical lookback with no lookahead).
    """
    import datetime as _dt

    rows = []
    eval_date = as_of_date or pd.Timestamp.today().date()

    for sym, d in prices_by_symbol.items():
        try:
            df = d.copy()
            # Truncate to as_of_date if specified (no lookahead).
            if as_of_date is not None:
                if "Date" in df.columns:
                    df = df[pd.to_datetime(df["Date"]).dt.date <= as_of_date]
                elif isinstance(df.index, pd.DatetimeIndex):
                    df = df[df.index.date <= as_of_date]

            close = df["Close"]
            if close is None or len(close) < min_bars:
                continue
            price = float(close.iloc[-1])
            ma200 = float(close.rolling(200).mean().iloc[-1])
            high_52w = float(close.rolling(252).max().iloc[-1])
            if not pd.notna(ma200) or not pd.notna(high_52w) or high_52w <= 0:
                continue
            stock_ret_252 = float(close.iloc[-1] / close.iloc[-252] - 1)
            rs_raw = stock_ret_252 - benchmark_return_252
            rows.append(
                {
                    "ticker": sym,
                    "date": eval_date,
                    "adj_close": price,
                    "above_200ma": price > ma200,
                    "near_52w": price >= high_52w * near_52w_ratio,
                    "rs_raw": rs_raw,
                }
            )
        except Exception:
            continue

    base = pd.DataFrame(rows)
    if base.empty:
        return base
    base["rs_rank"] = (base["rs_raw"].rank(pct=True) * 98 + 1).round(2)
    return base
