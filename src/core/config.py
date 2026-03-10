"""
System configuration management.

Centralized configuration with validation and defaults.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path
import yaml
import os


@dataclass
class RiskConfig:
    """Risk management configuration."""
    # Position limits
    max_position_pct: float = 0.15
    max_position_value: float = 50000
    min_position_value: float = 1000
    
    # Portfolio limits
    max_positions: int = 20
    max_portfolio_exposure: float = 1.0
    max_sector_exposure: float = 0.30
    max_correlated_exposure: float = 0.50
    
    # Loss limits
    max_daily_loss: float = 0.03
    max_weekly_loss: float = 0.06
    max_drawdown: float = 0.15
    single_trade_max_loss: float = 0.02
    
    # Circuit breakers
    consecutive_loss_halt: int = 5
    intraday_loss_halt: float = 0.02


@dataclass
class SizingConfig:
    """Position sizing configuration."""
    base_size_pct: float = 0.05
    add_size_pct: float = 0.025
    kelly_fraction: float = 0.25
    vol_target: float = 0.15
    min_position_value: float = 1000
    max_positions: int = 20
    
    # Volatility adjustments
    vol_low_multiplier: float = 1.2
    vol_high_multiplier: float = 0.6
    vol_ultra_high_multiplier: float = 0.3


@dataclass
class EntryConfig:
    """Entry criteria configuration."""
    min_score: float = 70
    min_rr_ratio: float = 2.0
    max_dist_from_entry: float = 0.03
    require_regime_confirm: bool = True
    require_volume_confirm: bool = True
    min_volume_ratio: float = 1.2
    
    # RSI limits
    rsi_overbought: float = 70
    rsi_oversold: float = 30


@dataclass
class ExitConfig:
    """Exit criteria configuration."""
    # Profit targets
    target_1_atr_mult: float = 1.5
    target_2_atr_mult: float = 3.0
    partial_exit_pct: float = 0.5
    
    # Stop loss
    initial_stop_atr_mult: float = 2.0
    trailing_stop_atr_mult: float = 2.5
    breakeven_threshold: float = 0.05
    
    # Time exits
    max_holding_days: int = 120
    stale_position_days: int = 30


@dataclass
class IndicatorConfig:
    """Technical indicator configuration."""
    # EMAs
    ema_fast: int = 9
    ema_medium: int = 21
    ema_slow: int = 50
    ema_trend: int = 200
    
    # RSI
    rsi_period: int = 14
    
    # ATR
    atr_period: int = 14
    
    # Volume
    volume_ma_period: int = 20
    volume_spike_threshold: float = 2.0
    
    # VPES
    vpes_volume_ma: int = 20
    vpes_smoothing: int = 5


@dataclass
class BacktestConfig:
    """Backtesting configuration."""
    initial_capital: float = 100000
    commission: float = 0.001
    slippage: float = 0.001
    
    # Walk-forward
    train_period_days: int = 252
    test_period_days: int = 63
    
    # Data
    warmup_bars: int = 200
    min_bars: int = 60


@dataclass
class DataConfig:
    """Data source configuration."""
    provider: str = "yfinance"
    cache_dir: str = "data/cache"
    cache_expiry_hours: int = 24
    
    # Timeframes
    base_timeframe: str = "1d"
    analysis_timeframes: List[str] = field(default_factory=lambda: ["1d", "1wk"])
    
    # Lookback
    default_lookback_days: int = 365
    max_lookback_days: int = 730


@dataclass
class IBKRConfig:
    """Interactive Brokers configuration."""
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    timeout: int = 30
    readonly: bool = False
    
    # Web API
    web_api_url: str = "https://localhost:5000"
    use_web_api: bool = False


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    log_dir: str = "logs"
    trade_log: bool = True
    signal_log: bool = True
    risk_log: bool = True
    max_log_files: int = 30


@dataclass
class SystemConfig:
    """
    Master system configuration.
    
    Combines all configuration sections.
    """
    # Sub-configs
    risk: RiskConfig = field(default_factory=RiskConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    data: DataConfig = field(default_factory=DataConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    # System mode
    mode: str = "paper"  # "paper", "live", "backtest"
    
    # Paths
    config_dir: str = "config"
    data_dir: str = "data"
    results_dir: str = "results"
    
    @classmethod
    def from_yaml(cls, path: str) -> "SystemConfig":
        """Load configuration from YAML file."""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SystemConfig":
        """Create config from dictionary."""
        config = cls()
        
        if 'risk' in data:
            config.risk = RiskConfig(**data['risk'])
        if 'sizing' in data:
            config.sizing = SizingConfig(**data['sizing'])
        if 'entry' in data:
            config.entry = EntryConfig(**data['entry'])
        if 'exit' in data:
            config.exit = ExitConfig(**data['exit'])
        if 'indicators' in data:
            config.indicators = IndicatorConfig(**data['indicators'])
        if 'backtest' in data:
            config.backtest = BacktestConfig(**data['backtest'])
        if 'data' in data:
            config.data = DataConfig(**data['data'])
        if 'ibkr' in data:
            config.ibkr = IBKRConfig(**data['ibkr'])
        if 'logging' in data:
            config.logging = LoggingConfig(**data['logging'])
        
        if 'mode' in data:
            config.mode = data['mode']
        if 'config_dir' in data:
            config.config_dir = data['config_dir']
        if 'data_dir' in data:
            config.data_dir = data['data_dir']
        if 'results_dir' in data:
            config.results_dir = data['results_dir']
        
        return config
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'mode': self.mode,
            'config_dir': self.config_dir,
            'data_dir': self.data_dir,
            'results_dir': self.results_dir,
            'risk': self.risk.__dict__,
            'sizing': self.sizing.__dict__,
            'entry': self.entry.__dict__,
            'exit': self.exit.__dict__,
            'indicators': self.indicators.__dict__,
            'backtest': self.backtest.__dict__,
            'data': self.data.__dict__,
            'ibkr': self.ibkr.__dict__,
            'logging': self.logging.__dict__,
        }
    
    def save_yaml(self, path: str):
        """Save configuration to YAML file."""
        with open(path, 'w') as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False)
    
    def validate(self) -> List[str]:
        """
        Validate configuration.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Risk validation
        if self.risk.max_position_pct > 0.5:
            errors.append("max_position_pct > 50% is very risky")
        if self.risk.max_drawdown > 0.25:
            errors.append("max_drawdown > 25% is aggressive")
        
        # Sizing validation
        if self.sizing.base_size_pct > self.risk.max_position_pct:
            errors.append("base_size_pct exceeds max_position_pct")
        if self.sizing.kelly_fraction > 0.5:
            errors.append("kelly_fraction > 50% is aggressive")
        
        # Entry validation
        if self.entry.min_rr_ratio < 1.0:
            errors.append("min_rr_ratio < 1.0 has negative expectancy")
        
        # Exit validation
        if self.exit.initial_stop_atr_mult < 1.0:
            errors.append("initial_stop_atr_mult < 1.0 will stop out frequently")
        
        return errors


def load_config(config_path: Optional[str] = None) -> SystemConfig:
    """
    Load system configuration.
    
    Args:
        config_path: Path to config file. If None, uses default.
        
    Returns:
        SystemConfig instance
    """
    if config_path is None:
        config_path = os.path.join("config", "settings.yaml")
    
    if os.path.exists(config_path):
        return SystemConfig.from_yaml(config_path)
    
    return SystemConfig()


# Global config instance
_global_config: Optional[SystemConfig] = None


def get_config() -> SystemConfig:
    """Get or create global config."""
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def set_config(config: SystemConfig):
    """Set global config."""
    global _global_config
    _global_config = config


def reset_config():
    """Reset global config (for testing)."""
    global _global_config
    _global_config = None
