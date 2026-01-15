"""
Universe Manager - Scan and manage the entire US stock market.

Features:
1. Get all tradable US stocks (NYSE, NASDAQ, AMEX)
2. Filter by market cap, volume, price
3. Categorize by sector/theme
4. Identify "recovery champions" - stocks that bounced from major drawdowns
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')

logger = logging.getLogger(__name__)


class MarketCap(Enum):
    """Market cap classification."""
    MEGA = "MEGA"           # >$200B
    LARGE = "LARGE"         # $10B-$200B
    MID = "MID"             # $2B-$10B
    SMALL = "SMALL"         # $300M-$2B
    MICRO = "MICRO"         # <$300M


class Sector(Enum):
    """Stock sector classification."""
    TECHNOLOGY = "Technology"
    HEALTHCARE = "Healthcare"
    FINANCIALS = "Financials"
    CONSUMER_CYCLICAL = "Consumer Cyclical"
    CONSUMER_DEFENSIVE = "Consumer Defensive"
    INDUSTRIALS = "Industrials"
    ENERGY = "Energy"
    UTILITIES = "Utilities"
    REAL_ESTATE = "Real Estate"
    MATERIALS = "Materials"
    COMMUNICATION = "Communication Services"
    UNKNOWN = "Unknown"


@dataclass
class StockInfo:
    """Basic stock information."""
    symbol: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0
    market_cap_class: MarketCap = MarketCap.MICRO
    price: float = 0.0
    avg_volume: int = 0
    
    # Fundamentals (if available)
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    profit_margin: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    revenue_growth: Optional[float] = None
    earnings_growth: Optional[float] = None
    
    # Price metrics
    high_52w: float = 0.0
    low_52w: float = 0.0
    dist_from_high: float = 0.0
    dist_from_low: float = 0.0
    
    # Recovery metrics
    max_drawdown: float = 0.0
    recovery_from_low: float = 0.0
    is_recovery_play: bool = False
    
    # Theme tags
    themes: List[str] = field(default_factory=list)


# =============================================================================
# THEME DEFINITIONS
# =============================================================================

THEME_KEYWORDS = {
    'ai_chips': {
        'keywords': ['semiconductor', 'chip', 'gpu', 'ai hardware', 'processor'],
        'symbols': ['NVDA', 'AMD', 'INTC', 'AVGO', 'QCOM', 'MU', 'MRVL', 'ARM', 'TSM', 'ASML'],
    },
    'ai_software': {
        'keywords': ['artificial intelligence', 'machine learning', 'ai platform'],
        'symbols': ['MSFT', 'GOOGL', 'META', 'PLTR', 'AI', 'PATH', 'SOUN', 'BBAI'],
    },
    'cloud': {
        'keywords': ['cloud computing', 'cloud infrastructure', 'saas'],
        'symbols': ['AMZN', 'MSFT', 'GOOGL', 'CRM', 'NOW', 'SNOW', 'DDOG', 'NET', 'MDB'],
    },
    'cybersecurity': {
        'keywords': ['cybersecurity', 'security software', 'network security'],
        'symbols': ['CRWD', 'PANW', 'ZS', 'FTNT', 'S', 'CYBR', 'OKTA'],
    },
    'space': {
        'keywords': ['space', 'satellite', 'rocket', 'aerospace'],
        'symbols': ['RKLB', 'SPCE', 'ASTS', 'LUNR', 'RDW', 'IRDM', 'GSAT'],
    },
    'ev_auto': {
        'keywords': ['electric vehicle', 'ev', 'autonomous', 'battery'],
        'symbols': ['TSLA', 'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'QS', 'CHPT'],
    },
    'crypto_blockchain': {
        'keywords': ['bitcoin', 'crypto', 'blockchain', 'digital asset'],
        'symbols': ['COIN', 'MSTR', 'MARA', 'RIOT', 'CLSK', 'IREN', 'HUT', 'BITF'],
    },
    'clean_energy': {
        'keywords': ['solar', 'wind', 'renewable', 'clean energy'],
        'symbols': ['ENPH', 'FSLR', 'SEDG', 'RUN', 'PLUG', 'BE', 'NEE'],
    },
    'nuclear': {
        'keywords': ['nuclear', 'uranium', 'smr'],
        'symbols': ['SMR', 'OKLO', 'CCJ', 'UEC', 'LEU', 'NNE'],
    },
    'quantum': {
        'keywords': ['quantum computing', 'quantum'],
        'symbols': ['IONQ', 'RGTI', 'QBTS', 'QUBT'],
    },
    'biotech': {
        'keywords': ['biotechnology', 'drug development', 'pharmaceutical'],
        'symbols': ['MRNA', 'BIIB', 'VRTX', 'REGN', 'ILMN', 'ALNY'],
    },
    'fintech': {
        'keywords': ['fintech', 'payment', 'digital banking'],
        'symbols': ['V', 'MA', 'PYPL', 'SQ', 'AFRM', 'SOFI', 'HOOD', 'NU'],
    },
    'mag7': {
        'keywords': [],
        'symbols': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA'],
    },
    'robotics': {
        'keywords': ['robotics', 'automation', 'robot'],
        'symbols': ['ISRG', 'ROK', 'TER', 'PATH'],
    },
    'defense': {
        'keywords': ['defense', 'military', 'aerospace defense'],
        'symbols': ['LMT', 'RTX', 'NOC', 'GD', 'BA', 'AXON', 'LHX'],
    },
}


# =============================================================================
# MARKET DATA SOURCES
# =============================================================================

def get_sp500_symbols() -> List[str]:
    """Get S&P 500 symbols from Wikipedia."""
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        tables = pd.read_html(url)
        df = tables[0]
        return df['Symbol'].str.replace('.', '-', regex=False).tolist()
    except Exception as e:
        logger.warning(f"Failed to get S&P 500 list: {e}")
        return []


def get_nasdaq100_symbols() -> List[str]:
    """Get NASDAQ 100 symbols."""
    try:
        url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
        tables = pd.read_html(url)
        # Find the table with Company column
        for table in tables:
            if 'Ticker' in table.columns:
                return table['Ticker'].tolist()
            if 'Symbol' in table.columns:
                return table['Symbol'].tolist()
        return []
    except Exception as e:
        logger.warning(f"Failed to get NASDAQ 100 list: {e}")
        return []


def get_all_us_stocks() -> List[str]:
    """
    Get comprehensive list of US tradable stocks.
    
    Combines multiple sources:
    - S&P 500
    - NASDAQ 100
    - Additional well-known stocks
    - Theme-based stocks
    
    Returns:
        List of unique symbols
    """
    symbols = set()
    
    # S&P 500
    sp500 = get_sp500_symbols()
    symbols.update(sp500)
    logger.info(f"Added {len(sp500)} S&P 500 symbols")
    
    # NASDAQ 100
    nasdaq100 = get_nasdaq100_symbols()
    symbols.update(nasdaq100)
    logger.info(f"Added {len(nasdaq100)} NASDAQ 100 symbols")
    
    # Add theme-based symbols
    for theme, data in THEME_KEYWORDS.items():
        symbols.update(data.get('symbols', []))
    
    # Add additional well-known stocks not in indices
    additional = [
        # Mid-cap growth
        'APP', 'CELH', 'DUOL', 'ONON', 'HIMS', 'AXON', 'DECK',
        # Volatile momentum
        'SMCI', 'IREN', 'RKLB', 'FLNC', 'IONQ', 'RGTI', 'OKLO', 'SMR',
        # Crypto/Bitcoin
        'MSTR', 'MARA', 'RIOT', 'CLSK', 'HUT', 'BITF', 'CIFR',
        # Space
        'LUNR', 'RDW', 'ASTS',
        # EV/Clean
        'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'QS', 'CHPT',
        # Fintech
        'AFRM', 'SOFI', 'HOOD', 'UPST', 'NU',
        # China ADRs
        'BABA', 'JD', 'PDD', 'BIDU', 'NIO', 'XPEV', 'LI',
        # Popular retail
        'GME', 'AMC', 'PLTR', 'DKNG',
    ]
    symbols.update(additional)
    
    # Clean up
    symbols = {s.upper().strip() for s in symbols if s and isinstance(s, str)}
    symbols = {s for s in symbols if len(s) <= 5 and s.isalpha()}  # Valid tickers only
    
    logger.info(f"Total unique symbols: {len(symbols)}")
    return sorted(list(symbols))


def get_tradable_universe(
    min_price: float = 5.0,
    min_volume: int = 500000,
    min_market_cap: float = 500_000_000,  # $500M
    max_symbols: int = 1000
) -> List[str]:
    """
    Get filtered universe of tradable stocks.
    
    Args:
        min_price: Minimum price
        min_volume: Minimum average volume
        min_market_cap: Minimum market cap
        max_symbols: Maximum symbols to return
        
    Returns:
        List of filtered symbols
    """
    all_symbols = get_all_us_stocks()
    
    # For now, return all - filtering happens in UniverseManager
    # This avoids making too many API calls here
    return all_symbols[:max_symbols]


# =============================================================================
# UNIVERSE MANAGER
# =============================================================================

class UniverseManager:
    """
    Manage and scan the US stock universe.
    
    Features:
    - Scan all US stocks
    - Filter by fundamentals
    - Identify recovery plays
    - Categorize by themes
    
    Example:
        manager = UniverseManager()
        stocks = manager.scan_universe(
            min_market_cap=1e9,
            min_volume=500000,
            include_recovery=True
        )
        
        # Get recovery champions
        recovery_plays = manager.get_recovery_champions(
            min_drawdown=0.40,
            min_recovery=0.30
        )
    """
    
    def __init__(self, cache_dir: str = "data/universe"):
        """Initialize universe manager."""
        self.cache_dir = cache_dir
        self.stock_info: Dict[str, StockInfo] = {}
        self.last_scan: Optional[datetime] = None
    
    def scan_universe(
        self,
        symbols: Optional[List[str]] = None,
        min_price: float = 5.0,
        min_volume: int = 300000,
        min_market_cap: float = 300_000_000,
        max_workers: int = 20,
        progress_callback: Optional[callable] = None
    ) -> List[StockInfo]:
        """
        Scan universe and get stock information.
        
        Args:
            symbols: Specific symbols to scan (None = all US stocks)
            min_price: Minimum price filter
            min_volume: Minimum average volume
            min_market_cap: Minimum market cap
            max_workers: Parallel workers
            progress_callback: Optional progress callback
            
        Returns:
            List of StockInfo objects
        """
        if symbols is None:
            symbols = get_all_us_stocks()
        
        logger.info(f"Scanning {len(symbols)} symbols...")
        
        results = []
        completed = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._get_stock_info, symbol): symbol 
                for symbol in symbols
            }
            
            for future in as_completed(futures):
                symbol = futures[future]
                completed += 1
                
                if progress_callback and completed % 50 == 0:
                    progress_callback(completed, len(symbols))
                
                try:
                    info = future.result()
                    if info:
                        # Apply filters
                        if (info.price >= min_price and 
                            info.avg_volume >= min_volume and
                            info.market_cap >= min_market_cap):
                            results.append(info)
                            self.stock_info[symbol] = info
                except Exception as e:
                    logger.debug(f"Error scanning {symbol}: {e}")
        
        self.last_scan = datetime.now()
        logger.info(f"Scan complete: {len(results)} stocks passed filters")
        
        return results
    
    def _get_stock_info(self, symbol: str) -> Optional[StockInfo]:
        """Get comprehensive stock information."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            if not info or 'regularMarketPrice' not in info:
                return None
            
            # Basic info
            price = info.get('regularMarketPrice') or info.get('currentPrice', 0)
            if not price or price <= 0:
                return None
            
            market_cap = info.get('marketCap', 0) or 0
            
            # Classify market cap
            if market_cap >= 200_000_000_000:
                cap_class = MarketCap.MEGA
            elif market_cap >= 10_000_000_000:
                cap_class = MarketCap.LARGE
            elif market_cap >= 2_000_000_000:
                cap_class = MarketCap.MID
            elif market_cap >= 300_000_000:
                cap_class = MarketCap.SMALL
            else:
                cap_class = MarketCap.MICRO
            
            # 52-week range
            high_52w = info.get('fiftyTwoWeekHigh', price)
            low_52w = info.get('fiftyTwoWeekLow', price)
            
            dist_from_high = (high_52w - price) / high_52w * 100 if high_52w > 0 else 0
            dist_from_low = (price - low_52w) / low_52w * 100 if low_52w > 0 else 0
            
            # Calculate drawdown and recovery
            max_drawdown = (high_52w - low_52w) / high_52w * 100 if high_52w > 0 else 0
            recovery_from_low = dist_from_low
            
            # Is this a recovery play? (>40% drawdown, >30% recovery)
            is_recovery = max_drawdown >= 40 and recovery_from_low >= 30
            
            # Identify themes
            themes = self._identify_themes(symbol, info)
            
            return StockInfo(
                symbol=symbol,
                name=info.get('shortName', ''),
                sector=info.get('sector', 'Unknown'),
                industry=info.get('industry', ''),
                market_cap=market_cap,
                market_cap_class=cap_class,
                price=price,
                avg_volume=info.get('averageVolume', 0),
                pe_ratio=info.get('trailingPE'),
                forward_pe=info.get('forwardPE'),
                peg_ratio=info.get('pegRatio'),
                price_to_book=info.get('priceToBook'),
                profit_margin=info.get('profitMargins'),
                roe=info.get('returnOnEquity'),
                debt_to_equity=info.get('debtToEquity'),
                revenue_growth=info.get('revenueGrowth'),
                earnings_growth=info.get('earningsGrowth'),
                high_52w=high_52w,
                low_52w=low_52w,
                dist_from_high=dist_from_high,
                dist_from_low=dist_from_low,
                max_drawdown=max_drawdown,
                recovery_from_low=recovery_from_low,
                is_recovery_play=is_recovery,
                themes=themes
            )
            
        except Exception as e:
            logger.debug(f"Error getting info for {symbol}: {e}")
            return None
    
    def _identify_themes(self, symbol: str, info: Dict) -> List[str]:
        """Identify themes/categories for a stock."""
        themes = []
        
        industry = info.get('industry', '').lower()
        sector = info.get('sector', '').lower()
        name = info.get('shortName', '').lower()
        
        for theme, data in THEME_KEYWORDS.items():
            # Check if symbol is explicitly in theme
            if symbol in data.get('symbols', []):
                themes.append(theme)
                continue
            
            # Check keywords
            for keyword in data.get('keywords', []):
                if keyword.lower() in industry or keyword.lower() in name:
                    themes.append(theme)
                    break
        
        return list(set(themes))
    
    def get_recovery_champions(
        self,
        stocks: Optional[List[StockInfo]] = None,
        min_drawdown: float = 40.0,
        min_recovery: float = 30.0,
        require_profitable: bool = False
    ) -> List[StockInfo]:
        """
        Get "recovery champion" stocks.
        
        These are stocks that:
        1. Had a significant drawdown (e.g., 40-60%)
        2. Recovered substantially (e.g., 30-50%+)
        3. Show institutional support through the recovery
        
        Args:
            stocks: List of stocks to filter (None = use cached)
            min_drawdown: Minimum drawdown from 52w high (%)
            min_recovery: Minimum recovery from 52w low (%)
            require_profitable: Only include profitable companies
            
        Returns:
            List of recovery champion stocks
        """
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        champions = []
        
        for stock in stocks:
            # Check drawdown and recovery
            if stock.max_drawdown < min_drawdown:
                continue
            if stock.recovery_from_low < min_recovery:
                continue
            
            # Optional: require profitability
            if require_profitable:
                if stock.profit_margin is None or stock.profit_margin <= 0:
                    continue
            
            champions.append(stock)
        
        # Sort by recovery strength
        champions.sort(key=lambda x: x.recovery_from_low, reverse=True)
        
        return champions
    
    def get_by_theme(
        self,
        theme: str,
        stocks: Optional[List[StockInfo]] = None
    ) -> List[StockInfo]:
        """Get stocks by theme."""
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        return [s for s in stocks if theme in s.themes]
    
    def get_by_sector(
        self,
        sector: str,
        stocks: Optional[List[StockInfo]] = None
    ) -> List[StockInfo]:
        """Get stocks by sector."""
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        return [s for s in stocks if sector.lower() in s.sector.lower()]
    
    def get_by_market_cap(
        self,
        cap_class: MarketCap,
        stocks: Optional[List[StockInfo]] = None
    ) -> List[StockInfo]:
        """Get stocks by market cap class."""
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        return [s for s in stocks if s.market_cap_class == cap_class]
    
    def get_fundamentally_strong(
        self,
        stocks: Optional[List[StockInfo]] = None,
        min_profit_margin: float = 0.05,
        max_debt_to_equity: float = 1.5,
        min_roe: float = 0.10
    ) -> List[StockInfo]:
        """
        Get stocks with strong fundamentals.
        
        Args:
            stocks: List to filter
            min_profit_margin: Minimum profit margin (5% default)
            max_debt_to_equity: Maximum D/E ratio
            min_roe: Minimum return on equity (10% default)
            
        Returns:
            Fundamentally strong stocks
        """
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        strong = []
        
        for stock in stocks:
            # Check profit margin
            if stock.profit_margin is None or stock.profit_margin < min_profit_margin:
                continue
            
            # Check debt (if available)
            if stock.debt_to_equity is not None and stock.debt_to_equity > max_debt_to_equity:
                continue
            
            # Check ROE (if available)
            if stock.roe is not None and stock.roe < min_roe:
                continue
            
            strong.append(stock)
        
        return strong
    
    def get_undervalued(
        self,
        stocks: Optional[List[StockInfo]] = None,
        max_pe: float = 20.0,
        max_peg: float = 1.5,
        min_dist_from_high: float = 20.0
    ) -> List[StockInfo]:
        """
        Get potentially undervalued stocks.
        
        Args:
            stocks: List to filter
            max_pe: Maximum P/E ratio
            max_peg: Maximum PEG ratio
            min_dist_from_high: Minimum % below 52w high
            
        Returns:
            Potentially undervalued stocks
        """
        if stocks is None:
            stocks = list(self.stock_info.values())
        
        undervalued = []
        
        for stock in stocks:
            score = 0
            
            # P/E check
            if stock.pe_ratio is not None and 0 < stock.pe_ratio < max_pe:
                score += 1
            
            # PEG check
            if stock.peg_ratio is not None and 0 < stock.peg_ratio < max_peg:
                score += 1
            
            # Distance from high
            if stock.dist_from_high >= min_dist_from_high:
                score += 1
            
            # Price to book
            if stock.price_to_book is not None and 0 < stock.price_to_book < 3:
                score += 1
            
            # Need at least 2 value indicators
            if score >= 2:
                undervalued.append(stock)
        
        return undervalued
    
    def print_summary(self, stocks: List[StockInfo]):
        """Print summary of scanned stocks."""
        if not stocks:
            print("No stocks to summarize")
            return
        
        print("\n" + "=" * 80)
        print(f"  UNIVERSE SCAN SUMMARY - {len(stocks)} Stocks")
        print("=" * 80)
        
        # By market cap
        print("\n📊 BY MARKET CAP:")
        for cap in MarketCap:
            count = len([s for s in stocks if s.market_cap_class == cap])
            if count > 0:
                print(f"   {cap.value:10} : {count:4} stocks")
        
        # By sector
        print("\n📈 BY SECTOR:")
        sectors = {}
        for stock in stocks:
            sector = stock.sector or "Unknown"
            sectors[sector] = sectors.get(sector, 0) + 1
        for sector, count in sorted(sectors.items(), key=lambda x: -x[1])[:10]:
            print(f"   {sector:25} : {count:4} stocks")
        
        # Recovery plays
        recovery = [s for s in stocks if s.is_recovery_play]
        print(f"\n🔄 RECOVERY PLAYS: {len(recovery)} stocks")
        for stock in recovery[:10]:
            print(f"   {stock.symbol:6} | DD: {stock.max_drawdown:5.1f}% | "
                  f"Recovery: {stock.recovery_from_low:5.1f}% | ${stock.price:.2f}")
        
        # By theme
        print("\n🏷️  BY THEME:")
        theme_counts = {}
        for stock in stocks:
            for theme in stock.themes:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
        for theme, count in sorted(theme_counts.items(), key=lambda x: -x[1]):
            print(f"   {theme:20} : {count:4} stocks")
