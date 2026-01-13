"""
Logging Configuration and Trade Logger
Comprehensive logging for trading operations.
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
from loguru import logger


def setup_logging(
    log_dir: str = "logs",
    level: str = "INFO",
    rotation: str = "1 day",
    retention: str = "30 days"
):
    """
    Configure logging for the trading system.
    
    Args:
        log_dir: Directory for log files
        level: Log level
        rotation: Log rotation frequency
        retention: Log retention period
    """
    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Remove default handler
    logger.remove()
    
    # Console handler (colorful)
    logger.add(
        sys.stdout,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True
    )
    
    # Main log file
    logger.add(
        log_path / "trading_{time:YYYY-MM-DD}.log",
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation=rotation,
        retention=retention,
        compression="gz"
    )
    
    # Error log file
    logger.add(
        log_path / "errors_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation=rotation,
        retention=retention,
        compression="gz"
    )
    
    # Trade-specific log
    logger.add(
        log_path / "trades_{time:YYYY-MM-DD}.log",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
        filter=lambda record: record["extra"].get("trade_log", False),
        rotation=rotation,
        retention=retention
    )
    
    logger.info("Logging configured successfully")


class TradeLogger:
    """
    Specialized logger for trade records and signal snapshots.
    Maintains structured logs for analysis.
    """
    
    def __init__(self, log_dir: str = "logs", strategy_version: str = "1.0.0"):
        """
        Initialize trade logger.
        
        Args:
            log_dir: Directory for logs
            strategy_version: Current strategy version
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        self.strategy_version = strategy_version
        
        # Trade log file
        self.trade_file = self.log_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        # Signal log file
        self.signal_file = self.log_dir / f"signals_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        # Risk event log
        self.risk_file = self.log_dir / f"risk_events_{datetime.now().strftime('%Y%m%d')}.jsonl"
    
    def log_trade(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        order_type: str,
        reason: str,
        signal_score: float = 0.0,
        pnl: float = 0.0,
        position_pct: float = 0.0,
        metadata: Optional[Dict] = None
    ):
        """
        Log a trade execution.
        
        Args:
            symbol: Stock symbol
            action: BUY, SELL, ADD, REDUCE
            quantity: Shares traded
            price: Execution price
            order_type: market, limit, stop
            reason: Trade reason
            signal_score: Signal score at entry
            pnl: Realized P&L (for exits)
            position_pct: Position % after trade
            metadata: Additional data
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "strategy_version": self.strategy_version,
            "type": "trade",
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "reason": reason,
            "signal_score": signal_score,
            "pnl": pnl,
            "position_pct": position_pct,
            "metadata": metadata or {}
        }
        
        self._write_jsonl(self.trade_file, record)
        
        # Also log to main logger
        logger.bind(trade_log=True).info(
            f"TRADE | {action} {symbol} x{quantity} @ ${price:.2f} | {reason}"
        )
    
    def log_signal(
        self,
        symbol: str,
        signal_type: str,
        score: float,
        indicators: Dict,
        classification: str,
        reasoning: str
    ):
        """
        Log a trading signal.
        
        Args:
            symbol: Stock symbol
            signal_type: Signal type
            score: Signal score
            indicators: Indicator values snapshot
            classification: Stock classification
            reasoning: Signal reasoning
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "strategy_version": self.strategy_version,
            "type": "signal",
            "symbol": symbol,
            "signal_type": signal_type,
            "score": score,
            "classification": classification,
            "indicators": indicators,
            "reasoning": reasoning
        }
        
        self._write_jsonl(self.signal_file, record)
    
    def log_risk_event(
        self,
        level: str,
        category: str,
        message: str,
        value: float,
        threshold: float,
        action_taken: str
    ):
        """
        Log a risk event.
        
        Args:
            level: Risk level
            category: Risk category
            message: Event message
            value: Current value
            threshold: Threshold that was breached
            action_taken: Action taken
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "strategy_version": self.strategy_version,
            "type": "risk_event",
            "level": level,
            "category": category,
            "message": message,
            "value": value,
            "threshold": threshold,
            "action_taken": action_taken
        }
        
        self._write_jsonl(self.risk_file, record)
        
        logger.warning(f"RISK [{level}] | {category}: {message}")
    
    def log_portfolio_snapshot(
        self,
        equity: float,
        cash: float,
        positions: List[Dict],
        drawdown: float,
        daily_pnl: float
    ):
        """
        Log portfolio state snapshot.
        
        Args:
            equity: Total equity
            cash: Available cash
            positions: List of position dicts
            drawdown: Current drawdown
            daily_pnl: Today's P&L
        """
        snapshot_file = self.log_dir / f"portfolio_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        record = {
            "timestamp": datetime.now().isoformat(),
            "equity": equity,
            "cash": cash,
            "positions_count": len(positions),
            "positions": positions,
            "drawdown_pct": drawdown,
            "daily_pnl": daily_pnl
        }
        
        self._write_jsonl(snapshot_file, record)
    
    def _write_jsonl(self, filepath: Path, record: Dict):
        """Write record to JSONL file."""
        try:
            with open(filepath, 'a') as f:
                f.write(json.dumps(record) + '\n')
        except Exception as e:
            logger.error(f"Error writing to {filepath}: {e}")
    
    def get_today_trades(self) -> List[Dict]:
        """Get all trades from today."""
        return self._read_jsonl(self.trade_file)
    
    def get_today_signals(self) -> List[Dict]:
        """Get all signals from today."""
        return self._read_jsonl(self.signal_file)
    
    def _read_jsonl(self, filepath: Path) -> List[Dict]:
        """Read records from JSONL file."""
        records = []
        
        if not filepath.exists():
            return records
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
        
        return records


class PerformanceMonitor:
    """
    Monitors system performance and health.
    """
    
    def __init__(self):
        """Initialize performance monitor."""
        self.metrics: Dict[str, List[float]] = {
            'signal_generation_time': [],
            'order_execution_time': [],
            'data_fetch_time': [],
        }
        self.errors: List[Dict] = []
    
    def record_timing(self, metric: str, duration: float):
        """Record timing metric."""
        if metric not in self.metrics:
            self.metrics[metric] = []
        
        self.metrics[metric].append(duration)
        
        # Keep only last 1000 measurements
        if len(self.metrics[metric]) > 1000:
            self.metrics[metric] = self.metrics[metric][-1000:]
    
    def record_error(self, error: str, context: Dict):
        """Record error occurrence."""
        self.errors.append({
            'timestamp': datetime.now().isoformat(),
            'error': error,
            'context': context
        })
        
        # Keep only last 100 errors
        if len(self.errors) > 100:
            self.errors = self.errors[-100:]
    
    def get_stats(self) -> Dict:
        """Get performance statistics."""
        import numpy as np
        
        stats = {}
        
        for metric, values in self.metrics.items():
            if values:
                stats[metric] = {
                    'mean': float(np.mean(values)),
                    'min': float(np.min(values)),
                    'max': float(np.max(values)),
                    'p95': float(np.percentile(values, 95)),
                    'count': len(values)
                }
        
        stats['error_count'] = len(self.errors)
        
        return stats
