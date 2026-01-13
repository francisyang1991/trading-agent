#!/usr/bin/env python3
"""
PORTFOLIO MANAGEMENT SYSTEM
============================
Combines stocks to achieve solid returns with proper diversification.

Features:
1. Portfolio construction with sector diversification
2. Correlation analysis to avoid concentrated risk
3. Position sizing (Kelly, vol-targeting, risk parity)
4. Risk budget allocation
5. Rebalancing recommendations
6. Performance simulation

Usage:
    # Build optimal portfolio from scan results
    python tools/portfolio.py --from-scan results/scan_20260111_230124.txt
    
    # Build portfolio from symbols
    python tools/portfolio.py NVDA GOOGL PLTR IONQ XPEV --capital 100000
    
    # Analyze existing portfolio
    python tools/portfolio.py --analyze NVDA:0.3,GOOGL:0.25,AAPL:0.2,MSFT:0.25
    
    # Rebalance recommendations
    python tools/portfolio.py --rebalance
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
import yaml
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StockMetrics:
    """Metrics for a single stock."""
    symbol: str
    price: float
    returns_6m: float
    volatility: float
    sharpe: float
    beta: float
    sector: str
    correlation_spy: float
    
@dataclass
class PortfolioAllocation:
    """Allocation for a portfolio position."""
    symbol: str
    weight: float
    shares: int
    value: float
    sector: str
    contribution_risk: float
    reasoning: str

@dataclass
class PortfolioResult:
    """Complete portfolio analysis result."""
    allocations: List[PortfolioAllocation]
    total_value: float
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float
    max_sector_concentration: float
    diversification_score: float
    correlation_matrix: pd.DataFrame
    risk_metrics: Dict


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_stock_data(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Fetch stock data."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period=period)
        if data.empty or len(data) < 60:
            return None
        return data
    except:
        return None


def get_stock_info(symbol: str) -> Dict:
    """Get stock info including sector."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return {
            'sector': info.get('sector', 'Unknown'),
            'industry': info.get('industry', 'Unknown'),
            'market_cap': info.get('marketCap', 0),
            'name': info.get('shortName', symbol)
        }
    except:
        return {'sector': 'Unknown', 'industry': 'Unknown', 'market_cap': 0, 'name': symbol}


def calculate_returns(data: pd.DataFrame) -> pd.Series:
    """Calculate daily returns."""
    return data['Close'].pct_change().dropna()


def calculate_volatility(returns: pd.Series) -> float:
    """Calculate annualized volatility."""
    return returns.std() * np.sqrt(252) * 100


def calculate_sharpe(returns: pd.Series, rf: float = 0.04) -> float:
    """Calculate Sharpe ratio."""
    if returns.std() == 0:
        return 0
    return np.sqrt(252) * (returns.mean() - rf/252) / returns.std()


def calculate_beta(stock_returns: pd.Series, market_returns: pd.Series) -> float:
    """Calculate beta vs market."""
    if len(stock_returns) != len(market_returns):
        min_len = min(len(stock_returns), len(market_returns))
        stock_returns = stock_returns.tail(min_len)
        market_returns = market_returns.tail(min_len)
    
    covariance = np.cov(stock_returns, market_returns)[0, 1]
    market_variance = np.var(market_returns)
    
    if market_variance == 0:
        return 1.0
    return covariance / market_variance


def calculate_correlation_matrix(returns_dict: Dict[str, pd.Series]) -> pd.DataFrame:
    """Calculate correlation matrix between stocks."""
    returns_df = pd.DataFrame(returns_dict)
    return returns_df.corr()


# ============================================================================
# PORTFOLIO OPTIMIZER
# ============================================================================

