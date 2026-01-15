"""
Stock Screeners - Multiple screening strategies.

Screeners:
1. FundamentalScreener - Quality companies with solid financials
2. RecoveryScreener - Stocks bouncing from major drawdowns  
3. MomentumScreener - Strong price momentum
4. UndervaluedScreener - Cheap relative to fundamentals
5. ComprehensiveScreener - Combines all criteria
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from .universe_manager import StockInfo, MarketCap, THEME_KEYWORDS
from ..core.types import Regime, VolatilityLevel, Strategy

logger = logging.getLogger(__name__)


@dataclass
class ScreenResult:
    """Result from screening a stock."""
    symbol: str
    stock_info: StockInfo
    
    # Scores (0-100)
    fundamental_score: float = 0.0
    momentum_score: float = 0.0
    value_score: float = 0.0
    recovery_score: float = 0.0
    technical_score: float = 0.0
    
    # Overall
    total_score: float = 0.0
    grade: str = "F"
    
    # Flags
    is_recovery_play: bool = False
    is_momentum_play: bool = False
    is_value_play: bool = False
    is_quality: bool = False
    
    # Reasoning
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    reasoning: str = ""


class FundamentalScreener:
    """
    Screen for fundamentally strong companies.
    
    Criteria:
    - Profitable (positive margins)
    - Good ROE (>10%)
    - Reasonable debt (<1.5x D/E)
    - Growing revenue/earnings
    """
    
    def __init__(
        self,
        min_profit_margin: float = 0.05,
        min_roe: float = 0.10,
        max_debt_to_equity: float = 1.5,
        min_revenue_growth: float = 0.05
    ):
        self.min_profit_margin = min_profit_margin
        self.min_roe = min_roe
        self.max_debt_to_equity = max_debt_to_equity
        self.min_revenue_growth = min_revenue_growth
    
    def score(self, stock: StockInfo) -> Tuple[float, List[str], List[str]]:
        """
        Score stock on fundamentals (0-100).
        
        Returns: (score, strengths, weaknesses)
        """
        score = 50  # Base score
        strengths = []
        weaknesses = []
        
        # Profit Margin (30 points max)
        if stock.profit_margin is not None:
            if stock.profit_margin >= 0.20:
                score += 30
                strengths.append(f"High margin ({stock.profit_margin:.1%})")
            elif stock.profit_margin >= 0.10:
                score += 20
                strengths.append(f"Good margin ({stock.profit_margin:.1%})")
            elif stock.profit_margin >= self.min_profit_margin:
                score += 10
            elif stock.profit_margin <= 0:
                score -= 20
                weaknesses.append("Unprofitable")
        else:
            weaknesses.append("No margin data")
        
        # ROE (20 points max)
        if stock.roe is not None:
            if stock.roe >= 0.20:
                score += 20
                strengths.append(f"Excellent ROE ({stock.roe:.1%})")
            elif stock.roe >= self.min_roe:
                score += 10
                strengths.append(f"Good ROE ({stock.roe:.1%})")
            elif stock.roe < 0:
                score -= 10
                weaknesses.append("Negative ROE")
        
        # Debt (15 points max)
        if stock.debt_to_equity is not None:
            if stock.debt_to_equity <= 0.5:
                score += 15
                strengths.append("Low debt")
            elif stock.debt_to_equity <= self.max_debt_to_equity:
                score += 5
            elif stock.debt_to_equity > 2.0:
                score -= 10
                weaknesses.append(f"High debt ({stock.debt_to_equity:.1f}x)")
        
        # Growth (20 points max)
        if stock.revenue_growth is not None:
            if stock.revenue_growth >= 0.20:
                score += 20
                strengths.append(f"Strong growth ({stock.revenue_growth:.1%})")
            elif stock.revenue_growth >= self.min_revenue_growth:
                score += 10
                strengths.append(f"Growing ({stock.revenue_growth:.1%})")
            elif stock.revenue_growth < 0:
                score -= 5
                weaknesses.append("Revenue declining")
        
        # Earnings growth (15 points max)
        if stock.earnings_growth is not None:
            if stock.earnings_growth >= 0.25:
                score += 15
                strengths.append("Strong earnings growth")
            elif stock.earnings_growth >= 0.10:
                score += 8
        
        return max(0, min(100, score)), strengths, weaknesses
    
    def screen(self, stocks: List[StockInfo]) -> List[StockInfo]:
        """Screen for fundamentally strong stocks."""
        results = []
        
        for stock in stocks:
            score, _, _ = self.score(stock)
            if score >= 60:  # Pass threshold
                results.append(stock)
        
        return results


class RecoveryScreener:
    """
    Screen for "recovery champion" stocks.
    
    Criteria:
    - Major drawdown from high (40-60%+)
    - Significant recovery from low (30-50%+)
    - Shows institutional support
    - Ideally with improving fundamentals
    """
    
    def __init__(
        self,
        min_drawdown: float = 40.0,
        min_recovery: float = 30.0,
        max_recovery: float = 80.0  # Not already fully recovered
    ):
        self.min_drawdown = min_drawdown
        self.min_recovery = min_recovery
        self.max_recovery = max_recovery
    
    def score(self, stock: StockInfo) -> Tuple[float, List[str], List[str]]:
        """
        Score stock on recovery potential (0-100).
        
        Returns: (score, strengths, weaknesses)
        """
        score = 0
        strengths = []
        weaknesses = []
        
        # Must have significant drawdown
        if stock.max_drawdown < self.min_drawdown:
            return 0, [], ["Not enough drawdown"]
        
        # Drawdown score (25 points max)
        # Bigger drawdown = more opportunity
        if stock.max_drawdown >= 60:
            score += 25
            strengths.append(f"Major correction ({stock.max_drawdown:.0f}% DD)")
        elif stock.max_drawdown >= 50:
            score += 20
            strengths.append(f"Significant DD ({stock.max_drawdown:.0f}%)")
        elif stock.max_drawdown >= self.min_drawdown:
            score += 15
        
        # Recovery score (40 points max)
        # Sweet spot is 30-60% recovery (room to run)
        if self.min_recovery <= stock.recovery_from_low <= 60:
            score += 40
            strengths.append(f"Bouncing ({stock.recovery_from_low:.0f}% from low)")
        elif stock.recovery_from_low >= 60:
            score += 25
            strengths.append(f"Strong recovery ({stock.recovery_from_low:.0f}%)")
            if stock.recovery_from_low >= self.max_recovery:
                weaknesses.append("May have run too far")
        elif stock.recovery_from_low < self.min_recovery:
            score += 10
            weaknesses.append("Still near lows")
        
        # Room to run (20 points max)
        # Distance from 52w high
        if 20 <= stock.dist_from_high <= 40:
            score += 20
            strengths.append(f"Room to run ({stock.dist_from_high:.0f}% from high)")
        elif stock.dist_from_high >= 40:
            score += 15
            strengths.append("Deep value territory")
        elif stock.dist_from_high < 10:
            score += 5
            weaknesses.append("Near highs already")
        else:
            score += 10
        
        # Fundamental bonus (15 points max)
        if stock.profit_margin is not None and stock.profit_margin > 0:
            score += 10
            strengths.append("Profitable")
        if stock.revenue_growth is not None and stock.revenue_growth > 0:
            score += 5
            strengths.append("Growing")
        
        return max(0, min(100, score)), strengths, weaknesses
    
    def screen(self, stocks: List[StockInfo]) -> List[StockInfo]:
        """Screen for recovery plays."""
        results = []
        
        for stock in stocks:
            # Must meet drawdown criteria
            if stock.max_drawdown < self.min_drawdown:
                continue
            if stock.recovery_from_low < self.min_recovery:
                continue
            
            score, _, _ = self.score(stock)
            if score >= 50:
                results.append(stock)
        
        # Sort by recovery score
        results.sort(key=lambda x: self.score(x)[0], reverse=True)
        return results


class MomentumScreener:
    """
    Screen for stocks with strong momentum.
    
    Criteria:
    - Strong recent price performance
    - Above key moving averages
    - Relative strength vs market
    - Not overextended
    """
    
    def __init__(
        self,
        min_3m_return: float = 10.0,
        max_dist_from_high: float = 15.0
    ):
        self.min_3m_return = min_3m_return
        self.max_dist_from_high = max_dist_from_high
    
    def score_with_data(
        self,
        stock: StockInfo,
        price_data: Optional[pd.DataFrame] = None
    ) -> Tuple[float, List[str], List[str]]:
        """Score momentum with price data."""
        score = 50
        strengths = []
        weaknesses = []
        
        # Near highs (30 points)
        if stock.dist_from_high <= 5:
            score += 30
            strengths.append("At 52w highs")
        elif stock.dist_from_high <= self.max_dist_from_high:
            score += 20
            strengths.append(f"Near highs ({stock.dist_from_high:.0f}% off)")
        elif stock.dist_from_high > 30:
            score -= 10
            weaknesses.append("Far from highs")
        
        # Recovery strength (20 points)
        if stock.recovery_from_low >= 50:
            score += 20
            strengths.append("Strong bounce from lows")
        elif stock.recovery_from_low >= 30:
            score += 10
        
        # If we have price data, calculate more
        if price_data is not None and len(price_data) >= 63:
            close = price_data['close'] if 'close' in price_data else price_data['Close']
            
            # 3-month return
            ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
            if ret_3m >= 30:
                score += 25
                strengths.append(f"Strong 3M ({ret_3m:+.0f}%)")
            elif ret_3m >= self.min_3m_return:
                score += 15
                strengths.append(f"Good 3M ({ret_3m:+.0f}%)")
            elif ret_3m < 0:
                score -= 15
                weaknesses.append(f"Negative 3M ({ret_3m:+.0f}%)")
        
        return max(0, min(100, score)), strengths, weaknesses


class UndervaluedScreener:
    """
    Screen for undervalued stocks.
    
    Criteria:
    - Low P/E relative to growth (PEG)
    - Low price-to-book
    - Significant discount from highs
    - But still quality company
    """
    
    def __init__(
        self,
        max_pe: float = 25.0,
        max_peg: float = 1.5,
        max_pb: float = 3.0,
        min_dist_from_high: float = 15.0
    ):
        self.max_pe = max_pe
        self.max_peg = max_peg
        self.max_pb = max_pb
        self.min_dist_from_high = min_dist_from_high
    
    def score(self, stock: StockInfo) -> Tuple[float, List[str], List[str]]:
        """Score stock on value (0-100)."""
        score = 40
        strengths = []
        weaknesses = []
        
        value_indicators = 0
        
        # P/E (25 points)
        if stock.pe_ratio is not None:
            if 0 < stock.pe_ratio < 15:
                score += 25
                strengths.append(f"Low P/E ({stock.pe_ratio:.1f})")
                value_indicators += 1
            elif 0 < stock.pe_ratio < self.max_pe:
                score += 15
                value_indicators += 1
            elif stock.pe_ratio > 50:
                score -= 10
                weaknesses.append(f"High P/E ({stock.pe_ratio:.0f})")
        
        # PEG (25 points)
        if stock.peg_ratio is not None:
            if 0 < stock.peg_ratio < 1.0:
                score += 25
                strengths.append(f"Low PEG ({stock.peg_ratio:.2f})")
                value_indicators += 1
            elif 0 < stock.peg_ratio < self.max_peg:
                score += 15
                value_indicators += 1
            elif stock.peg_ratio > 2.5:
                score -= 5
        
        # Price-to-Book (15 points)
        if stock.price_to_book is not None:
            if 0 < stock.price_to_book < 2:
                score += 15
                strengths.append(f"Low P/B ({stock.price_to_book:.1f})")
                value_indicators += 1
            elif 0 < stock.price_to_book < self.max_pb:
                score += 8
        
        # Distance from high (15 points)
        if stock.dist_from_high >= 30:
            score += 15
            strengths.append(f"Beaten down ({stock.dist_from_high:.0f}% off highs)")
            value_indicators += 1
        elif stock.dist_from_high >= self.min_dist_from_high:
            score += 10
            value_indicators += 1
        
        # Quality check - don't want value traps
        if stock.profit_margin is not None and stock.profit_margin > 0:
            score += 10
            strengths.append("Profitable (not a trap)")
        else:
            score -= 15
            weaknesses.append("Unprofitable - potential trap")
        
        # Need multiple value indicators
        if value_indicators < 2:
            score -= 20
            weaknesses.append("Few value indicators")
        
        return max(0, min(100, score)), strengths, weaknesses


class ComprehensiveScreener:
    """
    Comprehensive screener combining all criteria.
    
    Finds stocks that are:
    1. Recovery champions (bouncing from drawdown)
    2. OR strong momentum plays
    3. OR undervalued quality
    
    All with solid fundamentals as a base.
    """
    
    def __init__(self):
        self.fundamental = FundamentalScreener()
        self.recovery = RecoveryScreener()
        self.momentum = MomentumScreener()
        self.value = UndervaluedScreener()
    
    def screen(
        self,
        stock: StockInfo,
        price_data: Optional[pd.DataFrame] = None
    ) -> ScreenResult:
        """
        Comprehensive screen of a single stock.
        
        Returns ScreenResult with all scores and analysis.
        """
        # Get all scores
        fund_score, fund_str, fund_weak = self.fundamental.score(stock)
        recov_score, recov_str, recov_weak = self.recovery.score(stock)
        mom_score, mom_str, mom_weak = self.momentum.score_with_data(stock, price_data)
        value_score, value_str, value_weak = self.value.score(stock)
        
        # Calculate total score (weighted)
        # - Base quality: 30%
        # - Best of (recovery, momentum, value): 50%
        # - Other factors: 20%
        
        best_play_score = max(recov_score, mom_score, value_score)
        
        total_score = (
            fund_score * 0.30 +
            best_play_score * 0.50 +
            (recov_score + mom_score + value_score - best_play_score) / 2 * 0.20
        )
        
        # Grade
        if total_score >= 80:
            grade = "A"
        elif total_score >= 70:
            grade = "B"
        elif total_score >= 60:
            grade = "C"
        elif total_score >= 50:
            grade = "D"
        else:
            grade = "F"
        
        # Flags
        is_recovery = recov_score >= 60 and stock.max_drawdown >= 40
        is_momentum = mom_score >= 60 and stock.dist_from_high <= 15
        is_value = value_score >= 60
        is_quality = fund_score >= 60
        
        # Combine strengths/weaknesses
        all_strengths = fund_str + recov_str + mom_str + value_str
        all_weaknesses = fund_weak + recov_weak + mom_weak + value_weak
        
        # Build reasoning
        play_type = []
        if is_recovery:
            play_type.append("Recovery")
        if is_momentum:
            play_type.append("Momentum")
        if is_value:
            play_type.append("Value")
        if is_quality:
            play_type.append("Quality")
        
        reasoning = f"{' + '.join(play_type) or 'Mixed'} play. "
        if all_strengths:
            reasoning += f"Strengths: {', '.join(all_strengths[:3])}. "
        if all_weaknesses:
            reasoning += f"Watch: {', '.join(all_weaknesses[:2])}."
        
        return ScreenResult(
            symbol=stock.symbol,
            stock_info=stock,
            fundamental_score=fund_score,
            momentum_score=mom_score,
            value_score=value_score,
            recovery_score=recov_score,
            technical_score=mom_score,  # Use momentum as proxy
            total_score=total_score,
            grade=grade,
            is_recovery_play=is_recovery,
            is_momentum_play=is_momentum,
            is_value_play=is_value,
            is_quality=is_quality,
            strengths=list(set(all_strengths)),
            weaknesses=list(set(all_weaknesses)),
            reasoning=reasoning
        )
    
    def screen_all(
        self,
        stocks: List[StockInfo],
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
        min_score: float = 50.0
    ) -> List[ScreenResult]:
        """Screen all stocks and return sorted results."""
        results = []
        
        for stock in stocks:
            data = price_data.get(stock.symbol) if price_data else None
            result = self.screen(stock, data)
            
            if result.total_score >= min_score:
                results.append(result)
        
        # Sort by score
        results.sort(key=lambda x: x.total_score, reverse=True)
        
        return results
