#!/usr/bin/env python3
"""
SAIYAN Trading Agent - Backtest Runner
Run backtests and parameter optimization.
"""

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger

from src.utils.config import load_config, load_symbols
from src.utils.logger import setup_logging
from src.backtest.engine import BacktestEngine
from src.backtest.optimizer import ParameterOptimizer


def load_sample_data(symbols: List[str], data_dir: str = "data/historical") -> Dict[str, pd.DataFrame]:
    """
    Load historical data from CSV files.
    
    Expected format: data/historical/{symbol}.csv with columns:
    date, open, high, low, close, volume
    
    Args:
        symbols: List of symbols to load
        data_dir: Directory containing CSV files
        
    Returns:
        Dict of {symbol: DataFrame}
    """
    data = {}
    data_path = Path(data_dir)
    
    for symbol in symbols:
        file_path = data_path / f"{symbol}.csv"
        
        if not file_path.exists():
            logger.warning(f"Data file not found: {file_path}")
            continue
        
        try:
            df = pd.read_csv(file_path, parse_dates=['date'])
            df = df.rename(columns={'date': 'datetime'})
            df = df.set_index('datetime')
            
            # Ensure required columns
            required = ['open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required):
                logger.warning(f"Missing columns in {symbol} data")
                continue
            
            data[symbol] = df
            logger.info(f"Loaded {len(df)} bars for {symbol}")
            
        except Exception as e:
            logger.error(f"Error loading {symbol}: {e}")
    
    return data


def download_sample_data(symbols: List[str], output_dir: str = "data/historical"):
    """
    Download sample data using yfinance (if available).
    
    Args:
        symbols: Symbols to download
        output_dir: Output directory
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    for symbol in symbols:
        try:
            logger.info(f"Downloading {symbol}...")
            
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="2y")
            
            if df.empty:
                logger.warning(f"No data for {symbol}")
                continue
            
            # Rename columns to lowercase
            df.columns = df.columns.str.lower()
            df.index.name = 'date'
            
            # Keep only needed columns
            df = df[['open', 'high', 'low', 'close', 'volume']]
            
            # Save
            output_file = output_path / f"{symbol}.csv"
            df.to_csv(output_file)
            
            logger.info(f"Saved {len(df)} bars to {output_file}")
            
        except Exception as e:
            logger.error(f"Error downloading {symbol}: {e}")


def run_backtest(
    symbols: List[str],
    data: Dict[str, pd.DataFrame],
    config,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    """
    Run backtest with specified parameters.
    
    Args:
        symbols: Symbols to trade
        data: Historical data
        config: Configuration
        start_date: Backtest start
        end_date: Backtest end
    """
    logger.info("="*60)
    logger.info("RUNNING BACKTEST")
    logger.info("="*60)
    
    # Create backtest engine
    engine = BacktestEngine(
        initial_capital=config.backtest.initial_capital,
        commission=config.backtest.commission,
        slippage=config.backtest.slippage,
        initial_position_pct=config.trading.initial_position_pct,
        max_position_pct=config.trading.max_position_pct,
        max_positions=config.risk.max_positions
    )
    
    # Run backtest
    result = engine.run(
        symbols=symbols,
        data=data,
        start_date=start_date,
        end_date=end_date
    )
    
    # Print results
    result.print_summary()
    
    # Save equity curve
    if not result.equity_curve.empty:
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        
        result.equity_curve.to_csv(
            output_dir / f"equity_curve_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        logger.info(f"Equity curve saved to results/")
    
    return result


def run_optimization(
    symbols: List[str],
    data: Dict[str, pd.DataFrame],
    config,
    method: str = "grid",
    n_trials: int = 50
):
    """
    Run parameter optimization.
    
    Args:
        symbols: Symbols to trade
        data: Historical data
        config: Configuration
        method: "grid" or "bayesian"
        n_trials: Number of trials for bayesian
    """
    logger.info("="*60)
    logger.info("RUNNING PARAMETER OPTIMIZATION")
    logger.info("="*60)
    
    # Create backtest engine
    engine = BacktestEngine(
        initial_capital=config.backtest.initial_capital,
        commission=config.backtest.commission,
        slippage=config.backtest.slippage
    )
    
    # Create optimizer
    optimizer = ParameterOptimizer(
        backtest_engine=engine,
        symbols=symbols,
        data=data,
        optimization_metric="sharpe_ratio"
    )
    
    if method == "grid":
        # Grid search parameters
        param_grid = {
            'initial_position_pct': [0.03, 0.05, 0.08],
            'max_position_pct': [0.15, 0.20, 0.25],
            'min_entry_score': [1.5, 2.0, 2.5],
            'ema_fast': [15, 20, 25],
            'ema_slow': [40, 50, 60],
        }
        
        result = optimizer.grid_search(param_grid)
        
    else:
        # Bayesian optimization parameters
        param_ranges = {
            'initial_position_pct': (0.03, 0.10),
            'max_position_pct': (0.15, 0.30),
            'min_entry_score': (1.5, 3.0),
            'ema_fast': (10, 30),
            'ema_slow': (35, 70),
        }
        
        result = optimizer.bayesian_optimize(
            param_ranges=param_ranges,
            n_trials=n_trials
        )
    
    # Print results
    logger.info("\n" + "="*60)
    logger.info("OPTIMIZATION RESULTS")
    logger.info("="*60)
    logger.info(f"Best {result.optimization_metric}: {result.best_score:.4f}")
    logger.info(f"Best parameters: {result.best_params}")
    logger.info(f"Duration: {result.duration_seconds:.1f} seconds")
    
    # Save results
    results_df = optimizer.get_results_dataframe()
    if not results_df.empty:
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)
        
        results_df.to_csv(
            output_dir / f"optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            index=False
        )
        logger.info("Results saved to results/")
    
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="SAIYAN Backtest Runner")
    
    parser.add_argument(
        "--mode",
        choices=["backtest", "optimize", "download"],
        default="backtest",
        help="Run mode"
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to config file"
    )
    parser.add_argument(
        "--symbols",
        default="config/symbols.yaml",
        help="Path to symbols file"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        help="Single symbol to test (overrides config)"
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--opt-method",
        choices=["grid", "bayesian"],
        default="grid",
        help="Optimization method"
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=50,
        help="Number of optimization trials"
    )
    parser.add_argument(
        "--data-dir",
        default="data/historical",
        help="Historical data directory"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(log_dir="logs", level=args.log_level)
    
    # Load configuration
    config = load_config(args.config)
    symbols_config = load_symbols(args.symbols)
    
    # Get symbols
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = [s['symbol'] for s in symbols_config.get('watchlist', [])]
        excluded = symbols_config.get('excluded', [])
        symbols = [s for s in symbols if s not in excluded]
    
    logger.info(f"Symbols: {symbols[:10]}{'...' if len(symbols) > 10 else ''}")
    
    # Parse dates
    start_date = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
    
    # Execute based on mode
    if args.mode == "download":
        download_sample_data(symbols, args.data_dir)
        
    elif args.mode == "backtest":
        # Load data
        data = load_sample_data(symbols, args.data_dir)
        
        if not data:
            logger.error("No data loaded. Try running with --mode download first.")
            return
        
        run_backtest(
            symbols=list(data.keys()),
            data=data,
            config=config,
            start_date=start_date,
            end_date=end_date
        )
        
    elif args.mode == "optimize":
        # Load data
        data = load_sample_data(symbols, args.data_dir)
        
        if not data:
            logger.error("No data loaded. Try running with --mode download first.")
            return
        
        run_optimization(
            symbols=list(data.keys()),
            data=data,
            config=config,
            method=args.opt_method,
            n_trials=args.n_trials
        )


if __name__ == "__main__":
    main()
