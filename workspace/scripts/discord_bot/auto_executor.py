"""
Auto-Executor — Executes Approved Nightly Trades
================================================
Runs at market open (9:35 AM ET) to execute trades that were approved
during the nightly review.

Flow:
1. Load workspace/data/approved_trades.json
2. Filter for status="pending"
3. Execute via trade_executor (calls GCloud API)
4. Update status to "executed" or "failed"
5. Report results to Discord
"""

import os
import sys
import json
import logging
import asyncio
from datetime import datetime, timezone

# Add path for imports
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

import trade_executor

log = logging.getLogger("auto-executor")

DATA_DIR = os.path.join(os.path.dirname(__file__), '../../data')
APPROVED_FILE = os.path.join(DATA_DIR, 'approved_trades.json')

async def execute_approved_trades(channel_send_fn=None):
    """
    Execute all pending approved trades.

    Args:
        channel_send_fn: async function(msg) to send Discord alerts
    """
    if not os.path.exists(APPROVED_FILE):
        log.info("No approved trades file found.")
        return

    try:
        with open(APPROVED_FILE, 'r') as f:
            trades = json.load(f)
    except Exception as e:
        log.error(f"Error loading approved trades: {e}")
        return

    pending = [t for t in trades if t.get('status') == 'pending']
    if not pending:
        log.info("No pending trades to execute.")
        return

    if channel_send_fn:
        await channel_send_fn(f"🌅 **Market Open Execution**\nProcessing {len(pending)} approved trades...")

    executed_count = 0
    failed_count = 0

    for trade in pending:
        ticker = trade.get('ticker')
        params = trade.get('params')

        log.info(f"Executing approved trade for {ticker}...")

        # Call GCloud API via trade_executor
        result = await trade_executor.call_api("/api/trade", method="POST", payload=params)

        timestamp = datetime.now(timezone.utc).isoformat()

        if result.get("success"):
            trade['status'] = 'executed'
            trade['execution_result'] = result
            trade['executed_at'] = timestamp
            executed_count += 1

            if channel_send_fn:
                # Format success message
                order_id = result.get('order_id')
                price = params.get('price')
                await channel_send_fn(f"✅ **Executed {ticker}**\n   Order ID: {order_id}\n   Limit: ${price}\n   Status: Placed")
        else:
            trade['status'] = 'failed'
            trade['error'] = result.get('error')
            trade['executed_at'] = timestamp
            failed_count += 1

            if channel_send_fn:
                await channel_send_fn(f"❌ **Failed {ticker}**\n   Reason: {result.get('error')}")

        # Small delay to prevent rate limits
        await asyncio.sleep(1)

    # Save updates
    try:
        with open(APPROVED_FILE, 'w') as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        log.error(f"Error saving trade updates: {e}")

    summary = f"🏁 Execution Complete: {executed_count} placed, {failed_count} failed."
    log.info(summary)
    if channel_send_fn:
        await channel_send_fn(summary)

if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.INFO)
    asyncio.run(execute_approved_trades())
