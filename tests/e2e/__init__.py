"""
End-to-End Tests for IBKR Paper Trading.

Test Categories:
- Connection tests: test_connection.py
- Market data tests: test_data.py
- Order lifecycle tests: test_orders.py
- Full trading flow tests: test_trading_flow.py
- 24/7 reliability tests: test_reliability.py

Prerequisites:
1. IB Gateway running: docker compose --profile gateway up -d
2. Paper trading account configured in env.list
3. Wait ~120s for IB Gateway to authenticate

Run tests:
    pytest tests/e2e/ -v --tb=short
    pytest tests/e2e/test_connection.py -v -s  # With live output
    pytest tests/e2e/ -v -m "not slow"  # Skip slow tests
"""
