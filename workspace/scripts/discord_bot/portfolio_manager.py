"""
Portfolio Manager — Mid-Day Review & Risk Management
====================================================
Runs at 12:00 PM ET.
1. Fetches open positions from IBKR via GCloud
2. Analyzes P&L per position
3. Checks against latest pipeline candidates
4. Generates management suggestions (Trim, Raise Stop, Exit, Add)
5. Reports to Discord
"""

import os
import sys
import logging
import asyncio
from datetime import datetime, timezone

# Add path for imports
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

import trade_executor
import signal_pipeline

log = logging.getLogger("portfolio-manager")

async def run_midday_check(channel_send_fn=None):
    """
    Run the mid-day portfolio review.
    """
    log.info("Running mid-day portfolio check...")

    # 1. Fetch positions
    positions_resp = await trade_executor.call_api("/api/positions")
    if isinstance(positions_resp, dict) and "error" in positions_resp:
        log.error(f"Failed to fetch positions: {positions_resp['error']}")
        if channel_send_fn:
            await channel_send_fn(f"⚠️ Mid-day check failed: Could not fetch positions ({positions_resp['error']})")
        return

    positions = positions_resp if isinstance(positions_resp, list) else []

    if not positions:
        log.info("No open positions.")
        if channel_send_fn:
            await channel_send_fn("☀️ **Mid-Day Check**: No open positions.")
        return

    # 2. Load latest candidates
    candidates = signal_pipeline.load_candidates()

    # 3. Generate suggestions
    report = signal_pipeline.format_portfolio_suggestions(positions, candidates)

    # 4. Report
    if channel_send_fn:
        header = f"☀️ **Mid-Day Portfolio Review** | {datetime.now().strftime('%H:%M ET')}\n"
        await channel_send_fn(header + report)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_midday_check())