class PortfolioOptimizer:
    """Portfolio construction and optimization."""
    
    def __init__(self, capital: float = 100000, max_positions: int = 10):
        self.capital = capital
        self.max_positions = max_positions
        self.spy_returns = None
        self._load_spy_data()
    
    def _load_spy_data(self):
        """Load SPY for beta/correlation calculations."""
        spy_data = get_stock_data("SPY", "1y")
        if spy_data is not None:
            self.spy_returns = calculate_returns(spy_data)
    
    def analyze_stock(self, symbol: str) -> Optional[StockMetrics]:
        """Analyze a single stock."""
        data = get_stock_data(symbol, "1y")
        if data is None:
            return None
        
        returns = calculate_returns(data)
        info = get_stock_info(symbol)
        
        # Calculate metrics
        price = data['Close'].iloc[-1]
        returns_6m = (data['Close'].iloc[-1] / data['Close'].iloc[-126] - 1) * 100 if len(data) >= 126 else 0
        volatility = calculate_volatility(returns)
        sharpe = calculate_sharpe(returns)
        
        # Beta and correlation
        beta = 1.0
        corr_spy = 0.5
        if self.spy_returns is not None:
            beta = calculate_beta(returns, self.spy_returns)
            min_len = min(len(returns), len(self.spy_returns))
            corr_spy = returns.tail(min_len).corr(self.spy_returns.tail(min_len))
        
        return StockMetrics(
            symbol=symbol,
            price=price,
            returns_6m=returns_6m,
            volatility=volatility,
            sharpe=sharpe,
            beta=beta,
            sector=info['sector'],
            correlation_spy=corr_spy
        )
    
    def analyze_stocks(self, symbols: List[str]) -> Dict[str, StockMetrics]:
        """Analyze multiple stocks in parallel."""
        metrics = {}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.analyze_stock, s): s for s in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                result = future.result()
                if result:
                    metrics[symbol] = result
        
        return metrics
    
    def calculate_optimal_weights(
        self,
        metrics: Dict[str, StockMetrics],
        method: str = "risk_parity"
    ) -> Dict[str, float]:
        """Calculate optimal portfolio weights."""
        
        symbols = list(metrics.keys())
        n = len(symbols)
        
        if n == 0:
            return {}
        
        if method == "equal":
            return {s: 1.0 / n for s in symbols}
        
        elif method == "risk_parity":
            # Inverse volatility weighting
            total_inv_vol = sum(1 / m.volatility for m in metrics.values() if m.volatility > 0)
            weights = {}
            for s, m in metrics.items():
                if m.volatility > 0:
                    weights[s] = (1 / m.volatility) / total_inv_vol
                else:
                    weights[s] = 1 / n
            return weights
        
        elif method == "sharpe":
            # Weight by Sharpe ratio (only positive Sharpe)
            positive_sharpe = {s: max(0.1, m.sharpe) for s, m in metrics.items()}
            total_sharpe = sum(positive_sharpe.values())
            return {s: v / total_sharpe for s, v in positive_sharpe.items()}
        
        elif method == "momentum":
            # Weight by 6M momentum (only positive)
            positive_mom = {s: max(0.1, m.returns_6m) for s, m in metrics.items()}
            total_mom = sum(positive_mom.values())
            return {s: v / total_mom for s, v in positive_mom.items()}
        
        elif method == "kelly":
            # Kelly criterion based weights
            weights = {}
            for s, m in metrics.items():
                # Simplified Kelly: f = (p*b - q) / b where p = win_rate, b = avg_win/avg_loss
                # Approximate from Sharpe: f ≈ sharpe / volatility
                if m.volatility > 0:
                    kelly = max(0, min(0.25, m.sharpe / (m.volatility / 100)))
                else:
                    kelly = 0.05
                weights[s] = kelly
            
            # Normalize
            total = sum(weights.values())
            if total > 0:
                weights = {s: w / total for s, w in weights.items()}
            else:
                weights = {s: 1 / n for s in symbols}
            
            return weights
        
        else:
            return {s: 1.0 / n for s in symbols}
    
    def apply_constraints(
        self,
        weights: Dict[str, float],
        metrics: Dict[str, StockMetrics],
        max_position: float = 0.25,
        max_sector: float = 0.40,
        min_position: float = 0.03
    ) -> Dict[str, float]:
        """Apply portfolio constraints."""
        
        # Apply position limits
        constrained = {}
        for s, w in weights.items():
            constrained[s] = max(min_position, min(max_position, w))
        
        # Apply sector limits
        sector_weights = {}
        for s, w in constrained.items():
            sector = metrics[s].sector
            sector_weights[sector] = sector_weights.get(sector, 0) + w
        
        # Scale down over-concentrated sectors
        for sector, total in sector_weights.items():
            if total > max_sector:
                scale = max_sector / total
                for s in constrained:
                    if metrics[s].sector == sector:
                        constrained[s] *= scale
        
        # Normalize to 1.0
        total = sum(constrained.values())
        if total > 0:
            constrained = {s: w / total for s, w in constrained.items()}
        
        return constrained
    
    def build_portfolio(
        self,
        symbols: List[str],
        method: str = "risk_parity",
        max_position: float = 0.25,
        max_sector: float = 0.40
    ) -> PortfolioResult:
        """Build optimal portfolio from symbols."""
        
        print(f"\n📊 Analyzing {len(symbols)} stocks...")
        
        # Analyze all stocks
        metrics = self.analyze_stocks(symbols)
        
        if len(metrics) == 0:
            raise ValueError("No valid stocks found!")
        
        print(f"   ✅ {len(metrics)} stocks analyzed successfully")
        
        # Calculate optimal weights
        weights = self.calculate_optimal_weights(metrics, method)
        
        # Apply constraints
        weights = self.apply_constraints(weights, metrics, max_position, max_sector)
        
        # Build allocations
        allocations = []
        returns_dict = {}
        
        for symbol, weight in weights.items():
            m = metrics[symbol]
            value = self.capital * weight
            shares = int(value / m.price)
            actual_value = shares * m.price
            
            # Get returns for correlation
            data = get_stock_data(symbol, "6mo")
            if data is not None:
                returns_dict[symbol] = calculate_returns(data)
            
            # Risk contribution (simplified: weight * volatility)
            risk_contrib = weight * m.volatility / 100
            
            # Reasoning
            if m.sharpe > 1:
                reason = f"Strong Sharpe ({m.sharpe:.2f})"
            elif m.returns_6m > 30:
                reason = f"High momentum ({m.returns_6m:+.1f}%)"
            elif m.volatility < 30:
                reason = f"Low volatility ({m.volatility:.0f}%)"
            else:
                reason = f"Diversification benefit"
            
            allocations.append(PortfolioAllocation(
                symbol=symbol,
                weight=weight,
                shares=shares,
                value=actual_value,
                sector=m.sector,
                contribution_risk=risk_contrib,
                reasoning=reason
            ))
        
        # Sort by weight
        allocations.sort(key=lambda x: x.weight, reverse=True)
        
        # Calculate portfolio metrics
        total_value = sum(a.value for a in allocations)
        
        # Expected return (weighted average of 6M momentum / 2 for 6M forward)
        exp_return = sum(weights[a.symbol] * metrics[a.symbol].returns_6m / 2 for a in allocations)
        
        # Portfolio volatility (simplified - ignores correlations)
        exp_vol = np.sqrt(sum((weights[a.symbol] * metrics[a.symbol].volatility / 100) ** 2 for a in allocations)) * 100
        
        # Sharpe
        sharpe = (exp_return - 4) / exp_vol if exp_vol > 0 else 0
        
        # Sector concentration
        sector_weights = {}
        for a in allocations:
            sector_weights[a.sector] = sector_weights.get(a.sector, 0) + a.weight
        max_sector_conc = max(sector_weights.values()) if sector_weights else 0
        
        # Diversification score (1 - avg correlation)
        corr_matrix = calculate_correlation_matrix(returns_dict) if returns_dict else pd.DataFrame()
        if not corr_matrix.empty:
            avg_corr = (corr_matrix.sum().sum() - len(corr_matrix)) / (len(corr_matrix) ** 2 - len(corr_matrix))
            div_score = 1 - avg_corr
        else:
            div_score = 0.5
        
        # Risk metrics
        risk_metrics = {
            'total_beta': sum(weights[a.symbol] * metrics[a.symbol].beta for a in allocations),
            'avg_volatility': sum(weights[a.symbol] * metrics[a.symbol].volatility for a in allocations),
            'sector_count': len(sector_weights),
            'position_count': len(allocations),
            'largest_position': max(a.weight for a in allocations),
            'smallest_position': min(a.weight for a in allocations),
        }
        
        return PortfolioResult(
            allocations=allocations,
            total_value=total_value,
            expected_return=exp_return,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe,
            max_sector_concentration=max_sector_conc,
            diversification_score=div_score,
            correlation_matrix=corr_matrix,
            risk_metrics=risk_metrics
        )


