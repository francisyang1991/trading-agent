#!/usr/bin/env python3
"""
IBind Trading Integration Example
Shows how to use IBind with the SAIYAN trading system for live trading.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.data.ibind_client import IBindRESTClient
from src.data.data_manager import DataManager
from src.indicators.vpes import VPES
from src.indicators.trend import TrendIndicators
from src.classifier.stock_classifier import StockClassifier
from src.signals.signal_engine import SignalEngine


class IBindTradingSystem:
    """
    Complete trading system using IBind for IBKR connectivity.
    """

    def __init__(self):
        # Initialize IBind client
        self.ibind = IBindRESTClient(
            ibind_account_id=None,  # Auto-detect from IBeam
            host="localhost",       # IBeam host
            port="5000"            # IBeam port
        )

        # Initialize trading components
        self.data_manager = DataManager()
        self.vpes = VPES()
        self.trend = TrendIndicators()
        self.classifier = StockClassifier()
        self.signal_engine = SignalEngine()

        self.authenticated = False

    async def initialize(self) -> bool:
        """Initialize the trading system."""
        print("🚀 Initializing SAIYAN Trading System with IBind")

        # Authenticate with IBKR
        print("🔐 Authenticating with IBKR...")
        self.authenticated = await self.ibind.authenticate()

        if not self.authenticated:
            print("❌ Authentication failed!")
            return False

        print("✅ Authentication successful!")
        return True

    async def analyze_stock(self, symbol: str) -> dict:
        """
        Complete analysis of a stock using all components.

        Args:
            symbol: Stock symbol to analyze

        Returns:
            Analysis results
        """
        print(f"\n📊 Analyzing {symbol}...")

        try:
            # Get historical data
            hist_data = await self.ibind.get_historical_data(
                symbol, period="3m", bar="1d"  # 3 months daily data
            )

            if hist_data.empty:
                return {"error": "No historical data available"}

            print(f"   📈 Historical data: {len(hist_data)} bars")

            # Calculate indicators
            with_vpes = self.vpes.calculate(hist_data)
            with_emas = self.trend.calculate_emas(with_vpes)

            print("   ✅ Indicators calculated")

            # Classify stock
            classification = self.classifier.classify(symbol, with_emas)
            print(f"   🏷️  Classification: {classification.stock_type.value}")

            # Generate trading signal
            signal = self.signal_engine.generate_signal(
                symbol=symbol,
                data=with_emas,
                current_position=0  # Assume no position
            )

            print(f"   🎯 Signal: {signal.signal_type.value}")

            return {
                "symbol": symbol,
                "classification": {
                    "type": classification.stock_type.value,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning
                },
                "signal": {
                    "type": signal.signal_type.value,
                    "strength": signal.strength,
                    "score": signal.total_score,
                    "reasoning": signal.reasoning
                },
                "indicators": {
                    "latest_price": with_emas['close'].iloc[-1],
                    "vpes_score": signal.vpes_score,
                    "trend_score": signal.trend_score,
                    "volume_score": signal.volume_score
                },
                "data": with_emas
            }

        except Exception as e:
            print(f"   ❌ Analysis failed: {e}")
            return {"error": str(e)}

    async def get_portfolio_status(self) -> dict:
        """Get current portfolio status."""
        print("\n💼 Getting Portfolio Status...")

        try:
            # Get account summary
            summary = await self.ibind.get_account_summary()

            # Get positions
            positions = await self.ibind.get_positions()

            # Get open orders
            orders = await self.ibind.get_orders()

            return {
                "account": summary,
                "positions": positions,
                "orders": orders,
                "position_count": len(positions),
                "order_count": len(orders)
            }

        except Exception as e:
            print(f"   ❌ Portfolio status failed: {e}")
            return {"error": str(e)}

    async def execute_signal(self, analysis: dict, paper_trade: bool = True) -> dict:
        """
        Execute a trading signal.

        Args:
            analysis: Analysis results from analyze_stock
            paper_trade: If True, only log the order (don't execute)

        Returns:
            Execution results
        """
        signal = analysis.get("signal", {})
        symbol = analysis.get("symbol")

        if signal.get("type") == "entry_long":
            print(f"\n📈 Executing ENTRY signal for {symbol}")

            if paper_trade:
                print("   📝 PAPER TRADE MODE - Order would be placed:")
                print(f"      BUY 100 shares of {symbol}")
                print("      Reason: {signal.get('reasoning', 'N/A')}")
                return {"status": "paper_trade", "symbol": symbol, "action": "BUY", "quantity": 100}

            else:
                print("   ⚠️  LIVE TRADING MODE - Placing real order!")
                try:
                    # Place market order
                    result = await self.ibind.place_market_order(symbol, 100, "BUY")
                    print(f"   ✅ Order placed: {result}")
                    return {"status": "executed", "result": result}

                except Exception as e:
                    print(f"   ❌ Order failed: {e}")
                    return {"status": "failed", "error": str(e)}

        elif signal.get("type") == "exit_long":
            print(f"\n📉 Executing EXIT signal for {symbol}")

            if paper_trade:
                print("   📝 PAPER TRADE MODE - Order would be placed:")
                print(f"      SELL ALL shares of {symbol}")
                return {"status": "paper_trade", "symbol": symbol, "action": "SELL", "quantity": "ALL"}

            else:
                print("   ⚠️  LIVE TRADING MODE - Placing real order!")
                try:
                    # Get current position
                    positions = await self.ibind.get_positions()
                    position_qty = 0

                    for pos in positions:
                        if pos.get('ticker') == symbol:
                            position_qty = abs(pos.get('position', 0))
                            break

                    if position_qty > 0:
                        result = await self.ibind.place_market_order(symbol, position_qty, "SELL")
                        print(f"   ✅ Order placed: {result}")
                        return {"status": "executed", "result": result}
                    else:
                        print("   ℹ️  No position to sell")
                        return {"status": "no_position"}

                except Exception as e:
                    print(f"   ❌ Order failed: {e}")
                    return {"status": "failed", "error": str(e)}

        else:
            print(f"\n⏸️  No actionable signal for {symbol}: {signal.get('type')}")
            return {"status": "no_action"}


async def main():
    """Run the IBind trading system example."""
    print("🚀 SAIYAN Trading System - IBind Integration")
    print("=" * 60)
    print("This example demonstrates live trading with IBind.")
    print("Make sure IBeam is running: docker compose up -d")
    print("=" * 60)

    # Initialize trading system
    system = IBindTradingSystem()

    if not await system.initialize():
        return

    # Get portfolio status
    portfolio = await system.get_portfolio_status()

    if "error" not in portfolio:
        print(f"💰 Account: {portfolio.get('account', {}).get('netliquidation', {}).get('amount', 'N/A')}")
        print(f"📊 Positions: {portfolio['position_count']}")
        print(f"📋 Orders: {portfolio['order_count']}")

    # Analyze some stocks
    symbols_to_analyze = ["AAPL", "MSFT", "NVDA"]

    for symbol in symbols_to_analyze:
        analysis = await system.analyze_stock(symbol)

        if "error" not in analysis:
            # Print summary
            cls = analysis["classification"]
            sig = analysis["signal"]
            ind = analysis["indicators"]

            print(f"\n📊 {symbol} Summary:")
            print(f"   Type: {cls['type']} ({cls['confidence']:.1f} confidence)")
            print(f"   Signal: {sig['type']} (score: {sig['score']:.1f})")
            print(".2f")

            # Execute signal (paper trading mode)
            await system.execute_signal(analysis, paper_trade=True)

    print("\n" + "=" * 60)
    print("✅ IBind trading example completed!")
    print("To enable live trading, change paper_trade=False")
    print("⚠️  Live trading carries real financial risk!")


if __name__ == "__main__":
    asyncio.run(main())
