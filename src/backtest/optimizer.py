"""
Parameter Optimizer
Optimizes strategy parameters using grid search or Bayesian optimization.
"""

from typing import Dict, List, Optional, Callable, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger
import itertools

try:
    import optuna
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False

from .engine import BacktestEngine, BacktestResult


@dataclass
class OptimizationResult:
    """Optimization results container."""
    best_params: Dict[str, Any]
    best_score: float
    optimization_metric: str
    all_results: List[Dict]
    n_trials: int
    duration_seconds: float


class ParameterOptimizer:
    """
    Optimizes strategy parameters to maximize performance metrics.
    Supports grid search and Bayesian optimization.
    """
    
    def __init__(
        self,
        backtest_engine: BacktestEngine,
        symbols: List[str],
        data: Dict[str, pd.DataFrame],
        multi_tf_data: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None,
        optimization_metric: str = "sharpe_ratio"
    ):
        """
        Initialize optimizer.
        
        Args:
            backtest_engine: Backtest engine instance
            symbols: Symbols to test
            data: Historical data
            multi_tf_data: Multi-timeframe data
            optimization_metric: Metric to optimize
        """
        self.engine = backtest_engine
        self.symbols = symbols
        self.data = data
        self.multi_tf_data = multi_tf_data
        self.metric = optimization_metric
        
        self._results: List[Dict] = []
    
    def grid_search(
        self,
        param_grid: Dict[str, List[Any]],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> OptimizationResult:
        """
        Perform grid search optimization.
        
        Args:
            param_grid: Dict of {param_name: [values]}
            start_date: Backtest start
            end_date: Backtest end
            
        Returns:
            OptimizationResult with best parameters
        """
        import time
        start_time = time.time()
        
        self._results = []
        
        # Generate all combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))
        
        logger.info(f"Grid search: {len(combinations)} combinations")
        
        best_score = float('-inf')
        best_params = {}
        
        for i, combo in enumerate(combinations):
            params = dict(zip(param_names, combo))
            
            try:
                # Apply parameters
                self._apply_params(params)
                
                # Run backtest
                result = self.engine.run(
                    symbols=self.symbols,
                    data=self.data,
                    start_date=start_date,
                    end_date=end_date,
                    multi_tf_data=self.multi_tf_data
                )
                
                # Get score
                score = self._get_metric_value(result)
                
                # Record
                self._results.append({
                    'params': params.copy(),
                    'score': score,
                    'result': result.to_dict()
                })
                
                # Check if best
                if score > best_score:
                    best_score = score
                    best_params = params.copy()
                
                if (i + 1) % 10 == 0:
                    logger.info(f"Progress: {i+1}/{len(combinations)}, best {self.metric}: {best_score:.4f}")
                    
            except Exception as e:
                logger.warning(f"Error with params {params}: {e}")
        
        duration = time.time() - start_time
        
        logger.info(f"Grid search complete. Best {self.metric}: {best_score:.4f}")
        logger.info(f"Best params: {best_params}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            optimization_metric=self.metric,
            all_results=self._results,
            n_trials=len(combinations),
            duration_seconds=duration
        )
    
    def bayesian_optimize(
        self,
        param_ranges: Dict[str, Tuple[float, float]],
        n_trials: int = 100,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> OptimizationResult:
        """
        Perform Bayesian optimization using Optuna.
        
        Args:
            param_ranges: Dict of {param_name: (min, max)}
            n_trials: Number of optimization trials
            start_date: Backtest start
            end_date: Backtest end
            
        Returns:
            OptimizationResult with best parameters
        """
        if not OPTUNA_AVAILABLE:
            logger.error("Optuna not available. Install with: pip install optuna")
            return self._empty_optimization_result()
        
        import time
        start_time = time.time()
        
        self._results = []
        
        def objective(trial):
            # Sample parameters
            params = {}
            for name, (low, high) in param_ranges.items():
                if isinstance(low, int) and isinstance(high, int):
                    params[name] = trial.suggest_int(name, low, high)
                else:
                    params[name] = trial.suggest_float(name, low, high)
            
            try:
                # Apply parameters
                self._apply_params(params)
                
                # Run backtest
                result = self.engine.run(
                    symbols=self.symbols,
                    data=self.data,
                    start_date=start_date,
                    end_date=end_date,
                    multi_tf_data=self.multi_tf_data
                )
                
                # Get score
                score = self._get_metric_value(result)
                
                # Record
                self._results.append({
                    'params': params.copy(),
                    'score': score,
                    'result': result.to_dict()
                })
                
                return score
                
            except Exception as e:
                logger.warning(f"Trial failed: {e}")
                return float('-inf')
        
        # Create study
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
        
        duration = time.time() - start_time
        
        logger.info(f"Bayesian optimization complete.")
        logger.info(f"Best {self.metric}: {study.best_value:.4f}")
        logger.info(f"Best params: {study.best_params}")
        
        return OptimizationResult(
            best_params=study.best_params,
            best_score=study.best_value,
            optimization_metric=self.metric,
            all_results=self._results,
            n_trials=n_trials,
            duration_seconds=duration
        )
    
    def walk_forward_optimization(
        self,
        param_grid: Dict[str, List[Any]],
        train_period_days: int = 252,
        test_period_days: int = 63,
        n_splits: int = 4
    ) -> List[OptimizationResult]:
        """
        Perform walk-forward optimization.
        
        Args:
            param_grid: Parameter grid
            train_period_days: Training period in days
            test_period_days: Out-of-sample test period
            n_splits: Number of train/test splits
            
        Returns:
            List of OptimizationResult for each fold
        """
        results = []
        
        # Get date range
        all_dates = sorted(set().union(*[set(df.index) for df in self.data.values()]))
        
        total_days = len(all_dates)
        fold_size = (total_days - train_period_days) // n_splits
        
        for i in range(n_splits):
            # Calculate dates
            test_start_idx = train_period_days + i * fold_size
            test_end_idx = min(test_start_idx + test_period_days, total_days - 1)
            train_start_idx = max(0, test_start_idx - train_period_days)
            
            train_start = all_dates[train_start_idx]
            train_end = all_dates[test_start_idx - 1]
            test_start = all_dates[test_start_idx]
            test_end = all_dates[test_end_idx]
            
            logger.info(f"Fold {i+1}/{n_splits}")
            logger.info(f"  Train: {train_start.date()} to {train_end.date()}")
            logger.info(f"  Test:  {test_start.date()} to {test_end.date()}")
            
            # Optimize on training data
            train_result = self.grid_search(
                param_grid=param_grid,
                start_date=train_start,
                end_date=train_end
            )
            
            # Test on out-of-sample
            self._apply_params(train_result.best_params)
            
            test_backtest = self.engine.run(
                symbols=self.symbols,
                data=self.data,
                start_date=test_start,
                end_date=test_end,
                multi_tf_data=self.multi_tf_data
            )
            
            logger.info(f"  OOS {self.metric}: {self._get_metric_value(test_backtest):.4f}")
            
            results.append(train_result)
        
        return results
    
    def _apply_params(self, params: Dict[str, Any]):
        """Apply parameters to backtest engine and signal engine."""
        
        # Position sizing params
        if 'initial_position_pct' in params:
            self.engine.initial_position_pct = params['initial_position_pct']
        if 'max_position_pct' in params:
            self.engine.max_position_pct = params['max_position_pct']
        
        # Signal engine params
        signal_engine = self.engine.signal_engine
        
        if 'min_entry_score' in params:
            signal_engine.min_entry_score = params['min_entry_score']
        if 'min_add_score' in params:
            signal_engine.min_add_score = params['min_add_score']
        if 'min_timeframes_aligned' in params:
            signal_engine.min_tf_aligned = params['min_timeframes_aligned']
        
        # Indicator params
        if 'ema_fast' in params:
            signal_engine.trend.ema_fast_period = params['ema_fast']
        if 'ema_slow' in params:
            signal_engine.trend.ema_slow_period = params['ema_slow']
        
        # VPES params
        if 'vpes_volume_ma' in params:
            signal_engine.vpes.volume_ma_period = params['vpes_volume_ma']
    
    def _get_metric_value(self, result: BacktestResult) -> float:
        """Get optimization metric value from backtest result."""
        
        metric_map = {
            'sharpe_ratio': result.sharpe_ratio,
            'sortino_ratio': result.sortino_ratio,
            'calmar_ratio': result.calmar_ratio,
            'total_return_pct': result.total_return_pct,
            'annualized_return': result.annualized_return,
            'win_rate': result.win_rate,
            'profit_factor': result.profit_factor,
            'max_drawdown_pct': -result.max_drawdown_pct,  # Minimize
        }
        
        return metric_map.get(self.metric, result.sharpe_ratio)
    
    def _empty_optimization_result(self) -> OptimizationResult:
        """Return empty optimization result."""
        return OptimizationResult(
            best_params={},
            best_score=0.0,
            optimization_metric=self.metric,
            all_results=[],
            n_trials=0,
            duration_seconds=0.0
        )
    
    def get_results_dataframe(self) -> pd.DataFrame:
        """Get optimization results as DataFrame."""
        if not self._results:
            return pd.DataFrame()
        
        rows = []
        for r in self._results:
            row = r['params'].copy()
            row['score'] = r['score']
            rows.append(row)
        
        return pd.DataFrame(rows).sort_values('score', ascending=False)