# ============================================================================
# OUTPUT FUNCTIONS
# ============================================================================

def print_portfolio(result: PortfolioResult, capital: float):
    """Print portfolio allocation."""
    
    print(f"\n{'='*90}")
    print(f"  PORTFOLIO ALLOCATION")
    print(f"  Capital: ${capital:,.0f}")
    print(f"{'='*90}")
    
    print(f"\n{'Symbol':<8} {'Weight':>8} {'Shares':>8} {'Value':>12} {'Sector':<20} {'Reasoning':<25}")
    print("-" * 90)
    
    for a in result.allocations:
        print(f"{a.symbol:<8} {a.weight*100:>7.1f}% {a.shares:>8} ${a.value:>11,.0f} {a.sector:<20} {a.reasoning:<25}")
    
    print("-" * 90)
    print(f"{'TOTAL':<8} {'100.0':>7}% {'':<8} ${result.total_value:>11,.0f}")
    
    # Portfolio metrics
    print(f"\n{'='*90}")
    print(f"  PORTFOLIO METRICS")
    print(f"{'='*90}")
    print(f"  Expected Return (6M): {result.expected_return:+.1f}%")
    print(f"  Expected Volatility:  {result.expected_volatility:.1f}%")
    print(f"  Sharpe Ratio:         {result.sharpe_ratio:.2f}")
    print(f"  Diversification:      {result.diversification_score:.2f} (higher is better)")
    print(f"  Max Sector Conc.:     {result.max_sector_concentration*100:.1f}%")
    
    # Risk metrics
    print(f"\n  Risk Metrics:")
    print(f"  - Portfolio Beta:     {result.risk_metrics['total_beta']:.2f}")
    print(f"  - Avg Volatility:     {result.risk_metrics['avg_volatility']:.1f}%")
    print(f"  - Sectors:            {result.risk_metrics['sector_count']}")
    print(f"  - Positions:          {result.risk_metrics['position_count']}")
    print(f"  - Largest Position:   {result.risk_metrics['largest_position']*100:.1f}%")
    
    # Sector breakdown
    sector_weights = {}
    for a in result.allocations:
        sector_weights[a.sector] = sector_weights.get(a.sector, 0) + a.weight
    
    print(f"\n  Sector Breakdown:")
    for sector, weight in sorted(sector_weights.items(), key=lambda x: -x[1]):
        bar = "█" * int(weight * 40)
        print(f"  {sector:<25} {weight*100:>5.1f}% {bar}")
    
    # Correlation matrix
    if not result.correlation_matrix.empty and len(result.correlation_matrix) <= 10:
        print(f"\n  Correlation Matrix:")
        print(result.correlation_matrix.round(2).to_string())


