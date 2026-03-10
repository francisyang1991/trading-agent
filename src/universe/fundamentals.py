"""
ADVANCED FUNDAMENTAL ANALYSIS
=============================

Sophisticated fundamental metrics beyond basic P/E:

1. QUALITY METRICS
   - ROIC (Return on Invested Capital) - True profitability
   - FCF Yield (Free Cash Flow Yield) - Cash generation
   - Gross Margin Stability - Business moat indicator
   - Earnings Quality - Accruals ratio

2. GROWTH METRICS  
   - Revenue CAGR (3-year)
   - EPS Growth Rate
   - FCF Growth
   - Rule of 40 (for SaaS/growth)

3. FINANCIAL HEALTH
   - Interest Coverage Ratio
   - Current Ratio
   - Quick Ratio
   - Altman Z-Score (bankruptcy risk)

4. VALUATION METRICS
   - EV/EBITDA
   - EV/FCF
   - Price/Sales to Growth (PSG)
   - Discounted Cash Flow (DCF) intrinsic value

5. COMPOSITE SCORES
   - Piotroski F-Score (0-9)
   - Quality Score (0-100)
   - Value Score (0-100)
   - Growth Score (0-100)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import yfinance as yf
import logging

logger = logging.getLogger(__name__)


@dataclass
class FundamentalMetrics:
    """Comprehensive fundamental metrics for a stock."""
    symbol: str
    
    # Basic Info
    name: str = ""
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0
    
    # Price Metrics
    price: float = 0.0
    high_52w: float = 0.0
    low_52w: float = 0.0
    
    # Profitability
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    profit_margin: Optional[float] = None
    roe: Optional[float] = None  # Return on Equity
    roa: Optional[float] = None  # Return on Assets
    roic: Optional[float] = None  # Return on Invested Capital (calculated)
    
    # Cash Flow
    free_cash_flow: Optional[float] = None
    fcf_yield: Optional[float] = None
    fcf_per_share: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    
    # Growth
    revenue_growth_yoy: Optional[float] = None
    revenue_growth_3y_cagr: Optional[float] = None
    earnings_growth_yoy: Optional[float] = None
    fcf_growth_yoy: Optional[float] = None
    
    # Valuation
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    price_to_book: Optional[float] = None
    price_to_sales: Optional[float] = None
    ev_to_ebitda: Optional[float] = None
    ev_to_revenue: Optional[float] = None
    ev_to_fcf: Optional[float] = None
    
    # Financial Health
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    interest_coverage: Optional[float] = None
    altman_z_score: Optional[float] = None
    
    # Quality Indicators
    earnings_quality: Optional[float] = None  # OCF/Net Income ratio
    accruals_ratio: Optional[float] = None
    revenue_consistency: Optional[float] = None
    
    # Composite Scores (0-100)
    piotroski_f_score: int = 0  # 0-9
    quality_score: float = 0.0
    value_score: float = 0.0
    growth_score: float = 0.0
    financial_health_score: float = 0.0
    overall_fundamental_score: float = 0.0
    
    # Grade
    fundamental_grade: str = "F"
    
    # Analysis
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    analysis: str = ""


class FundamentalAnalyzer:
    """
    Advanced fundamental analysis for stocks.
    
    Goes beyond basic P/E to analyze:
    - True profitability (ROIC, FCF)
    - Business quality (margins, consistency)
    - Financial health (debt, coverage)
    - Growth sustainability
    - Fair value estimation
    """
    
    def __init__(self):
        self.cache: Dict[str, FundamentalMetrics] = {}
    
    def analyze(self, symbol: str, force_refresh: bool = False) -> Optional[FundamentalMetrics]:
        """
        Perform comprehensive fundamental analysis.
        
        Args:
            symbol: Stock ticker
            force_refresh: Ignore cache
            
        Returns:
            FundamentalMetrics with all analysis
        """
        if not force_refresh and symbol in self.cache:
            return self.cache[symbol]
        
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            if not info:
                return None
            
            metrics = FundamentalMetrics(symbol=symbol)
            
            # Basic info
            metrics.name = info.get('shortName', '')
            metrics.sector = info.get('sector', '')
            metrics.industry = info.get('industry', '')
            metrics.market_cap = info.get('marketCap', 0) or 0
            metrics.price = info.get('regularMarketPrice') or info.get('currentPrice', 0)
            metrics.high_52w = info.get('fiftyTwoWeekHigh', 0)
            metrics.low_52w = info.get('fiftyTwoWeekLow', 0)
            
            # Get financial statements for deeper analysis
            try:
                income_stmt = ticker.income_stmt
                balance_sheet = ticker.balance_sheet
                cash_flow = ticker.cash_flow
            except Exception:
                income_stmt = pd.DataFrame()
                balance_sheet = pd.DataFrame()
                cash_flow = pd.DataFrame()
            
            # Profitability metrics
            metrics.gross_margin = info.get('grossMargins')
            metrics.operating_margin = info.get('operatingMargins')
            metrics.profit_margin = info.get('profitMargins')
            metrics.roe = info.get('returnOnEquity')
            metrics.roa = info.get('returnOnAssets')
            
            # Calculate ROIC if possible
            metrics.roic = self._calculate_roic(info, balance_sheet, income_stmt)
            
            # Cash flow metrics
            metrics.free_cash_flow = info.get('freeCashflow')
            metrics.operating_cash_flow = info.get('operatingCashflow')
            
            if metrics.free_cash_flow and metrics.market_cap:
                metrics.fcf_yield = metrics.free_cash_flow / metrics.market_cap
            
            shares = info.get('sharesOutstanding', 0)
            if metrics.free_cash_flow and shares:
                metrics.fcf_per_share = metrics.free_cash_flow / shares
            
            # Growth metrics
            metrics.revenue_growth_yoy = info.get('revenueGrowth')
            metrics.earnings_growth_yoy = info.get('earningsGrowth')
            metrics.revenue_growth_3y_cagr = self._calculate_revenue_cagr(income_stmt)
            
            # Valuation metrics
            metrics.pe_ratio = info.get('trailingPE')
            metrics.forward_pe = info.get('forwardPE')
            metrics.peg_ratio = info.get('pegRatio')
            metrics.price_to_book = info.get('priceToBook')
            metrics.price_to_sales = info.get('priceToSalesTrailing12Months')
            metrics.ev_to_ebitda = info.get('enterpriseToEbitda')
            metrics.ev_to_revenue = info.get('enterpriseToRevenue')
            
            # Calculate EV/FCF
            ev = info.get('enterpriseValue', 0)
            if ev and metrics.free_cash_flow and metrics.free_cash_flow > 0:
                metrics.ev_to_fcf = ev / metrics.free_cash_flow
            
            # Financial health
            metrics.current_ratio = info.get('currentRatio')
            metrics.quick_ratio = info.get('quickRatio')
            metrics.debt_to_equity = info.get('debtToEquity')
            
            # Calculate additional health metrics
            metrics.interest_coverage = self._calculate_interest_coverage(income_stmt)
            metrics.altman_z_score = self._calculate_altman_z(info, balance_sheet, income_stmt)
            
            # Earnings quality
            if metrics.operating_cash_flow and metrics.profit_margin:
                net_income = info.get('netIncomeToCommon', 0)
                if net_income and net_income > 0:
                    metrics.earnings_quality = metrics.operating_cash_flow / net_income
            
            # Calculate composite scores
            metrics.piotroski_f_score = self._calculate_piotroski(metrics, info)
            metrics.quality_score = self._calculate_quality_score(metrics)
            metrics.value_score = self._calculate_value_score(metrics)
            metrics.growth_score = self._calculate_growth_score(metrics)
            metrics.financial_health_score = self._calculate_health_score(metrics)
            
            # Overall score (weighted average)
            metrics.overall_fundamental_score = (
                metrics.quality_score * 0.30 +
                metrics.value_score * 0.25 +
                metrics.growth_score * 0.25 +
                metrics.financial_health_score * 0.20
            )
            
            # Grade
            if metrics.overall_fundamental_score >= 80:
                metrics.fundamental_grade = "A"
            elif metrics.overall_fundamental_score >= 70:
                metrics.fundamental_grade = "B"
            elif metrics.overall_fundamental_score >= 60:
                metrics.fundamental_grade = "C"
            elif metrics.overall_fundamental_score >= 50:
                metrics.fundamental_grade = "D"
            else:
                metrics.fundamental_grade = "F"
            
            # Generate analysis
            self._generate_analysis(metrics)
            
            self.cache[symbol] = metrics
            return metrics
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    def _calculate_roic(
        self,
        info: Dict,
        balance_sheet: pd.DataFrame,
        income_stmt: pd.DataFrame
    ) -> Optional[float]:
        """Calculate Return on Invested Capital."""
        try:
            # ROIC = NOPAT / Invested Capital
            # NOPAT = Operating Income * (1 - Tax Rate)
            # Invested Capital = Total Equity + Total Debt - Cash
            
            operating_income = info.get('operatingIncome', 0)
            if not operating_income:
                return None
            
            # Estimate tax rate (use 25% if not available)
            tax_rate = 0.25
            
            nopat = operating_income * (1 - tax_rate)
            
            total_equity = info.get('totalStockholderEquity', 0) or 0
            total_debt = info.get('totalDebt', 0) or 0
            cash = info.get('totalCash', 0) or 0
            
            invested_capital = total_equity + total_debt - cash
            
            if invested_capital > 0:
                return nopat / invested_capital
            
            return None
            
        except Exception:
            return None
    
    def _calculate_revenue_cagr(self, income_stmt: pd.DataFrame) -> Optional[float]:
        """Calculate 3-year revenue CAGR."""
        try:
            if income_stmt.empty:
                return None
            
            # Get Total Revenue row
            if 'Total Revenue' in income_stmt.index:
                revenue = income_stmt.loc['Total Revenue']
            elif 'TotalRevenue' in income_stmt.index:
                revenue = income_stmt.loc['TotalRevenue']
            else:
                return None
            
            # Get first and last values (most recent first in yfinance)
            if len(revenue) >= 3:
                latest = revenue.iloc[0]
                oldest = revenue.iloc[2]  # 3 years ago
                
                if oldest > 0:
                    cagr = (latest / oldest) ** (1/3) - 1
                    return cagr
            
            return None
            
        except Exception:
            return None
    
    def _calculate_interest_coverage(self, income_stmt: pd.DataFrame) -> Optional[float]:
        """Calculate interest coverage ratio."""
        try:
            if income_stmt.empty:
                return None
            
            ebit = None
            interest = None
            
            # Find EBIT
            for key in ['EBIT', 'Operating Income', 'OperatingIncome']:
                if key in income_stmt.index:
                    ebit = income_stmt.loc[key].iloc[0]
                    break
            
            # Find Interest Expense
            for key in ['Interest Expense', 'InterestExpense']:
                if key in income_stmt.index:
                    interest = abs(income_stmt.loc[key].iloc[0])
                    break
            
            if ebit and interest and interest > 0:
                return ebit / interest
            
            return None
            
        except Exception:
            return None
    
    def _calculate_altman_z(
        self,
        info: Dict,
        balance_sheet: pd.DataFrame,
        income_stmt: pd.DataFrame
    ) -> Optional[float]:
        """
        Calculate Altman Z-Score (bankruptcy risk).
        
        Z = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E
        
        Where:
        A = Working Capital / Total Assets
        B = Retained Earnings / Total Assets
        C = EBIT / Total Assets
        D = Market Cap / Total Liabilities
        E = Sales / Total Assets
        
        Score interpretation:
        > 2.99: Safe
        1.81 - 2.99: Grey zone
        < 1.81: Distress
        """
        try:
            total_assets = info.get('totalAssets', 0)
            if not total_assets or total_assets <= 0:
                return None
            
            # A: Working Capital / Total Assets
            current_assets = info.get('totalCurrentAssets', 0) or 0
            current_liab = info.get('totalCurrentLiabilities', 0) or 0
            working_capital = current_assets - current_liab
            A = working_capital / total_assets
            
            # B: Retained Earnings / Total Assets (approximate with equity)
            retained = info.get('totalStockholderEquity', 0) or 0
            B = retained / total_assets * 0.5  # Discount since it's equity not RE
            
            # C: EBIT / Total Assets
            ebit = info.get('operatingIncome', 0) or 0
            C = ebit / total_assets
            
            # D: Market Cap / Total Liabilities
            market_cap = info.get('marketCap', 0) or 0
            total_liab = info.get('totalLiabilities', 0) or info.get('totalDebt', 0) or 1
            D = market_cap / total_liab if total_liab > 0 else 0
            
            # E: Sales / Total Assets
            revenue = info.get('totalRevenue', 0) or 0
            E = revenue / total_assets
            
            z_score = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E
            
            return z_score
            
        except Exception:
            return None
    
    def _calculate_piotroski(self, metrics: FundamentalMetrics, info: Dict) -> int:
        """
        Calculate Piotroski F-Score (0-9).
        
        Profitability (4 points):
        1. Positive net income
        2. Positive ROA
        3. Positive operating cash flow
        4. OCF > Net Income (quality of earnings)
        
        Leverage/Liquidity (3 points):
        5. Lower debt ratio vs prior year
        6. Higher current ratio vs prior year
        7. No new shares issued
        
        Operating Efficiency (2 points):
        8. Higher gross margin vs prior year
        9. Higher asset turnover vs prior year
        """
        score = 0
        
        # Profitability
        # 1. Positive net income
        if metrics.profit_margin and metrics.profit_margin > 0:
            score += 1
        
        # 2. Positive ROA
        if metrics.roa and metrics.roa > 0:
            score += 1
        
        # 3. Positive operating cash flow
        if metrics.operating_cash_flow and metrics.operating_cash_flow > 0:
            score += 1
        
        # 4. OCF > Net Income
        if metrics.earnings_quality and metrics.earnings_quality > 1:
            score += 1
        
        # Leverage/Liquidity
        # 5. Lower debt (use D/E < 1 as proxy)
        if metrics.debt_to_equity is not None and metrics.debt_to_equity < 1:
            score += 1
        
        # 6. Good current ratio (> 1.5)
        if metrics.current_ratio and metrics.current_ratio > 1.5:
            score += 1
        
        # 7. No dilution (skip - would need historical data)
        # Give point if shares outstanding is reasonable
        shares = info.get('sharesOutstanding', 0)
        float_shares = info.get('floatShares', 0)
        if shares and float_shares and float_shares / shares > 0.7:
            score += 1
        
        # Operating Efficiency
        # 8. Good gross margin (> 30%)
        if metrics.gross_margin and metrics.gross_margin > 0.30:
            score += 1
        
        # 9. Revenue growth (proxy for efficiency)
        if metrics.revenue_growth_yoy and metrics.revenue_growth_yoy > 0:
            score += 1
        
        return score
    
    def _calculate_quality_score(self, metrics: FundamentalMetrics) -> float:
        """Calculate quality score (0-100)."""
        score = 50  # Base
        
        # ROIC (25 points max)
        if metrics.roic is not None:
            if metrics.roic >= 0.20:
                score += 25
            elif metrics.roic >= 0.15:
                score += 20
            elif metrics.roic >= 0.10:
                score += 15
            elif metrics.roic >= 0.05:
                score += 10
            elif metrics.roic < 0:
                score -= 15
        
        # Profit margin (20 points max)
        if metrics.profit_margin is not None:
            if metrics.profit_margin >= 0.20:
                score += 20
            elif metrics.profit_margin >= 0.10:
                score += 15
            elif metrics.profit_margin >= 0.05:
                score += 10
            elif metrics.profit_margin < 0:
                score -= 20
        
        # ROE (15 points max)
        if metrics.roe is not None:
            if metrics.roe >= 0.20:
                score += 15
            elif metrics.roe >= 0.15:
                score += 10
            elif metrics.roe >= 0.10:
                score += 5
            elif metrics.roe < 0:
                score -= 10
        
        # Earnings quality (15 points max)
        if metrics.earnings_quality is not None:
            if metrics.earnings_quality >= 1.2:
                score += 15  # OCF exceeds net income
            elif metrics.earnings_quality >= 1.0:
                score += 10
            elif metrics.earnings_quality >= 0.8:
                score += 5
            elif metrics.earnings_quality < 0.5:
                score -= 10  # Poor earnings quality
        
        # FCF yield (10 points max)
        if metrics.fcf_yield is not None:
            if metrics.fcf_yield >= 0.08:
                score += 10
            elif metrics.fcf_yield >= 0.05:
                score += 7
            elif metrics.fcf_yield >= 0.02:
                score += 3
            elif metrics.fcf_yield < 0:
                score -= 10
        
        # Piotroski bonus (up to 10 points)
        score += metrics.piotroski_f_score
        
        return max(0, min(100, score))
    
    def _calculate_value_score(self, metrics: FundamentalMetrics) -> float:
        """Calculate value score (0-100)."""
        score = 50  # Base
        
        # P/E (25 points max)
        if metrics.pe_ratio is not None and metrics.pe_ratio > 0:
            if metrics.pe_ratio < 12:
                score += 25
            elif metrics.pe_ratio < 18:
                score += 20
            elif metrics.pe_ratio < 25:
                score += 10
            elif metrics.pe_ratio > 50:
                score -= 15
        
        # PEG (20 points max)
        if metrics.peg_ratio is not None and metrics.peg_ratio > 0:
            if metrics.peg_ratio < 1.0:
                score += 20
            elif metrics.peg_ratio < 1.5:
                score += 15
            elif metrics.peg_ratio < 2.0:
                score += 5
            elif metrics.peg_ratio > 3.0:
                score -= 10
        
        # EV/EBITDA (15 points max)
        if metrics.ev_to_ebitda is not None and metrics.ev_to_ebitda > 0:
            if metrics.ev_to_ebitda < 8:
                score += 15
            elif metrics.ev_to_ebitda < 12:
                score += 10
            elif metrics.ev_to_ebitda < 15:
                score += 5
            elif metrics.ev_to_ebitda > 25:
                score -= 10
        
        # EV/FCF (15 points max)
        if metrics.ev_to_fcf is not None and metrics.ev_to_fcf > 0:
            if metrics.ev_to_fcf < 15:
                score += 15
            elif metrics.ev_to_fcf < 20:
                score += 10
            elif metrics.ev_to_fcf < 25:
                score += 5
            elif metrics.ev_to_fcf > 40:
                score -= 10
        
        # Price to Book (10 points max)
        if metrics.price_to_book is not None and metrics.price_to_book > 0:
            if metrics.price_to_book < 2:
                score += 10
            elif metrics.price_to_book < 4:
                score += 5
            elif metrics.price_to_book > 10:
                score -= 10
        
        return max(0, min(100, score))
    
    def _calculate_growth_score(self, metrics: FundamentalMetrics) -> float:
        """Calculate growth score (0-100)."""
        score = 50  # Base
        
        # Revenue growth YoY (30 points max)
        if metrics.revenue_growth_yoy is not None:
            if metrics.revenue_growth_yoy >= 0.30:
                score += 30
            elif metrics.revenue_growth_yoy >= 0.20:
                score += 25
            elif metrics.revenue_growth_yoy >= 0.10:
                score += 15
            elif metrics.revenue_growth_yoy >= 0.05:
                score += 5
            elif metrics.revenue_growth_yoy < -0.05:
                score -= 15
        
        # Earnings growth (25 points max)
        if metrics.earnings_growth_yoy is not None:
            if metrics.earnings_growth_yoy >= 0.30:
                score += 25
            elif metrics.earnings_growth_yoy >= 0.15:
                score += 15
            elif metrics.earnings_growth_yoy >= 0.05:
                score += 5
            elif metrics.earnings_growth_yoy < -0.10:
                score -= 15
        
        # 3-year revenue CAGR (20 points max)
        if metrics.revenue_growth_3y_cagr is not None:
            if metrics.revenue_growth_3y_cagr >= 0.25:
                score += 20
            elif metrics.revenue_growth_3y_cagr >= 0.15:
                score += 15
            elif metrics.revenue_growth_3y_cagr >= 0.08:
                score += 8
            elif metrics.revenue_growth_3y_cagr < 0:
                score -= 10
        
        # Rule of 40 check for growth companies (15 points)
        # Revenue Growth % + Profit Margin % >= 40%
        if metrics.revenue_growth_yoy is not None and metrics.profit_margin is not None:
            rule_of_40 = (metrics.revenue_growth_yoy * 100) + (metrics.profit_margin * 100)
            if rule_of_40 >= 40:
                score += 15
            elif rule_of_40 >= 30:
                score += 8
        
        return max(0, min(100, score))
    
    def _calculate_health_score(self, metrics: FundamentalMetrics) -> float:
        """Calculate financial health score (0-100)."""
        score = 50  # Base
        
        # Debt to Equity (25 points max)
        if metrics.debt_to_equity is not None:
            if metrics.debt_to_equity < 0.3:
                score += 25
            elif metrics.debt_to_equity < 0.6:
                score += 20
            elif metrics.debt_to_equity < 1.0:
                score += 10
            elif metrics.debt_to_equity > 2.0:
                score -= 20
        
        # Current Ratio (20 points max)
        if metrics.current_ratio is not None:
            if metrics.current_ratio >= 2.0:
                score += 20
            elif metrics.current_ratio >= 1.5:
                score += 15
            elif metrics.current_ratio >= 1.0:
                score += 5
            elif metrics.current_ratio < 0.8:
                score -= 15
        
        # Interest Coverage (20 points max)
        if metrics.interest_coverage is not None:
            if metrics.interest_coverage >= 10:
                score += 20
            elif metrics.interest_coverage >= 5:
                score += 15
            elif metrics.interest_coverage >= 2:
                score += 5
            elif metrics.interest_coverage < 1.5:
                score -= 15
        
        # Altman Z-Score (20 points max)
        if metrics.altman_z_score is not None:
            if metrics.altman_z_score >= 3.0:
                score += 20
            elif metrics.altman_z_score >= 2.5:
                score += 15
            elif metrics.altman_z_score >= 1.8:
                score += 5
            elif metrics.altman_z_score < 1.5:
                score -= 20
        
        # FCF positive (15 points)
        if metrics.free_cash_flow is not None:
            if metrics.free_cash_flow > 0:
                score += 15
            else:
                score -= 10
        
        return max(0, min(100, score))
    
    def _generate_analysis(self, metrics: FundamentalMetrics):
        """Generate human-readable analysis."""
        strengths = []
        weaknesses = []
        
        # Quality
        if metrics.roic and metrics.roic >= 0.15:
            strengths.append(f"High ROIC ({metrics.roic:.1%}) - efficient capital use")
        elif metrics.roic and metrics.roic < 0.05:
            weaknesses.append(f"Low ROIC ({metrics.roic:.1%})")
        
        if metrics.profit_margin and metrics.profit_margin >= 0.15:
            strengths.append(f"Strong margins ({metrics.profit_margin:.1%})")
        elif metrics.profit_margin and metrics.profit_margin < 0:
            weaknesses.append("Unprofitable")
        
        if metrics.fcf_yield and metrics.fcf_yield >= 0.05:
            strengths.append(f"Good FCF yield ({metrics.fcf_yield:.1%})")
        elif metrics.free_cash_flow and metrics.free_cash_flow < 0:
            weaknesses.append("Negative free cash flow")
        
        # Growth
        if metrics.revenue_growth_yoy and metrics.revenue_growth_yoy >= 0.20:
            strengths.append(f"Strong revenue growth ({metrics.revenue_growth_yoy:.0%})")
        elif metrics.revenue_growth_yoy and metrics.revenue_growth_yoy < -0.05:
            weaknesses.append("Declining revenue")
        
        # Valuation
        if metrics.pe_ratio and metrics.pe_ratio < 15:
            strengths.append(f"Low P/E ({metrics.pe_ratio:.1f})")
        elif metrics.pe_ratio and metrics.pe_ratio > 40:
            weaknesses.append(f"High P/E ({metrics.pe_ratio:.0f})")
        
        if metrics.peg_ratio and metrics.peg_ratio < 1.0:
            strengths.append(f"Attractive PEG ({metrics.peg_ratio:.2f})")
        
        # Health
        if metrics.debt_to_equity is not None and metrics.debt_to_equity < 0.5:
            strengths.append("Low debt")
        elif metrics.debt_to_equity and metrics.debt_to_equity > 1.5:
            weaknesses.append(f"High debt ({metrics.debt_to_equity:.1f}x)")
        
        if metrics.altman_z_score and metrics.altman_z_score >= 3.0:
            strengths.append("Strong financial stability")
        elif metrics.altman_z_score and metrics.altman_z_score < 1.8:
            weaknesses.append("Financial distress risk")
        
        if metrics.piotroski_f_score >= 7:
            strengths.append(f"High Piotroski ({metrics.piotroski_f_score}/9)")
        elif metrics.piotroski_f_score <= 3:
            weaknesses.append(f"Low Piotroski ({metrics.piotroski_f_score}/9)")
        
        metrics.strengths = strengths
        metrics.weaknesses = weaknesses
        
        # Summary
        grade_text = {
            'A': 'Excellent fundamentals',
            'B': 'Good fundamentals',
            'C': 'Average fundamentals',
            'D': 'Below average fundamentals',
            'F': 'Poor fundamentals'
        }
        
        metrics.analysis = f"{grade_text[metrics.fundamental_grade]}. "
        if strengths:
            metrics.analysis += f"Strengths: {', '.join(strengths[:3])}. "
        if weaknesses:
            metrics.analysis += f"Watch: {', '.join(weaknesses[:2])}."
    
    def compare(self, symbols: List[str]) -> pd.DataFrame:
        """Compare fundamentals across multiple stocks."""
        data = []
        
        for symbol in symbols:
            metrics = self.analyze(symbol)
            if metrics:
                data.append({
                    'Symbol': symbol,
                    'Grade': metrics.fundamental_grade,
                    'Overall': metrics.overall_fundamental_score,
                    'Quality': metrics.quality_score,
                    'Value': metrics.value_score,
                    'Growth': metrics.growth_score,
                    'Health': metrics.financial_health_score,
                    'Piotroski': metrics.piotroski_f_score,
                    'ROIC': metrics.roic,
                    'FCF Yield': metrics.fcf_yield,
                    'P/E': metrics.pe_ratio,
                    'PEG': metrics.peg_ratio,
                    'D/E': metrics.debt_to_equity,
                    'Rev Growth': metrics.revenue_growth_yoy,
                })
        
        df = pd.DataFrame(data)
        if not df.empty:
            df = df.sort_values('Overall', ascending=False)
        
        return df


def print_fundamental_report(metrics: FundamentalMetrics):
    """Print detailed fundamental report."""
    m = metrics
    
    print(f"\n{'='*80}")
    print(f"  FUNDAMENTAL ANALYSIS: {m.symbol}")
    print(f"  {m.name}")
    print(f"  Grade: {m.fundamental_grade} | Overall Score: {m.overall_fundamental_score:.0f}/100")
    print(f"{'='*80}")
    
    print(f"\n  COMPANY INFO")
    print(f"  {'─'*40}")
    print(f"  Sector:     {m.sector}")
    print(f"  Industry:   {m.industry}")
    print(f"  Market Cap: ${m.market_cap/1e9:.2f}B")
    print(f"  Price:      ${m.price:.2f}")
    
    print(f"\n  COMPOSITE SCORES")
    print(f"  {'─'*40}")
    print(f"  Quality Score:  {m.quality_score:5.0f}/100")
    print(f"  Value Score:    {m.value_score:5.0f}/100")
    print(f"  Growth Score:   {m.growth_score:5.0f}/100")
    print(f"  Health Score:   {m.financial_health_score:5.0f}/100")
    print(f"  Piotroski:      {m.piotroski_f_score}/9")
    
    print(f"\n  PROFITABILITY")
    print(f"  {'─'*40}")
    roic = f"{m.roic:.1%}" if m.roic else "N/A"
    roe = f"{m.roe:.1%}" if m.roe else "N/A"
    margin = f"{m.profit_margin:.1%}" if m.profit_margin else "N/A"
    fcf_y = f"{m.fcf_yield:.1%}" if m.fcf_yield else "N/A"
    print(f"  ROIC:          {roic:>10}   (>15% excellent)")
    print(f"  ROE:           {roe:>10}   (>15% good)")
    print(f"  Profit Margin: {margin:>10}   (>10% good)")
    print(f"  FCF Yield:     {fcf_y:>10}   (>5% good)")
    
    print(f"\n  VALUATION")
    print(f"  {'─'*40}")
    pe = f"{m.pe_ratio:.1f}" if m.pe_ratio else "N/A"
    peg = f"{m.peg_ratio:.2f}" if m.peg_ratio else "N/A"
    ev_ebitda = f"{m.ev_to_ebitda:.1f}" if m.ev_to_ebitda else "N/A"
    ev_fcf = f"{m.ev_to_fcf:.1f}" if m.ev_to_fcf else "N/A"
    print(f"  P/E:           {pe:>10}   (<20 value)")
    print(f"  PEG:           {peg:>10}   (<1.5 attractive)")
    print(f"  EV/EBITDA:     {ev_ebitda:>10}   (<12 value)")
    print(f"  EV/FCF:        {ev_fcf:>10}   (<20 value)")
    
    print(f"\n  GROWTH")
    print(f"  {'─'*40}")
    rev_g = f"{m.revenue_growth_yoy:.0%}" if m.revenue_growth_yoy else "N/A"
    earn_g = f"{m.earnings_growth_yoy:.0%}" if m.earnings_growth_yoy else "N/A"
    cagr = f"{m.revenue_growth_3y_cagr:.0%}" if m.revenue_growth_3y_cagr else "N/A"
    print(f"  Revenue YoY:   {rev_g:>10}")
    print(f"  Earnings YoY:  {earn_g:>10}")
    print(f"  3Y Rev CAGR:   {cagr:>10}")
    
    print(f"\n  FINANCIAL HEALTH")
    print(f"  {'─'*40}")
    de = f"{m.debt_to_equity:.2f}" if m.debt_to_equity is not None else "N/A"
    cr = f"{m.current_ratio:.2f}" if m.current_ratio else "N/A"
    z = f"{m.altman_z_score:.2f}" if m.altman_z_score else "N/A"
    eq = f"{m.earnings_quality:.2f}" if m.earnings_quality else "N/A"
    print(f"  Debt/Equity:   {de:>10}   (<1.0 low)")
    print(f"  Current Ratio: {cr:>10}   (>1.5 good)")
    print(f"  Altman Z:      {z:>10}   (>3.0 safe)")
    print(f"  Earn Quality:  {eq:>10}   (>1.0 good)")
    
    print(f"\n  ANALYSIS")
    print(f"  {'─'*40}")
    if m.strengths:
        print(f"  ✅ Strengths: {', '.join(m.strengths)}")
    if m.weaknesses:
        print(f"  ⚠️  Watch: {', '.join(m.weaknesses)}")
    
    print(f"\n  {m.analysis}")
    print()
