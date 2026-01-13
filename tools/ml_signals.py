#!/usr/bin/env python3
"""
ML-Based Trading Signal Generator
==================================
Uses machine learning with EMA, RSI, and ATR features to generate LONG-only trading ideas.

Features (22 total):
- EMA positions (above/below 9, 21, 50, 120, 200)
- EMA distances (% distance from each EMA)
- EMA score (% of EMAs price is above)
- RSI (14-day) + oversold/overbought flags
- ATR (14-day) as % of price
- Volume ratio (current vs 20-day average)
- Price momentum (1-day, 5-day, 20-day returns)
- Volatility (20-day annualized)
- Higher highs / higher lows

Models (Ensemble):
- Logistic Regression (fast, interpretable)
- Random Forest (robust, handles non-linearity)
- Gradient Boosting (high accuracy)

NEW Features:
- Walk-forward backtesting with TimeSeriesSplit
- Feature importance analysis (RF, GB, Permutation)
- Hyperparameter tuning (GridSearchCV, RandomizedSearchCV)

Usage:
    # Basic signal analysis
    python tools/ml_signals.py AAPL MSFT NVDA
    
    # Full analysis (signal + backtest + features)
    python tools/ml_signals.py NVDA --full
    
    # Walk-forward backtesting
    python tools/ml_signals.py NVDA GOOGL PLTR --backtest
    
    # Feature importance analysis
    python tools/ml_signals.py NVDA --features
    
    # Hyperparameter tuning
    python tools/ml_signals.py NVDA --tune
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import (
        train_test_split, cross_val_score, TimeSeriesSplit,
        GridSearchCV, RandomizedSearchCV
    )
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score, f1_score,
        classification_report, confusion_matrix
    )
    from sklearn.inspection import permutation_importance
    SKLEARN_AVAILABLE = True
except ImportError:
    print("⚠️  scikit-learn not installed. Install with: pip install scikit-learn")
    SKLEARN_AVAILABLE = False


class SignalStrength(Enum):
    STRONG_BUY = "🟢🟢 STRONG BUY"
    BUY = "🟢 BUY"
    WEAK_BUY = "🟡 WEAK BUY"
    HOLD = "⚪ HOLD"
    NO_SIGNAL = "❌ NO SIGNAL"


@dataclass
class MLSignal:
    """ML-based trading signal."""
    symbol: str
    signal: SignalStrength
    probability: float
    confidence: float
    features: Dict[str, float]
    model_agreement: float  # % of models that agree
    reasoning: List[str]


@dataclass
class BacktestResult:
    """Results from backtesting ML signals."""
    total_signals: int
    winning_signals: int
    losing_signals: int
    win_rate: float
    avg_return: float
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy: float
    
    
@dataclass
class FeatureImportance:
    """Feature importance analysis results."""
    feature_name: str
    rf_importance: float
    gb_importance: float
    permutation_importance: float
    combined_rank: int


class TechnicalFeatureEngine:
    """Generates ML features from price data."""
    
    def __init__(self, ema_periods: List[int] = [9, 21, 50, 120, 200]):
        self.ema_periods = ema_periods
    
    def calculate_ema(self, data: pd.Series, period: int) -> pd.Series:
        """Calculate EMA."""
        return data.ewm(span=period, adjust=False).mean()
    
    def calculate_rsi(self, data: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI."""
        delta = data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    def calculate_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Calculate ATR."""
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    def generate_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate ML features from price data.
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            DataFrame with features
        """
        df = data.copy()
        
        # Price
        close = df['Close']
        
        # EMAs
        for period in self.ema_periods:
            df[f'EMA_{period}'] = self.calculate_ema(close, period)
            # Position relative to EMA (1 = above, 0 = below)
            df[f'above_EMA_{period}'] = (close > df[f'EMA_{period}']).astype(int)
            # Distance from EMA (%)
            df[f'dist_EMA_{period}'] = (close - df[f'EMA_{period}']) / df[f'EMA_{period}'] * 100
        
        # EMA structure score (how many EMAs price is above)
        df['ema_score'] = sum(df[f'above_EMA_{p}'] for p in self.ema_periods) / len(self.ema_periods)
        
        # RSI
        df['RSI'] = self.calculate_rsi(close)
        df['RSI_oversold'] = (df['RSI'] < 30).astype(int)
        df['RSI_overbought'] = (df['RSI'] > 70).astype(int)
        
        # ATR
        df['ATR'] = self.calculate_atr(df['High'], df['Low'], close)
        df['ATR_pct'] = df['ATR'] / close * 100
        
        # Volume
        df['volume_ma'] = df['Volume'].rolling(window=20).mean()
        df['volume_ratio'] = df['Volume'] / df['volume_ma']
        
        # Price momentum
        df['return_1d'] = close.pct_change(1) * 100
        df['return_5d'] = close.pct_change(5) * 100
        df['return_20d'] = close.pct_change(20) * 100
        
        # Volatility
        df['volatility_20d'] = close.pct_change().rolling(20).std() * np.sqrt(252) * 100
        
        # Higher highs / lower lows
        df['higher_high'] = (df['High'] > df['High'].shift(1)).astype(int)
        df['higher_low'] = (df['Low'] > df['Low'].shift(1)).astype(int)
        
        return df
    
    def generate_target(self, data: pd.DataFrame, forward_days: int = 5, threshold: float = 2.0) -> pd.Series:
        """
        Generate target variable for ML (future return > threshold%).
        
        Args:
            data: DataFrame with price data
            forward_days: Days ahead to look for return
            threshold: Minimum % return to classify as positive
            
        Returns:
            Series with target (1 = buy opportunity, 0 = not)
        """
        future_return = data['Close'].pct_change(forward_days).shift(-forward_days) * 100
        return (future_return > threshold).astype(int)


