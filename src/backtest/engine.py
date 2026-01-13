"""
Backtesting Engine
Simulates trading strategy on historical data with comprehensive metrics.
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from loguru import logger

from ..data.data_manager import BacktestDataManager
from ..indicators.vpes import VPES
from ..indicators.trend import TrendIndicators
from ..indicators.volume import VolumeIndicators
from ..classifier.stock_classifier import StockClassifier, ClassificationResult, StockType
from ..signals.signal_engine import SignalEngine, Signal, SignalType
from ..position.position_manager import PositionManager, Position
from ..position.order_executor import OrderExecutor


@dataclass
class Trade:
    """Individual trade record."""
    symbol: str
    entry_date: datetime
    entry_price: float
    exit_date: Optional[datetime]
    exit_price: float
    quantity: int
    side: str                # "long" or "short"
    pnl: float
    pnl_pct: float
    holding_days: int
    entry_reason: str
    exit_reason: str


@dataclass
class BacktestResult:
    """Comprehensive backtest results."""
    # Period
    start_date: datetime
    end_date: datetime
    trading_days: int
    
    # Returns
    initial_capital: float
    final_capital: float
    total_return: float
    total_return_pct: float
    annualized_return: float
    
    # Risk metrics
    max_drawdown: float
    max_drawdown_pct: float
    max_drawdown_duration: int    # Days
    volatility: float             # Annualized
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    
    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    avg_trade_pnl: float
    avg_holding_days: float
    
    # Position metrics
    max_positions: int
    avg_positions: float
    
    # Trade list
    trades: List[Trade] = field(default_factory=list)
    
    # Equity curve
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    
    # Daily returns
    daily_returns: pd.Series = field(default_factory=pd.Series)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'period': {
                'start_date': self.start_date.isoformat(),
                'end_date': self.end_date.isoformat(),
                'trading_days': self.trading_days
            },
            'returns': {
                'initial_capital': self.initial_capital,
                'final_capital': self.final_capital,
                'total_return': self.total_return,
                'total_return_pct': self.total_return_pct,
                'annualized_return': self.annualized_return
            },
            'risk': {
                'max_drawdown': self.max_drawdown,
                'max_drawdown_pct': self.max_drawdown_pct,
                'max_drawdown_duration': self.max_drawdown_duration,
                'volatility': self.volatility,
                'sharpe_ratio': self.sharpe_ratio,
                'sortino_ratio': self.sortino_ratio,
                'calmar_ratio': self.calmar_ratio
            },
            'trades': {
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'losing_trades': self.losing_trades,
                'win_rate': self.win_rate,
                'avg_win': self.avg_win,
                'avg_loss': self.avg_loss,
                'profit_factor': self.profit_factor,
                'avg_trade_pnl': self.avg_trade_pnl,
                'avg_holding_days': self.avg_holding_days
            },
            'positions': {
                'max_positions': self.max_positions,
                'avg_positions': self.avg_positions
            }
        }
    
    def print_summary(self):
        """Print formatted summary."""
        print("\n" + "="*60)
        print("BACKTEST RESULTS")
        print("="*60)
        
        print(f"\n📅 Period: {self.start_date.date()} to {self.end_date.date()}")
        print(f"   Trading Days: {self.trading_days}")
        
        print(f"\n💰 Returns:")
        print(f"   Initial Capital: ${self.initial_capital:,.2f}")
        print(f"   Final Capital:   ${self.final_capital:,.2f}")
        print(f"   Total Return:    ${self.total_return:,.2f} ({self.total_return_pct:.2f}%)")
        print(f"   Annualized:      {self.annualized_return:.2f}%")
        
        print(f"\n📊 Risk Metrics:")
        print(f"   Max Drawdown:    ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2f}%)")
        print(f"   Volatility:      {self.volatility:.2f}%")
        print(f"   Sharpe Ratio:    {self.sharpe_ratio:.2f}")
        print(f"   Sortino Ratio:   {self.sortino_ratio:.2f}")
        print(f"   Calmar Ratio:    {self.calmar_ratio:.2f}")
        
        print(f"\n📈 Trade Statistics:")
        print(f"   Total Trades:    {self.total_trades}")
        print(f"   Win Rate:        {self.win_rate:.2f}%")
        print(f"   Avg Win:         ${self.avg_win:,.2f}")
        print(f"   Avg Loss:        ${self.avg_loss:,.2f}")
        print(f"   Profit Factor:   {self.profit_factor:.2f}")
        print(f"   Avg Holding:     {self.avg_holding_days:.1f} days")
        
        print("\n" + "="*60)


class BacktestEngine:
    """
    Main backtesting engine.
    Simulates strategy execution on historical data.
    """
    
    def __init__(
        self,
        initial_capital: float = 100000,
        commission: float = 0.001,
        slippage: float = 0.001,
        initial_position_pct: float = 0.05,
        max_position_pct: float = 0.25,
        max_positions: int = 10
    ):
        """
        Initialize backtest engine.
        
        Args:
            initial_capital: Starting capital
            commission: Commission rate
            slippage: Slippage percentage
            initial_position_pct: Initial position size
            max_position_pct: Max position size
            max_positions: Maximum concurrent positions
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.initial_position_pct = initial_position_pct
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        
        # Components
        self.signal_engine = SignalEngine()
        self.classifier = StockClassifier()
        
        # Tracking
        self._trades: List[Trade] = []
        self._equity_curve: List[Dict] = []
        self._daily_positions: List[int] = []
    
    def run(
        self,
        symbols: List[str],
        data: Dict[str, pd.DataFrame],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        multi_tf_data: Optional[Dict[str, Dict[str, pd.DataFrame]]] = None
    ) -> BacktestResult:
        """
        Run backtest.
        
        Args:
            symbols: List of symbols to trade
            data: Dict of {symbol: DataFrame} with OHLCV data
            start_date: Backtest start date
            end_date: Backtest end date
            multi_tf_data: Optional multi-timeframe data
            
        Returns:
            BacktestResult with comprehensive metrics
        """
        logger.info(f"Starting backtest with {len(symbols)} symbols")
        
        # Initialize
        self._trades = []
        self._equity_curve = []
        self._daily_positions = []
        
        # Position manager
        position_manager = PositionManager(
            initial_capital=self.initial_capital,
            initial_position_pct=self.initial_position_pct,
            max_position_pct=self.max_position_pct,
            max_positions=self.max_positions
        )
        
        # Order executor (simulation mode)
        order_executor = OrderExecutor(
            simulation_mode=True,
            commission_rate=self.commission,
            slippage_pct=self.slippage
        )
        
        # Get date range
        all_dates = set()
        for symbol, df in data.items():
            all_dates.update(df.index.tolist())
        
        all_dates = sorted(all_dates)
        
        if start_date:
            # Handle timezone-aware vs naive datetime comparison
            if hasattr(start_date, 'tz') and start_date.tz is not None:
                # start_date is timezone-aware
                all_dates = [d for d in all_dates if d >= start_date]
            else:
                # start_date is timezone-naive, compare with naive version
                all_dates = [d for d in all_dates if d.tz_localize(None) >= start_date]
        if end_date:
            if hasattr(end_date, 'tz') and end_date.tz is not None:
                # end_date is timezone-aware
                all_dates = [d for d in all_dates if d <= end_date]
            else:
                # end_date is timezone-naive, compare with naive version
                all_dates = [d for d in all_dates if d.tz_localize(None) <= end_date]
        
        if not all_dates:
            logger.error("No dates in range")
            return self._empty_result()
        
        start_date = all_dates[0]
        end_date = all_dates[-1]
        
        logger.info(f"Backtest period: {start_date} to {end_date}")
        
        # Main loop
        for i, current_date in enumerate(all_dates):
            # Update positions with current prices
            current_prices = {}
            for symbol in symbols:
                if symbol in data and current_date in data[symbol].index:
                    current_prices[symbol] = data[symbol].loc[current_date, 'close']
            
            position_manager.update_prices(current_prices)
            
            # Check stop orders
            order_executor.check_stop_orders(current_prices)
            
            # Process each symbol
            for symbol in symbols:
                if symbol not in data or current_date not in data[symbol].index:
                    continue
                
                # Get historical data up to current date
                symbol_data = data[symbol].loc[:current_date]
                
                if len(symbol_data) < 60:  # Need minimum history
                    continue
                
                current_price = symbol_data.iloc[-1]['close']
                
                # Get multi-TF data if available
                symbol_multi_tf = None
                if multi_tf_data and symbol in multi_tf_data:
                    symbol_multi_tf = {
                        tf: df.loc[:current_date]
                        for tf, df in multi_tf_data[symbol].items()
                        if current_date in df.index or len(df.loc[:current_date]) > 0
                    }
                
                # Get current position
                position = position_manager.get_position(symbol)
                current_position = position_manager.get_position_pct(symbol) if position else 0
                
                # Generate signal
                signal = self.signal_engine.generate_signal(
                    symbol=symbol,
                    data=symbol_data,
                    multi_tf_data=symbol_multi_tf,
                    current_position=current_position
                )
                
                # Execute based on signal
                self._process_signal(
                    signal=signal,
                    symbol=symbol,
                    current_date=current_date,
                    current_price=current_price,
                    symbol_data=symbol_data,
                    position_manager=position_manager,
                    order_executor=order_executor
                )
            
            # Record equity
            portfolio_value = position_manager.portfolio_value
            for symbol, price in current_prices.items():
                if symbol in position_manager.positions:
                    pos = position_manager.positions[symbol]
                    portfolio_value = position_manager.cash_available + sum(
                        p.current_quantity * current_prices.get(p.symbol, p.avg_entry_price)
                        for p in position_manager.positions.values()
                    )
                    break
            
            self._equity_curve.append({
                'date': current_date,
                'equity': portfolio_value,
                'cash': position_manager.cash_available,
                'positions_value': position_manager.positions_value,
                'num_positions': position_manager.num_positions
            })
            
            self._daily_positions.append(position_manager.num_positions)
        
        # Close all remaining positions
        for symbol in list(position_manager.positions.keys()):
            if symbol in current_prices:
                position = position_manager.positions[symbol]
                pnl = position_manager.close_position(symbol, current_prices[symbol], "End of backtest")
                
                self._trades.append(Trade(
                    symbol=symbol,
                    entry_date=position.entry_date,
                    entry_price=position.entry_price,
                    exit_date=end_date,
                    exit_price=current_prices[symbol],
                    quantity=position.initial_quantity,
                    side="long",
                    pnl=pnl,
                    pnl_pct=(current_prices[symbol] - position.avg_entry_price) / position.avg_entry_price * 100,
                    holding_days=(end_date - position.entry_date).days if isinstance(position.entry_date, datetime) else 0,
                    entry_reason=position.entry_reason,
                    exit_reason="End of backtest"
                ))
        
        # Calculate results
        return self._calculate_results(
            start_date=start_date,
            end_date=end_date,
            final_capital=position_manager.portfolio_value
        )
    
    def _process_signal(
        self,
        signal: Signal,
        symbol: str,
        current_date: datetime,
        current_price: float,
        symbol_data: pd.DataFrame,
        position_manager: PositionManager,
        order_executor: OrderExecutor
    ):
        """Process trading signal."""
        
        position = position_manager.get_position(symbol)
        
        # Entry signal
        if signal.signal_type == SignalType.ENTRY_LONG and not position:
            # Classify stock
            classification = self.classifier.classify(symbol, symbol_data)
            
            if not classification.allow_trend_trade and classification.stock_type != StockType.TYPE_B_RANGE:
                return
            
            # Calculate position size
            quantity, value = position_manager.calculate_position_size(
                symbol, current_price, classification, is_add=False
            )
            
            if quantity <= 0:
                return
            
            # Calculate stop and target
            stop_price = self._calculate_stop_price(symbol_data, current_price)
            target_price = current_price + 2 * (current_price - stop_price)
            
            # Execute order
            result = order_executor.execute_market_order(
                symbol, quantity, "BUY", current_price
            )
            
            if result.success:
                position_manager.open_position(
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=result.filled_price,
                    stop_price=stop_price,
                    target_price=target_price,
                    classification=classification,
                    signal_score=signal.total_score,
                    reason=signal.reasoning
                )
        
        # Add to position
        elif signal.signal_type == SignalType.ADD_LONG and position:
            classification = self.classifier.classify(symbol, symbol_data)
            
            if not classification.allow_add_position:
                return
            
            quantity, value = position_manager.calculate_position_size(
                symbol, current_price, classification, is_add=True
            )
            
            if quantity <= 0:
                return
            
            result = order_executor.execute_market_order(
                symbol, quantity, "BUY", current_price
            )
            
            if result.success:
                position_manager.add_to_position(
                    symbol, quantity, result.filled_price, signal.reasoning
                )
        
        # Exit signals
        elif signal.signal_type in [SignalType.EXIT_LONG, SignalType.PARTIAL_EXIT] and position:
            if signal.signal_type == SignalType.PARTIAL_EXIT:
                # Partial exit
                exit_qty = int(position.current_quantity * 0.5)
            else:
                exit_qty = position.current_quantity
            
            if exit_qty <= 0:
                return
            
            result = order_executor.execute_market_order(
                symbol, exit_qty, "SELL", current_price
            )
            
            if result.success:
                entry_price = position.avg_entry_price
                entry_date = position.entry_date
                
                pnl = position_manager.reduce_position(
                    symbol, exit_qty, result.filled_price, signal.reasoning
                )
                
                if position.current_quantity == 0 or signal.signal_type == SignalType.EXIT_LONG:
                    # Record trade
                    self._trades.append(Trade(
                        symbol=symbol,
                        entry_date=entry_date,
                        entry_price=entry_price,
                        exit_date=current_date,
                        exit_price=result.filled_price,
                        quantity=exit_qty,
                        side="long",
                        pnl=pnl,
                        pnl_pct=(result.filled_price - entry_price) / entry_price * 100,
                        holding_days=(current_date.tz_localize(None) - entry_date).days if isinstance(entry_date, datetime) else 0,
                        entry_reason=position.entry_reason,
                        exit_reason=signal.reasoning
                    ))
        
        # Check stop loss
        if position:
            if current_price <= position.stop_price:
                result = order_executor.execute_market_order(
                    symbol, position.current_quantity, "SELL", current_price
                )
                
                if result.success:
                    pnl = position_manager.close_position(
                        symbol, result.filled_price, "Stop loss triggered"
                    )
                    
                    self._trades.append(Trade(
                        symbol=symbol,
                        entry_date=position.entry_date,
                        entry_price=position.entry_price,
                        exit_date=current_date,
                        exit_price=result.filled_price,
                        quantity=position.initial_quantity,
                        side="long",
                        pnl=pnl,
                        pnl_pct=(result.filled_price - position.avg_entry_price) / position.avg_entry_price * 100,
                        holding_days=(current_date.tz_localize(None) - position.entry_date).days if isinstance(position.entry_date, datetime) else 0,
                        entry_reason=position.entry_reason,
                        exit_reason="Stop loss"
                    ))
    
    def _calculate_stop_price(
        self,
        data: pd.DataFrame,
        entry_price: float
    ) -> float:
        """Calculate stop loss price."""
        # Use recent swing low
        trend = TrendIndicators()
        swing_lows = trend.find_swing_lows(data.tail(30))
        
        if swing_lows:
            return swing_lows[-1][1] * 0.99
        
        # Fallback: ATR-based
        atr = (data['high'] - data['low']).tail(14).mean()
        return entry_price - 2 * atr
    
    def _calculate_results(
        self,
        start_date: datetime,
        end_date: datetime,
        final_capital: float
    ) -> BacktestResult:
        """Calculate comprehensive backtest results."""
        
        # Convert equity curve to DataFrame
        equity_df = pd.DataFrame(self._equity_curve)
        equity_df.set_index('date', inplace=True)
        
        # Calculate daily returns
        equity_df['returns'] = equity_df['equity'].pct_change()
        daily_returns = equity_df['returns'].dropna()
        
        # Basic returns
        total_return = final_capital - self.initial_capital
        total_return_pct = (total_return / self.initial_capital) * 100
        
        # Trading days
        trading_days = len(equity_df)
        years = trading_days / 252
        
        # Annualized return
        if years > 0:
            annualized_return = ((final_capital / self.initial_capital) ** (1 / years) - 1) * 100
        else:
            annualized_return = 0.0
        
        # Drawdown
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = equity_df['equity'] - equity_df['peak']
        equity_df['drawdown_pct'] = equity_df['drawdown'] / equity_df['peak'] * 100
        
        max_drawdown = abs(equity_df['drawdown'].min())
        max_drawdown_pct = abs(equity_df['drawdown_pct'].min())
        
        # Drawdown duration
        in_drawdown = equity_df['drawdown'] < 0
        drawdown_groups = (~in_drawdown).cumsum()
        max_dd_duration = in_drawdown.groupby(drawdown_groups).sum().max()
        
        # Volatility (annualized)
        volatility = daily_returns.std() * np.sqrt(252) * 100
        
        # Sharpe ratio (assuming 0% risk-free rate)
        if volatility > 0:
            sharpe_ratio = (annualized_return) / volatility
        else:
            sharpe_ratio = 0.0
        
        # Sortino ratio (downside deviation)
        downside_returns = daily_returns[daily_returns < 0]
        downside_std = downside_returns.std() * np.sqrt(252) * 100
        if downside_std > 0:
            sortino_ratio = annualized_return / downside_std
        else:
            sortino_ratio = 0.0
        
        # Calmar ratio
        if max_drawdown_pct > 0:
            calmar_ratio = annualized_return / max_drawdown_pct
        else:
            calmar_ratio = 0.0
        
        # Trade statistics
        total_trades = len(self._trades)
        
        if total_trades > 0:
            winning_trades = [t for t in self._trades if t.pnl > 0]
            losing_trades = [t for t in self._trades if t.pnl <= 0]
            
            win_rate = len(winning_trades) / total_trades * 100
            
            avg_win = np.mean([t.pnl for t in winning_trades]) if winning_trades else 0
            avg_loss = abs(np.mean([t.pnl for t in losing_trades])) if losing_trades else 0
            
            gross_profit = sum(t.pnl for t in winning_trades)
            gross_loss = abs(sum(t.pnl for t in losing_trades))
            
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            
            avg_trade_pnl = total_return / total_trades
            avg_holding_days = np.mean([t.holding_days for t in self._trades])
        else:
            win_rate = 0.0
            avg_win = 0.0
            avg_loss = 0.0
            profit_factor = 0.0
            avg_trade_pnl = 0.0
            avg_holding_days = 0.0
            winning_trades = []
            losing_trades = []
        
        # Position metrics
        max_positions = max(self._daily_positions) if self._daily_positions else 0
        avg_positions = np.mean(self._daily_positions) if self._daily_positions else 0
        
        return BacktestResult(
            start_date=start_date,
            end_date=end_date,
            trading_days=trading_days,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            total_return_pct=total_return_pct,
            annualized_return=annualized_return,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            max_drawdown_duration=int(max_dd_duration),
            volatility=volatility,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            total_trades=total_trades,
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            avg_trade_pnl=avg_trade_pnl,
            avg_holding_days=avg_holding_days,
            max_positions=max_positions,
            avg_positions=avg_positions,
            trades=self._trades,
            equity_curve=equity_df,
            daily_returns=daily_returns
        )
    
    def _empty_result(self) -> BacktestResult:
        """Return empty result for failed backtest."""
        return BacktestResult(
            start_date=datetime.now(),
            end_date=datetime.now(),
            trading_days=0,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
            total_return=0,
            total_return_pct=0,
            annualized_return=0,
            max_drawdown=0,
            max_drawdown_pct=0,
            max_drawdown_duration=0,
            volatility=0,
            sharpe_ratio=0,
            sortino_ratio=0,
            calmar_ratio=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            avg_win=0,
            avg_loss=0,
            profit_factor=0,
            avg_trade_pnl=0,
            avg_holding_days=0,
            max_positions=0,
            avg_positions=0
        )
