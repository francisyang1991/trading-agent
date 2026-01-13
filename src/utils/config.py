"""
Configuration Management
Loads and validates configuration from YAML files.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class IBKRConfig:
    """IBKR connection configuration."""
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    readonly: bool = False
    timeout: int = 30


@dataclass
class TradingConfig:
    """Trading parameters configuration."""
    mode: str = "paper"
    currency: str = "USD"
    initial_position_pct: float = 0.05
    add_position_pct: float = 0.05
    max_position_pct: float = 0.25
    range_trade_max_pct: float = 0.05
    min_volume_ratio: float = 1.5
    min_vpes_threshold: float = 0.005


@dataclass
class TimeframeConfig:
    """Timeframe configuration."""
    base: str = "5 mins"
    analysis: List[str] = field(default_factory=lambda: [
        "5 mins", "15 mins", "30 mins", "1 hour", "4 hours", "1 day", "1 week"
    ])
    weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class IndicatorConfig:
    """Indicator configuration."""
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    vpes_volume_ma: int = 20
    vpes_smoothing: int = 3
    atr_period: int = 14
    volume_ma_period: int = 20


@dataclass
class RiskConfig:
    """Risk management configuration."""
    max_positions: int = 10
    max_sector_exposure: float = 0.30
    max_portfolio_drawdown: float = 0.15
    daily_loss_limit: float = 0.03
    single_trade_loss_limit: float = 0.02
    partial_profit_pct: float = 0.50
    trailing_stop_pct: float = 0.08
    vix_threshold: float = 30
    market_crash_threshold: float = 0.05


@dataclass
class BacktestConfig:
    """Backtest configuration."""
    initial_capital: float = 100000
    commission: float = 0.001
    slippage: float = 0.001
    optimization_method: str = "bayesian"
    n_trials: int = 100


@dataclass 
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    file_rotation: str = "1 day"
    retention: str = "30 days"
    log_signals: bool = True
    log_trades: bool = True
    log_indicators: bool = True
    log_risk_events: bool = True


@dataclass
class Config:
    """Main configuration container."""
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    timeframes: TimeframeConfig = field(default_factory=TimeframeConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # Raw data for custom access
    _raw: Dict = field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get config value by dot-notation key."""
        keys = key.split('.')
        value = self._raw
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value


def load_config(config_path: str = "config/settings.yaml") -> Config:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to settings.yaml
        
    Returns:
        Config object with all settings
    """
    path = Path(config_path)
    
    if not path.exists():
        logger.warning(f"Config file not found: {path}, using defaults")
        return Config()
    
    try:
        with open(path, 'r') as f:
            raw = yaml.safe_load(f)
        
        config = Config()
        config._raw = raw
        
        # Parse IBKR config
        if 'ibkr' in raw:
            config.ibkr = IBKRConfig(**raw['ibkr'])
        
        # Parse trading config
        if 'trading' in raw:
            config.trading = TradingConfig(**{
                k: v for k, v in raw['trading'].items()
                if k in TradingConfig.__dataclass_fields__
            })
        
        # Parse timeframes
        if 'timeframes' in raw:
            tf_data = raw['timeframes']
            config.timeframes = TimeframeConfig(
                base=tf_data.get('base', "5 mins"),
                analysis=tf_data.get('analysis', []),
                weights=tf_data.get('weights', {})
            )
        
        # Parse indicators
        if 'indicators' in raw:
            ind = raw['indicators']
            config.indicators = IndicatorConfig(
                ema_fast=ind.get('ema', {}).get('fast', 20),
                ema_slow=ind.get('ema', {}).get('slow', 50),
                ema_trend=ind.get('ema', {}).get('trend', 200),
                vpes_volume_ma=ind.get('vpes', {}).get('volume_ma_period', 20),
                vpes_smoothing=ind.get('vpes', {}).get('smoothing_period', 3),
                atr_period=ind.get('atr', {}).get('period', 14),
                volume_ma_period=ind.get('volume', {}).get('ma_period', 20)
            )
        
        # Parse risk config
        if 'risk' in raw:
            config.risk = RiskConfig(**{
                k: v for k, v in raw['risk'].items()
                if k in RiskConfig.__dataclass_fields__
            })
        
        # Parse backtest config
        if 'backtest' in raw:
            bt = raw['backtest']
            config.backtest = BacktestConfig(
                initial_capital=bt.get('initial_capital', 100000),
                commission=bt.get('commission', 0.001),
                slippage=bt.get('slippage', 0.001),
                optimization_method=bt.get('optimization', {}).get('method', 'bayesian'),
                n_trials=bt.get('optimization', {}).get('n_trials', 100)
            )
        
        # Parse logging config
        if 'logging' in raw:
            config.logging = LoggingConfig(**{
                k: v for k, v in raw['logging'].items()
                if k in LoggingConfig.__dataclass_fields__
            })
        
        logger.info(f"Configuration loaded from {path}")
        return config
        
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return Config()


def load_symbols(symbols_path: str = "config/symbols.yaml") -> Dict:
    """
    Load symbol configuration.
    
    Args:
        symbols_path: Path to symbols.yaml
        
    Returns:
        Dict with watchlist and sector info
    """
    path = Path(symbols_path)
    
    if not path.exists():
        logger.warning(f"Symbols file not found: {path}")
        return {'watchlist': [], 'sectors': {}, 'overrides': {}, 'excluded': []}
    
    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        
        return {
            'watchlist': data.get('watchlist', []),
            'sectors': data.get('sectors', {}),
            'overrides': data.get('overrides', {}),
            'excluded': [e['symbol'] for e in data.get('excluded', [])]
        }
        
    except Exception as e:
        logger.error(f"Error loading symbols: {e}")
        return {'watchlist': [], 'sectors': {}, 'overrides': {}, 'excluded': []}