class MLSignalGenerator:
    """
    ML-based trading signal generator.
    Uses ensemble of models for robust predictions.
    """
    
    def __init__(
        self,
        forward_days: int = 5,
        return_threshold: float = 2.0,
        min_probability: float = 0.6
    ):
        """
        Initialize the signal generator.
        
        Args:
            forward_days: Days ahead to predict
            return_threshold: Min % return for positive classification
            min_probability: Min probability to generate signal
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError("scikit-learn required for ML signals")
        
        self.forward_days = forward_days
        self.return_threshold = return_threshold
        self.min_probability = min_probability
        
        self.feature_engine = TechnicalFeatureEngine()
        self.scaler = StandardScaler()
        
        # Ensemble of models
        self.models = {
            'logistic': LogisticRegression(max_iter=1000, random_state=42),
            'random_forest': RandomForestClassifier(n_estimators=100, random_state=42),
            'gradient_boost': GradientBoostingClassifier(n_estimators=100, random_state=42)
        }
        
        self.feature_columns = None
        self.trained = False
    
    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get list of feature columns."""
        feature_cols = []
        
        # EMA features
        for period in self.feature_engine.ema_periods:
            feature_cols.extend([
                f'above_EMA_{period}',
                f'dist_EMA_{period}'
            ])
        
        # Other features
        feature_cols.extend([
            'ema_score',
            'RSI',
            'RSI_oversold',
            'RSI_overbought',
            'ATR_pct',
            'volume_ratio',
            'return_1d',
            'return_5d',
            'return_20d',
            'volatility_20d',
            'higher_high',
            'higher_low'
        ])
        
        return [c for c in feature_cols if c in df.columns]
    
    def train(self, data: pd.DataFrame) -> Dict[str, float]:
        """
        Train models on historical data.
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            Dict with model accuracies
        """
        # Generate features
        df = self.feature_engine.generate_features(data)
        
        # Generate target
        df['target'] = self.feature_engine.generate_target(
            df, self.forward_days, self.return_threshold
        )
        
        # Drop NaN rows
        df = df.dropna()
        
        # Get feature columns
        self.feature_columns = self._get_feature_columns(df)
        
        # Prepare data
        X = df[self.feature_columns]
        y = df['target']
        
        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        
        # Train-test split
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42
        )
        
        # Train models
        results = {}
        for name, model in self.models.items():
            model.fit(X_train, y_train)
            
            # Evaluate
            y_pred = model.predict(X_test)
            accuracy = accuracy_score(y_test, y_pred)
            precision = precision_score(y_test, y_pred, zero_division=0)
            
            results[name] = {
                'accuracy': accuracy,
                'precision': precision,
                'cross_val': cross_val_score(model, X_scaled, y, cv=5).mean()
            }
        
        self.trained = True
        return results
    
    def predict(self, data: pd.DataFrame) -> Tuple[float, float, Dict[str, float]]:
        """
        Make prediction for current data.
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            Tuple of (avg_probability, model_agreement, individual_probs)
        """
        if not self.trained:
            raise ValueError("Model not trained. Call train() first.")
        
        # Generate features
        df = self.feature_engine.generate_features(data)
        df = df.dropna()
        
        if len(df) == 0:
            return 0.0, 0.0, {}
        
        # Get latest row
        X = df[self.feature_columns].iloc[-1:].values
        X_scaled = self.scaler.transform(X)
        
        # Get predictions from all models
        probabilities = {}
        for name, model in self.models.items():
            prob = model.predict_proba(X_scaled)[0]
            # Probability of positive class (buy signal)
            probabilities[name] = prob[1] if len(prob) > 1 else prob[0]
        
        # Average probability
        avg_prob = np.mean(list(probabilities.values()))
        
        # Model agreement (% of models predicting buy)
        buy_votes = sum(1 for p in probabilities.values() if p > 0.5)
        agreement = buy_votes / len(probabilities)
        
        return avg_prob, agreement, probabilities
    
    def generate_signal(self, symbol: str, data: pd.DataFrame) -> MLSignal:
        """
        Generate trading signal for a stock.
        
        Args:
            symbol: Stock symbol
            data: DataFrame with OHLCV data
            
        Returns:
            MLSignal with trading recommendation
        """
        # Train on historical data
        train_results = self.train(data)
        
        # Get prediction
        probability, agreement, model_probs = self.predict(data)
        
        # Get current features for explanation
        df = self.feature_engine.generate_features(data)
        latest = df.iloc[-1]
        
        features = {
            'RSI': latest.get('RSI', 0),
            'EMA_score': latest.get('ema_score', 0),
            'ATR_pct': latest.get('ATR_pct', 0),
            'volume_ratio': latest.get('volume_ratio', 0),
            'return_5d': latest.get('return_5d', 0),
        }
        
        # Determine signal strength
        reasoning = []
        
        if probability >= 0.75 and agreement >= 0.8:
            signal = SignalStrength.STRONG_BUY
            confidence = probability
            reasoning.append(f"High probability ({probability:.1%}) with strong model agreement ({agreement:.1%})")
        elif probability >= 0.65 and agreement >= 0.6:
            signal = SignalStrength.BUY
            confidence = probability
            reasoning.append(f"Good probability ({probability:.1%}) with majority model agreement")
        elif probability >= 0.55:
            signal = SignalStrength.WEAK_BUY
            confidence = probability * 0.8
            reasoning.append(f"Moderate probability ({probability:.1%}) - proceed with caution")
        elif probability >= 0.4:
            signal = SignalStrength.HOLD
            confidence = 1 - probability
            reasoning.append(f"Low probability ({probability:.1%}) - hold position")
        else:
            signal = SignalStrength.NO_SIGNAL
            confidence = 1 - probability
            reasoning.append(f"Low probability ({probability:.1%}) - no entry recommended")
        
        # Add feature-based reasoning
        if features['RSI'] < 30:
            reasoning.append("RSI oversold - potential bounce")
        elif features['RSI'] > 70:
            reasoning.append("RSI overbought - caution on entry")
        
        if features['EMA_score'] > 0.8:
            reasoning.append("Strong bullish EMA structure (above most EMAs)")
        elif features['EMA_score'] < 0.2:
            reasoning.append("Weak EMA structure (below most EMAs)")
        
        if features['volume_ratio'] > 1.5:
            reasoning.append("High volume - institutional interest")
        
        return MLSignal(
            symbol=symbol,
            signal=signal,
            probability=probability,
            confidence=confidence,
            features=features,
            model_agreement=agreement,
            reasoning=reasoning
        )