def save_portfolio(result: PortfolioResult, capital: float, output_file: str):
    """Save portfolio to file."""
    
    with open(output_file, 'w') as f:
        f.write("=" * 90 + "\n")
        f.write(f"  PORTFOLIO ALLOCATION - Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"  Capital: ${capital:,.0f}\n")
        f.write("=" * 90 + "\n\n")
        
        f.write(f"{'Symbol':<8} {'Weight':>8} {'Shares':>8} {'Value':>12} {'Sector':<20}\n")
        f.write("-" * 70 + "\n")
        
        for a in result.allocations:
            f.write(f"{a.symbol:<8} {a.weight*100:>7.1f}% {a.shares:>8} ${a.value:>11,.0f} {a.sector:<20}\n")
        
        f.write("-" * 70 + "\n")
        f.write(f"{'TOTAL':<8} {'100.0':>7}% {'':<8} ${result.total_value:>11,.0f}\n")
        
        f.write(f"\nPORTFOLIO METRICS:\n")
        f.write(f"Expected Return (6M): {result.expected_return:+.1f}%\n")
        f.write(f"Expected Volatility:  {result.expected_volatility:.1f}%\n")
        f.write(f"Sharpe Ratio:         {result.sharpe_ratio:.2f}\n")
        f.write(f"Portfolio Beta:       {result.risk_metrics['total_beta']:.2f}\n")
    
    print(f"\n✅ Portfolio saved to: {output_file}")


# ============================================================================
# MAIN
# ============================================================================

def load_symbols_from_scan(scan_file: str) -> List[str]:
    """Load BUY symbols from scan results file."""
    symbols = []
    try:
        with open(scan_file, 'r') as f:
            for line in f:
                # Look for lines with [BUY] or [STRONG] action
                if '[BUY]' in line or '[STRONG]' in line:
                    parts = line.split()
                    if parts and parts[0].isalpha() and 1 <= len(parts[0]) <= 5:
                        symbols.append(parts[0])
    except Exception as e:
        print(f"Error reading scan file: {e}")
    return list(set(symbols))  # Remove duplicates


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Portfolio Management System")
    parser.add_argument("symbols", nargs="*", help="Symbols to include")
    parser.add_argument("--from-scan", type=str, help="Load symbols from scan results file")
    parser.add_argument("--capital", type=float, default=100000, help="Investment capital")
    parser.add_argument("--method", type=str, default="risk_parity",
                       choices=["equal", "risk_parity", "sharpe", "momentum", "kelly"],
                       help="Optimization method")
    parser.add_argument("--max-position", type=float, default=0.25, help="Max position size (0-1)")
    parser.add_argument("--max-sector", type=float, default=0.40, help="Max sector concentration (0-1)")
    parser.add_argument("--output", "-o", type=str, help="Output file path")
    
    args = parser.parse_args()
    
    # Get symbols
    if args.from_scan:
        symbols = load_symbols_from_scan(args.from_scan)
        if not symbols:
            print(f"❌ No BUY symbols found in {args.from_scan}")
            return
        print(f"📂 Loaded {len(symbols)} symbols from scan: {', '.join(symbols)}")
    elif args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        # Default high conviction picks
        symbols = ['NVDA', 'GOOGL', 'MSFT', 'PLTR', 'CRWD', 'AXON', 'LLY', 'CAT', 'MELI']
        print(f"📊 Using default high conviction portfolio")
    
    # Build portfolio
    optimizer = PortfolioOptimizer(capital=args.capital)
    
    try:
        result = optimizer.build_portfolio(
            symbols=symbols,
            method=args.method,
            max_position=args.max_position,
            max_sector=args.max_sector
        )
        
        # Print results
        print_portfolio(result, args.capital)
        
        # Save if requested
        if args.output:
            save_portfolio(result, args.capital, args.output)
        else:
            # Auto-save
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = os.path.join(os.path.dirname(__file__), '..', 'results', f'portfolio_{timestamp}.txt')
            save_portfolio(result, args.capital, output_file)
            
    except Exception as e:
        print(f"❌ Error building portfolio: {e}")
        raise


if __name__ == "__main__":
    main()
