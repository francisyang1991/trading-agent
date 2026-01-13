#!/usr/bin/env python3
"""
SAIYAN Trading Agent - Main Entry Point
Automated Quantitative Trading System for Interactive Brokers
"""

import argparse
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path

from loguru import logger

# Import components
from src.utils.config import load_config, load_symbols, Config
from src.utils.logger import setup_logging, TradeLogger
from src.data.ibkr_client import IBKRClient
from src.data.data_manager import DataManager
from src.indicators.vpes import VPES
from src.indicators.trend import TrendIndicators
from src.indicators.volume import VolumeIndicators
from src.classifier.stock_classifier import StockClassifier, StockType
from src.signals.signal_engine import SignalEngine, SignalType
from src.position.position_manager import PositionManager
from src.position.order_executor import OrderExecutor
from src.risk.risk_manager import RiskManager, RiskLevel


class TradingAgent:
    """
    Main trading agent orchestrator.
    Coordinates all components for live/paper trading.
    """
    
    def __init__(self, config: Config, symbols_config: Dict):
        """
        Initialize trading agent.
        
        Args:
            config: Configuration object
            symbols_config: Symbols configuration
        """
        self.config = config
        self.symbols_config = symbols_config
        
        # Extract symbol list
        self.symbols = [s['symbol'] for s in symbols_config.get('watchlist', [])]
        self.excluded = symbols_config.get('excluded', [])
        self.symbols = [s for s in self.symbols if s not in self.excluded]
        
        # Build sector map
        self.sector_map = {
            s['symbol']: s.get('sector', 'Unknown')
            for s in symbols_config.get('watchlist', [])
        }
        
        # Initialize components
        self._init_components()
        
        # State
        self.running = False
        self.last_update: Dict[str, datetime] = {}
    
    def _init_components(self):
        """Initialize all trading components."""
        
        # IBKR client
        self.ibkr = IBKRClient(
            host=self.config.ibkr.host,
            port=self.config.ibkr.port,
            client_id=self.config.ibkr.client_id,
            readonly=self.config.trading.mode == "readonly",
            timeout=self.config.ibkr.timeout
        )
        
        # Data manager
        self.data_manager = DataManager(
            ibkr_client=self.ibkr,
            cache_dir="data/cache",
            base_timeframe=self.config.timeframes.base
        )
        
        # Indicators
        self.vpes = VPES(
            volume_ma_period=self.config.indicators.vpes_volume_ma,
            smoothing_period=self.config.indicators.vpes_smoothing
        )
        
        self.trend = TrendIndicators(
            ema_fast=self.config.indicators.ema_fast,
            ema_slow=self.config.indicators.ema_slow,
            ema_trend=self.config.indicators.ema_trend
        )
        
        self.volume = VolumeIndicators(
            ma_period=self.config.indicators.volume_ma_period
        )
        
        # Classifier
        self.classifier = StockClassifier(
            type_a_max_position=self.config.trading.max_position_pct,
            type_b_max_position=self.config.trading.range_trade_max_pct
        )
        
        # Signal engine
        self.signal_engine = SignalEngine(
            classifier=self.classifier,
            trend_indicators=self.trend,
            vpes=self.vpes,
            volume_indicators=self.volume
        )
        
        # Position manager
        self.position_manager = PositionManager(
            initial_capital=self.config.backtest.initial_capital,
            initial_position_pct=self.config.trading.initial_position_pct,
            add_position_pct=self.config.trading.add_position_pct,
            max_position_pct=self.config.trading.max_position_pct,
            max_positions=self.config.risk.max_positions,
            state_file="data/positions.json"
        )
        
        # Order executor
        simulation_mode = self.config.trading.mode == "paper"
        self.order_executor = OrderExecutor(
            ibkr_client=self.ibkr if not simulation_mode else None,
            simulation_mode=simulation_mode,
            commission_rate=self.config.backtest.commission,
            slippage_pct=self.config.backtest.slippage
        )
        
        # Risk manager
        self.risk_manager = RiskManager(
            max_position_pct=self.config.trading.max_position_pct,
            max_positions=self.config.risk.max_positions,
            max_sector_exposure=self.config.risk.max_sector_exposure,
            max_portfolio_drawdown=self.config.risk.max_portfolio_drawdown,
            daily_loss_limit=self.config.risk.daily_loss_limit,
            single_trade_loss_limit=self.config.risk.single_trade_loss_limit,
            alert_callback=self._handle_risk_alert
        )
        self.risk_manager.set_sector_map(self.sector_map)
        
        # Trade logger
        self.trade_logger = TradeLogger(log_dir="logs")
    
    def _handle_risk_alert(self, alert):
        """Handle risk alert callback."""
        self.trade_logger.log_risk_event(
            level=alert.level.value,
            category=alert.category,
            message=alert.message,
            value=alert.value,
            threshold=alert.threshold,
            action_taken=alert.action_taken
        )
    
    def connect(self) -> bool:
        """Connect to IBKR."""
        logger.info("Connecting to IBKR...")
        
        if self.ibkr.connect():
            logger.info("Connected to IBKR successfully")
            return True
        else:
            logger.error("Failed to connect to IBKR")
            return False
    
    def disconnect(self):
        """Disconnect from IBKR."""
        self.ibkr.disconnect()
        logger.info("Disconnected from IBKR")
    
    def run(self, interval_seconds: int = 60):
        """
        Main trading loop.
        
        Args:
            interval_seconds: Seconds between iterations
        """
        self.running = True
        logger.info(f"Starting trading loop (interval: {interval_seconds}s)")
        
        while self.running:
            try:
                self._trading_iteration()
            except Exception as e:
                logger.error(f"Error in trading iteration: {e}")
            
            # Wait for next iteration
            time.sleep(interval_seconds)
        
        logger.info("Trading loop stopped")
    
    def _trading_iteration(self):
        """Single trading iteration."""
        logger.debug("Starting trading iteration")
        
        # Get current prices
        current_prices = self._get_current_prices()
        
        if not current_prices:
            logger.warning("No price data available")
            return
        
        # Update positions with prices
        self.position_manager.update_prices(current_prices)
        
        # Check risk
        risk_level, alerts = self.risk_manager.check_all_risks(
            position_manager=self.position_manager,
            current_prices=current_prices
        )
        
        if risk_level == RiskLevel.HALT:
            logger.warning("Trading halted due to risk limits")
            return
        
        # Process each symbol
        for symbol in self.symbols:
            if symbol not in current_prices:
                continue
            
            try:
                self._process_symbol(symbol, current_prices[symbol])
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
        
        # Check stop orders
        self.order_executor.check_stop_orders(current_prices)
        
        # Log portfolio snapshot
        self._log_portfolio_snapshot()
    
    def _process_symbol(self, symbol: str, current_price: float):
        """Process a single symbol."""
        
        # Get data
        data = self.data_manager.get_data(symbol, "1 day", lookback_bars=200)
        
        if data.empty or len(data) < 60:
            return
        
        # Get multi-timeframe data
        multi_tf_data = self.data_manager.get_multi_timeframe_data(
            symbol=symbol,
            timeframes=self.config.timeframes.analysis,
            lookback_bars=100
        )
        
        # Current position
        position = self.position_manager.get_position(symbol)
        current_position_pct = self.position_manager.get_position_pct(symbol)
        
        # Generate signal
        signal = self.signal_engine.generate_signal(
            symbol=symbol,
            data=data,
            multi_tf_data=multi_tf_data,
            current_position=current_position_pct
        )
        
        # Log signal
        if signal.signal_type != SignalType.NO_ACTION:
            self.trade_logger.log_signal(
                symbol=symbol,
                signal_type=signal.signal_type.value,
                score=signal.total_score,
                indicators=signal.indicators,
                classification=signal.stock_type.value,
                reasoning=signal.reasoning
            )
        
        # Execute based on signal
        self._execute_signal(signal, symbol, current_price, data, position)
    
    def _execute_signal(
        self,
        signal,
        symbol: str,
        current_price: float,
        data,
        position
    ):
        """Execute trading signal."""
        
        # Entry
        if signal.signal_type == SignalType.ENTRY_LONG:
            can_open, reason = self.risk_manager.can_open_position(
                symbol, self.position_manager
            )
            
            if not can_open:
                logger.info(f"Cannot open {symbol}: {reason}")
                return
            
            # Classify and calculate size
            classification = self.classifier.classify(symbol, data)
            quantity, value = self.position_manager.calculate_position_size(
                symbol, current_price, classification, is_add=False
            )
            
            if quantity <= 0:
                return
            
            # Calculate stop
            stop_price = self._calculate_stop(data, current_price)
            target_price = current_price + 2 * (current_price - stop_price)
            
            # Execute
            result = self.order_executor.execute_market_order(
                symbol, quantity, "BUY", current_price
            )
            
            if result.success:
                self.position_manager.open_position(
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=result.filled_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    classification=classification,
                    signal_score=signal.total_score,
                    reason=signal.reasoning
                )
                
                self.trade_logger.log_trade(
                    symbol=symbol,
                    action="BUY",
                    quantity=quantity,
                    price=result.filled_price,
                    order_type="market",
                    reason=signal.reasoning,
                    signal_score=signal.total_score,
                    position_pct=self.position_manager.get_position_pct(symbol)
                )
        
        # Add to position
        elif signal.signal_type == SignalType.ADD_LONG and position:
            can_add, reason = self.risk_manager.can_add_to_position(
                symbol, self.position_manager, current_price
            )
            
            if not can_add:
                logger.info(f"Cannot add to {symbol}: {reason}")
                return
            
            classification = self.classifier.classify(symbol, data)
            quantity, value = self.position_manager.calculate_position_size(
                symbol, current_price, classification, is_add=True
            )
            
            if quantity <= 0:
                return
            
            result = self.order_executor.execute_market_order(
                symbol, quantity, "BUY", current_price
            )
            
            if result.success:
                self.position_manager.add_to_position(
                    symbol, quantity, result.filled_price, signal.reasoning
                )
                
                self.trade_logger.log_trade(
                    symbol=symbol,
                    action="ADD",
                    quantity=quantity,
                    price=result.filled_price,
                    order_type="market",
                    reason=signal.reasoning,
                    signal_score=signal.total_score,
                    position_pct=self.position_manager.get_position_pct(symbol)
                )
        
        # Exit
        elif signal.signal_type in [SignalType.EXIT_LONG, SignalType.PARTIAL_EXIT] and position:
            if signal.signal_type == SignalType.PARTIAL_EXIT:
                quantity = int(position.current_quantity * 0.5)
            else:
                quantity = position.current_quantity
            
            if quantity <= 0:
                return
            
            result = self.order_executor.execute_market_order(
                symbol, quantity, "SELL", current_price
            )
            
            if result.success:
                pnl = self.position_manager.reduce_position(
                    symbol, quantity, result.filled_price, signal.reasoning
                )
                
                self.trade_logger.log_trade(
                    symbol=symbol,
                    action="SELL",
                    quantity=quantity,
                    price=result.filled_price,
                    order_type="market",
                    reason=signal.reasoning,
                    pnl=pnl,
                    position_pct=self.position_manager.get_position_pct(symbol)
                )
        
        # Check stop loss
        if position and current_price <= position.stop_price:
            result = self.order_executor.execute_market_order(
                symbol, position.current_quantity, "SELL", current_price
            )
            
            if result.success:
                pnl = self.position_manager.close_position(
                    symbol, result.filled_price, "Stop loss triggered"
                )
                
                self.trade_logger.log_trade(
                    symbol=symbol,
                    action="SELL",
                    quantity=position.current_quantity,
                    price=result.filled_price,
                    order_type="stop",
                    reason="Stop loss",
                    pnl=pnl
                )
    
    def _calculate_stop(self, data, entry_price: float) -> float:
        """Calculate stop loss price."""
        swing_lows = self.trend.find_swing_lows(data.tail(30))
        
        if swing_lows:
            return swing_lows[-1][1] * 0.99
        
        # ATR-based fallback
        atr = (data['high'] - data['low']).tail(14).mean()
        return entry_price - 2 * atr
    
    def _get_current_prices(self) -> Dict[str, float]:
        """Get current prices for all symbols."""
        prices = {}
        
        for symbol in self.symbols:
            data = self.data_manager.get_data(symbol, "1 day", lookback_bars=1)
            if not data.empty:
                prices[symbol] = data['close'].iloc[-1]
        
        return prices
    
    def _log_portfolio_snapshot(self):
        """Log current portfolio state."""
        summary = self.position_manager.get_portfolio_summary()
        
        self.trade_logger.log_portfolio_snapshot(
            equity=summary['portfolio_value'],
            cash=summary['cash'],
            positions=self.position_manager.get_positions_list(),
            drawdown=summary.get('drawdown', 0),
            daily_pnl=summary.get('daily_pnl', 0)
        )
    
    def stop(self):
        """Stop trading loop."""
        self.running = False
        logger.info("Stopping trading agent...")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="SAIYAN Trading Agent")
    parser.add_argument(
        "--mode",
        choices=["paper", "live", "readonly"],
        default="paper",
        help="Trading mode"
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
        "--interval",
        type=int,
        default=60,
        help="Trading interval in seconds"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(log_dir="logs", level=args.log_level)
    
    logger.info("="*60)
    logger.info("SAIYAN Trading Agent Starting")
    logger.info("="*60)
    
    # Load configuration
    config = load_config(args.config)
    config.trading.mode = args.mode
    
    symbols_config = load_symbols(args.symbols)
    
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Symbols: {len(symbols_config.get('watchlist', []))} configured")
    
    # Create agent
    agent = TradingAgent(config, symbols_config)
    
    # Handle shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        agent.stop()
        agent.disconnect()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Connect and run
    if agent.connect():
        try:
            agent.run(interval_seconds=args.interval)
        finally:
            agent.disconnect()
    else:
        logger.error("Failed to connect. Exiting.")
        sys.exit(1)


if __name__ == "__main__":
    main()