# ============================================================================
# BACKTESTING
# ============================================================================

class MLBacktester:
    """
    Walk-forward backtesting for ML signals.
    Tests how well the model would have performed historically.
    """
    
    def __init__(
        self,
        forward_days: int = 5,
        return_threshold: float = 2.0,
        probability_threshold: float = 0.6,
        n_splits: int = 5
    ):
        self.forward_days = forward_days
        self.return_threshold = return_threshold
        self.probability_threshold = probability_threshold
        self.n_splits = n_splits
        self.feature_engine = TechnicalFeatureEngine()
    
    def walk_forward_backtest(self, data: pd.DataFrame) -> BacktestResult:
        """
        Perform walk-forward backtesting.
        
        Uses TimeSeriesSplit to avoid look-ahead bias.
        For each fold:
        1. Train on past data
        2. Generate signals on test period
        3. Track actual returns
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            BacktestResult with performance metrics
        """
        # Generate features and target
        df = self.feature_engine.generate_features(data)
        df['target'] = self.feature_engine.generate_target(
            df, self.forward_days, self.return_threshold
        )
        df['actual_return'] = data['Close'].pct_change(self.forward_days).shift(-self.forward_days) * 100
        df = df.dropna()
        
        feature_cols = self._get_feature_columns(df)
        X = df[feature_cols].values
        y = df['target'].values
        actual_returns = df['actual_return'].values
        
        # Walk-forward split
        tscv = TimeSeriesSplit(n_splits=self.n_splits)
        
        all_trades = []
        
        for train_idx, test_idx in tscv.split(X):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train = y[train_idx]
            test_returns = actual_returns[test_idx]
            
            # Scale
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)
            
            # Train ensemble
            models = {
                'rf': RandomForestClassifier(n_estimators=100, random_state=42),
                'gb': GradientBoostingClassifier(n_estimators=100, random_state=42)
            }
            
            probabilities = np.zeros(len(X_test))
            
            for model in models.values():
                model.fit(X_train_scaled, y_train)
                probs = model.predict_proba(X_test_scaled)
                probabilities += probs[:, 1] if probs.shape[1] > 1 else probs[:, 0]
            
            probabilities /= len(models)
            
            # Generate trades where probability > threshold
            for i, (prob, ret) in enumerate(zip(probabilities, test_returns)):
                if prob >= self.probability_threshold:
                    all_trades.append({
                        'probability': prob,
                        'predicted': 1,
                        'actual_return': ret,
                        'won': ret > 0
                    })
        
        return self._calculate_metrics(all_trades)
    
    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get feature columns."""
        feature_cols = []
        for period in self.feature_engine.ema_periods:
            feature_cols.extend([f'above_EMA_{period}', f'dist_EMA_{period}'])
        feature_cols.extend([
            'ema_score', 'RSI', 'RSI_oversold', 'RSI_overbought',
            'ATR_pct', 'volume_ratio', 'return_1d', 'return_5d',
            'return_20d', 'volatility_20d', 'higher_high', 'higher_low'
        ])
        return [c for c in feature_cols if c in df.columns]
    
    def _calculate_metrics(self, trades: List[Dict]) -> BacktestResult:
        """Calculate backtest metrics from trades."""
        if not trades:
            return BacktestResult(
                total_signals=0, winning_signals=0, losing_signals=0,
                win_rate=0, avg_return=0, total_return=0, max_drawdown=0,
                sharpe_ratio=0, profit_factor=0, avg_win=0, avg_loss=0, expectancy=0
            )
        
        returns = [t['actual_return'] for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        
        total_signals = len(trades)
        winning_signals = len(wins)
        losing_signals = len(losses)
        win_rate = winning_signals / total_signals if total_signals > 0 else 0
        
        avg_return = np.mean(returns) if returns else 0
        total_return = sum(returns)
        
        # Max drawdown
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = running_max - cumulative
        max_drawdown = np.max(drawdown) if len(drawdown) > 0 else 0
        
        # Sharpe ratio (annualized, assuming 252 trading days / forward_days signals per year)
        if len(returns) > 1 and np.std(returns) > 0:
            signals_per_year = 252 / self.forward_days
            sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(signals_per_year)
        else:
            sharpe_ratio = 0
        
        # Profit factor
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0.001
        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 0
        
        # Expectancy (expected $ per trade)
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        return BacktestResult(
            total_signals=total_signals,
            winning_signals=winning_signals,
            losing_signals=losing_signals,
            win_rate=win_rate,
            avg_return=avg_return,
            total_return=total_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=expectancy
        )


# ============================================================================
# FEATURE IMPORTANCE ANALYSIS
# ============================================================================

class FeatureAnalyzer:
    """Analyzes feature importance for ML models."""
    
    def __init__(self):
        self.feature_engine = TechnicalFeatureEngine()
    
    def analyze_importance(self, data: pd.DataFrame) -> List[FeatureImportance]:
        """
        Analyze feature importance using multiple methods.
        
        Methods:
        1. Random Forest feature importance (Gini importance)
        2. Gradient Boosting feature importance
        3. Permutation importance (model-agnostic)
        
        Args:
            data: DataFrame with OHLCV data
            
        Returns:
            List of FeatureImportance sorted by combined rank
        """
        # Generate features
        df = self.feature_engine.generate_features(data)
        df['target'] = self.feature_engine.generate_target(df, 5, 2.0)
        df = df.dropna()
        
        feature_cols = self._get_feature_columns(df)
        X = df[feature_cols]
        y = df['target']
        
        # Scale
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # Split for permutation importance
        X_train, X_test, y_train, y_test = train_test_split(
            X_scaled, y, test_size=0.2, random_state=42
        )
        
        # Train models
        rf = RandomForestClassifier(n_estimators=100, random_state=42)
        gb = GradientBoostingClassifier(n_estimators=100, random_state=42)
        
        rf.fit(X_train, y_train)
        gb.fit(X_train, y_train)
        
        # Get importance scores
        rf_importance = rf.feature_importances_
        gb_importance = gb.feature_importances_
        
        # Permutation importance (slower but more reliable)
        perm_importance = permutation_importance(
            rf, X_test, y_test, n_repeats=10, random_state=42
        )
        perm_scores = perm_importance.importances_mean
        
        # Combine and rank
        results = []
        for i, feature in enumerate(feature_cols):
            results.append({
                'feature': feature,
                'rf': rf_importance[i],
                'gb': gb_importance[i],
                'perm': perm_scores[i]
            })
        
        # Rank by each method
        for method in ['rf', 'gb', 'perm']:
            sorted_by_method = sorted(results, key=lambda x: x[method], reverse=True)
            for rank, item in enumerate(sorted_by_method):
                item[f'{method}_rank'] = rank + 1
        
        # Combined rank (average of all ranks)
        for item in results:
            item['combined_rank'] = (item['rf_rank'] + item['gb_rank'] + item['perm_rank']) / 3
        
        # Sort by combined rank
        results.sort(key=lambda x: x['combined_rank'])
        
        # Convert to dataclass
        importance_list = []
        for i, item in enumerate(results):
            importance_list.append(FeatureImportance(
                feature_name=item['feature'],
                rf_importance=item['rf'],
                gb_importance=item['gb'],
                permutation_importance=item['perm'],
                combined_rank=i + 1
            ))
        
        return importance_list
    
    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get feature columns."""
        feature_cols = []
        for period in self.feature_engine.ema_periods:
            feature_cols.extend([f'above_EMA_{period}', f'dist_EMA_{period}'])
        feature_cols.extend([
            'ema_score', 'RSI', 'RSI_oversold', 'RSI_overbought',
            'ATR_pct', 'volume_ratio', 'return_1d', 'return_5d',
            'return_20d', 'volatility_20d', 'higher_high', 'higher_low'
        ])
        return [c for c in feature_cols if c in df.columns]


# ============================================================================
# HYPERPARAMETER TUNING
# ============================================================================

class HyperparameterTuner:
    """Tunes hyperparameters for ML models using cross-validation."""
    
    def __init__(self):
        self.feature_engine = TechnicalFeatureEngine()
        self.best_params = {}
        self.cv_results = {}
    
    def tune_random_forest(
        self,
        data: pd.DataFrame,
        method: str = "grid",  # "grid" or "random"
        n_iter: int = 20
    ) -> Dict:
        """
        Tune Random Forest hyperparameters.
        
        Args:
            data: OHLCV DataFrame
            method: "grid" for GridSearchCV, "random" for RandomizedSearchCV
            n_iter: Number of iterations for random search
            
        Returns:
            Dict with best params and CV score
        """
        X, y = self._prepare_data(data)
        
        param_grid = {
            'n_estimators': [50, 100, 200],
            'max_depth': [5, 10, 15, None],
            'min_samples_split': [2, 5, 10],
            'min_samples_leaf': [1, 2, 4],
            'max_features': ['sqrt', 'log2', None]
        }
        
        rf = RandomForestClassifier(random_state=42)
        tscv = TimeSeriesSplit(n_splits=5)
        
        if method == "grid":
            search = GridSearchCV(
                rf, param_grid, cv=tscv, scoring='precision',
                n_jobs=-1, verbose=1
            )
        else:
            search = RandomizedSearchCV(
                rf, param_grid, n_iter=n_iter, cv=tscv,
                scoring='precision', n_jobs=-1, verbose=1, random_state=42
            )
        
        search.fit(X, y)
        
        self.best_params['random_forest'] = search.best_params_
        self.cv_results['random_forest'] = {
            'best_score': search.best_score_,
            'best_params': search.best_params_
        }
        
        return self.cv_results['random_forest']
    
    def tune_gradient_boosting(
        self,
        data: pd.DataFrame,
        method: str = "random",
        n_iter: int = 20
    ) -> Dict:
        """
        Tune Gradient Boosting hyperparameters.
        
        Args:
            data: OHLCV DataFrame
            method: "grid" or "random"
            n_iter: Number of iterations for random search
            
        Returns:
            Dict with best params and CV score
        """
        X, y = self._prepare_data(data)
        
        param_grid = {
            'n_estimators': [50, 100, 200],
            'max_depth': [3, 5, 7],
            'learning_rate': [0.01, 0.05, 0.1, 0.2],
            'min_samples_split': [2, 5, 10],
            'min_samples_leaf': [1, 2, 4],
            'subsample': [0.8, 0.9, 1.0]
        }
        
        gb = GradientBoostingClassifier(random_state=42)
        tscv = TimeSeriesSplit(n_splits=5)
        
        if method == "grid":
            search = GridSearchCV(
                gb, param_grid, cv=tscv, scoring='precision',
                n_jobs=-1, verbose=1
            )
        else:
            search = RandomizedSearchCV(
                gb, param_grid, n_iter=n_iter, cv=tscv,
                scoring='precision', n_jobs=-1, verbose=1, random_state=42
            )
        
        search.fit(X, y)
        
        self.best_params['gradient_boosting'] = search.best_params_
        self.cv_results['gradient_boosting'] = {
            'best_score': search.best_score_,
            'best_params': search.best_params_
        }
        
        return self.cv_results['gradient_boosting']
    
    def tune_all(self, data: pd.DataFrame) -> Dict:
        """Tune all models and return best params."""
        print("\n🔧 Tuning Random Forest...")
        rf_result = self.tune_random_forest(data, method="random", n_iter=15)
        
        print("\n🔧 Tuning Gradient Boosting...")
        gb_result = self.tune_gradient_boosting(data, method="random", n_iter=15)
        
        return {
            'random_forest': rf_result,
            'gradient_boosting': gb_result
        }
    
    def _prepare_data(self, data: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Prepare data for tuning."""
        df = self.feature_engine.generate_features(data)
        df['target'] = self.feature_engine.generate_target(df, 5, 2.0)
        df = df.dropna()
        
        feature_cols = self._get_feature_columns(df)
        X = df[feature_cols].values
        y = df['target'].values
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        return X_scaled, y
    
    def _get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """Get feature columns."""
        feature_cols = []
        for period in self.feature_engine.ema_periods:
            feature_cols.extend([f'above_EMA_{period}', f'dist_EMA_{period}'])
        feature_cols.extend([
            'ema_score', 'RSI', 'RSI_oversold', 'RSI_overbought',
            'ATR_pct', 'volume_ratio', 'return_1d', 'return_5d',
            'return_20d', 'volatility_20d', 'higher_high', 'higher_low'
        ])
        return [c for c in feature_cols if c in df.columns]


# ============================================================================
# ENHANCED ANALYSIS FUNCTIONS
# ============================================================================

def run_full_analysis(symbol: str) -> Dict:
    """
    Run complete ML analysis including:
    1. Signal generation
    2. Backtesting
    3. Feature importance
    
    Args:
        symbol: Stock symbol
        
    Returns:
        Dict with all analysis results
    """
    print(f"\n{'='*70}")
    print(f"  FULL ML ANALYSIS: {symbol}")
    print(f"{'='*70}")
    
    # Download data
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="3y")
    
    if data.empty or len(data) < 300:
        print(f"❌ Insufficient data for {symbol}")
        return {}
    
    current_price = data['Close'].iloc[-1]
    print(f"\n📈 {symbol} @ ${current_price:.2f}")
    print(f"   Data points: {len(data)} days")
    
    results = {}
    
    # 1. Generate current signal
    print("\n" + "-" * 50)
    print("📊 CURRENT SIGNAL")
    print("-" * 50)
    
    generator = MLSignalGenerator(forward_days=5, return_threshold=2.0)
    signal = generator.generate_signal(symbol, data)
    results['signal'] = signal
    
    print(f"   Signal: {signal.signal.value}")
    print(f"   Probability: {signal.probability:.1%}")
    print(f"   Model Agreement: {signal.model_agreement:.1%}")
    
    # 2. Backtest
    print("\n" + "-" * 50)
    print("📈 BACKTESTING (Walk-Forward)")
    print("-" * 50)
    
    backtester = MLBacktester(forward_days=5, return_threshold=2.0, probability_threshold=0.6)
    backtest_result = backtester.walk_forward_backtest(data)
    results['backtest'] = backtest_result
    
    print(f"   Total Signals: {backtest_result.total_signals}")
    print(f"   Win Rate: {backtest_result.win_rate:.1%}")
    print(f"   Avg Return: {backtest_result.avg_return:+.2f}%")
    print(f"   Total Return: {backtest_result.total_return:+.1f}%")
    print(f"   Sharpe Ratio: {backtest_result.sharpe_ratio:.2f}")
    print(f"   Profit Factor: {backtest_result.profit_factor:.2f}")
    print(f"   Max Drawdown: {backtest_result.max_drawdown:.1f}%")
    print(f"   Expectancy: {backtest_result.expectancy:+.2f}% per trade")
    
    # 3. Feature Importance
    print("\n" + "-" * 50)
    print("🎯 FEATURE IMPORTANCE (Top 10)")
    print("-" * 50)
    
    analyzer = FeatureAnalyzer()
    importance = analyzer.analyze_importance(data)
    results['feature_importance'] = importance
    
    print(f"   {'Rank':<6} {'Feature':<20} {'RF':>8} {'GB':>8} {'Perm':>8}")
    print(f"   {'-'*50}")
    for fi in importance[:10]:
        print(f"   {fi.combined_rank:<6} {fi.feature_name:<20} "
              f"{fi.rf_importance:>7.3f} {fi.gb_importance:>7.3f} {fi.permutation_importance:>7.3f}")
    
    # Summary assessment
    print("\n" + "-" * 50)
    print("📋 ASSESSMENT")
    print("-" * 50)
    
    if backtest_result.win_rate >= 0.55 and backtest_result.profit_factor >= 1.5:
        assessment = "✅ RELIABLE - Model shows consistent edge"
    elif backtest_result.win_rate >= 0.50 and backtest_result.profit_factor >= 1.0:
        assessment = "⚠️ MARGINAL - Use with caution, weak edge"
    else:
        assessment = "❌ UNRELIABLE - Model doesn't show consistent edge"
    
    print(f"   {assessment}")
    
    # Top drivers
    top_features = [f.feature_name for f in importance[:3]]
    print(f"   Top predictors: {', '.join(top_features)}")
    
    return results


def analyze_stock_ml(symbol: str) -> Optional[MLSignal]:
    """
    Analyze a stock using ML and generate trading signal.
    
    Args:
        symbol: Stock symbol
        
    Returns:
        MLSignal with trading recommendation
    """
    print(f"\n🤖 ML Analysis for {symbol}")
    print("-" * 50)
    
    try:
        # Download data
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="2y")
        
        if data.empty or len(data) < 200:
            print(f"❌ Insufficient data for {symbol}")
            return None
        
        # Get stock info
        info = ticker.info
        stock_name = info.get('shortName', symbol)
        current_price = data['Close'].iloc[-1]
        
        print(f"📈 {stock_name} @ ${current_price:.2f}")
        
        # Generate ML signal
        generator = MLSignalGenerator(
            forward_days=5,
            return_threshold=2.0,
            min_probability=0.6
        )
        
        signal = generator.generate_signal(symbol, data)
        
        # Print results
        print(f"\n📊 ML Signal: {signal.signal.value}")
        print(f"   Probability: {signal.probability:.1%}")
        print(f"   Model Agreement: {signal.model_agreement:.1%}")
        print(f"   Confidence: {signal.confidence:.1%}")
        
        print(f"\n📋 Key Features:")
        print(f"   RSI: {signal.features['RSI']:.1f}")
        print(f"   EMA Score: {signal.features['EMA_score']:.2f}")
        print(f"   ATR %: {signal.features['ATR_pct']:.2f}%")
        print(f"   Volume Ratio: {signal.features['volume_ratio']:.2f}x")
        print(f"   5-Day Return: {signal.features['return_5d']:+.2f}%")
        
        print(f"\n💡 Reasoning:")
        for reason in signal.reasoning:
            print(f"   • {reason}")
        
        return signal
        
    except Exception as e:
        print(f"❌ Error analyzing {symbol}: {e}")
        return None


def print_summary_table(signals: List[MLSignal]):
    """Print summary table of all signals."""
    print("\n" + "=" * 90)
    print("📊 ML SIGNAL SUMMARY (LONG ONLY)")
    print("=" * 90)
    print(f"{'Symbol':<8} {'Signal':<20} {'Prob':<8} {'Agreement':<12} {'RSI':<8} {'EMA Score':<10}")
    print("-" * 90)
    
    for sig in signals:
        if sig:
            signal_str = sig.signal.value.split()[-1]  # Just get BUY/HOLD/etc
            print(f"{sig.symbol:<8} {signal_str:<20} {sig.probability:>6.1%} {sig.model_agreement:>10.1%} "
                  f"{sig.features['RSI']:>6.1f} {sig.features['EMA_score']:>9.2f}")
    
    print("=" * 90)
    
    # Actionable summary
    buy_signals = [s for s in signals if s and s.signal in [SignalStrength.STRONG_BUY, SignalStrength.BUY]]
    if buy_signals:
        print(f"\n🎯 ACTIONABLE BUY SIGNALS ({len(buy_signals)}):")
        for sig in sorted(buy_signals, key=lambda x: x.probability, reverse=True):
            print(f"   • {sig.symbol}: {sig.signal.value} ({sig.probability:.1%} probability)")


def print_backtest_summary(results: Dict[str, BacktestResult]):
    """Print backtest summary table."""
    print("\n" + "=" * 100)
    print("📊 ML BACKTEST SUMMARY")
    print("=" * 100)
    print(f"{'Symbol':<8} {'Signals':>8} {'Win Rate':>10} {'Avg Ret':>10} {'Total Ret':>12} {'Sharpe':>8} {'PF':>8} {'Expectancy':>12}")
    print("-" * 100)
    
    for symbol, result in results.items():
        print(f"{symbol:<8} {result.total_signals:>8} {result.win_rate:>9.1%} "
              f"{result.avg_return:>+9.2f}% {result.total_return:>+11.1f}% "
              f"{result.sharpe_ratio:>7.2f} {result.profit_factor:>7.2f} {result.expectancy:>+11.2f}%")
    
    print("=" * 100)


def main():
    """Main function with extended CLI options."""
    import argparse
    
    if not SKLEARN_AVAILABLE:
        print("❌ scikit-learn not installed. Install with: pip install scikit-learn")
        return
    
    parser = argparse.ArgumentParser(description="ML-Based Trading Signal Generator")
    parser.add_argument("symbols", nargs="*", help="Stock symbols to analyze")
    parser.add_argument("--backtest", "-b", action="store_true", 
                       help="Run walk-forward backtesting")
    parser.add_argument("--features", "-f", action="store_true",
                       help="Analyze feature importance")
    parser.add_argument("--tune", "-t", action="store_true",
                       help="Run hyperparameter tuning")
    parser.add_argument("--full", action="store_true",
                       help="Run full analysis (signal + backtest + features)")
    parser.add_argument("--output", "-o", type=str,
                       help="Output file path")
    
    args = parser.parse_args()
    
    print("\n🤖 SAIYAN ML Trading Signal Generator")
    print("=" * 70)
    print("Using EMA, RSI, ATR features for LONG-only signals")
    print("=" * 70)
    
    # Get symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        symbols = ["AAPL", "MSFT", "NVDA", "GOOGL", "META"]
        print(f"No symbols provided. Using default: {', '.join(symbols)}")
    
    print(f"Analyzing: {', '.join(symbols)}")
    
    # Run full analysis
    if args.full:
        for symbol in symbols:
            run_full_analysis(symbol)
        return
    
    # Run hyperparameter tuning
    if args.tune:
        print("\n🔧 HYPERPARAMETER TUNING")
        print("-" * 50)
        
        # Use first symbol for tuning
        symbol = symbols[0]
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="3y")
        
        if len(data) >= 300:
            tuner = HyperparameterTuner()
            results = tuner.tune_all(data)
            
            print("\n📋 BEST PARAMETERS FOUND:")
            for model, result in results.items():
                print(f"\n   {model}:")
                print(f"   Score: {result['best_score']:.3f}")
                for param, value in result['best_params'].items():
                    print(f"   - {param}: {value}")
        else:
            print(f"❌ Insufficient data for tuning ({len(data)} days, need 300+)")
        return
    
    # Run backtest
    if args.backtest:
        print("\n📈 WALK-FORWARD BACKTESTING")
        print("-" * 50)
        
        backtest_results = {}
        for symbol in symbols:
            print(f"\n   Testing {symbol}...")
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="3y")
            
            if len(data) >= 300:
                backtester = MLBacktester(forward_days=5, return_threshold=2.0)
                result = backtester.walk_forward_backtest(data)
                backtest_results[symbol] = result
            else:
                print(f"   ⚠️ Insufficient data for {symbol}")
        
        if backtest_results:
            print_backtest_summary(backtest_results)
        return
    
    # Run feature importance
    if args.features:
        print("\n🎯 FEATURE IMPORTANCE ANALYSIS")
        print("-" * 50)
        
        for symbol in symbols:
            print(f"\n📊 {symbol}:")
            ticker = yf.Ticker(symbol)
            data = ticker.history(period="2y")
            
            if len(data) >= 200:
                analyzer = FeatureAnalyzer()
                importance = analyzer.analyze_importance(data)
                
                print(f"   {'Rank':<6} {'Feature':<25} {'RF':>8} {'GB':>8} {'Perm':>8}")
                print(f"   {'-'*55}")
                for fi in importance[:12]:
                    print(f"   {fi.combined_rank:<6} {fi.feature_name:<25} "
                          f"{fi.rf_importance:>7.3f} {fi.gb_importance:>7.3f} "
                          f"{fi.permutation_importance:>7.3f}")
            else:
                print(f"   ⚠️ Insufficient data")
        return
    
    # Default: Run signal analysis
    signals = []
    for symbol in symbols:
        signal = analyze_stock_ml(symbol)
        signals.append(signal)
    
    # Print summary
    print_summary_table(signals)
    
    print("\n✅ ML Analysis complete!")
    print("⚠️  Note: ML predictions are for informational purposes only.")
    print("   Always do your own research before trading.")
    print("\n💡 TIP: Use --full for comprehensive analysis with backtesting")


if __name__ == "__main__":
    main()
