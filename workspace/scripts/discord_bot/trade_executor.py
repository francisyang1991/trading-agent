"""
Trade Executor
==============
Handles the full trade flow:
  1. Fetch stock data (yfinance)
  2. Compute entry prices (single or scaled)
  3. Call GCP Trading API (/api/trade)
  4. Report results back to Discord channel
  5. Log trades locally

Separated from the bot so the logic is testable and reusable.
"""

import os
import json
import math
import logging
import aiohttp
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional

from trade_parser import TradeCommand

log = logging.getLogger("trade-executor")

# ---------------------------------------------------------------------------
# Config (set via env vars)
# ---------------------------------------------------------------------------
TRADE_API_URL = os.environ.get("TRADE_API_URL", "http://localhost:8080")
TRADE_API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")
TRADE_LOG = os.path.join(os.path.dirname(__file__), "../../data/discord_trades.json")

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
_session: Optional[aiohttp.ClientSession] = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"X-API-Key": TRADE_API_KEY, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
    return _session


async def call_api(endpoint: str, method: str = "GET", payload: dict = None) -> dict:
    """Call the GCP Trading GUI API."""
    url = f"{TRADE_API_URL}{endpoint}"
    session = await _get_session()
    try:
        if method == "POST":
            async with session.post(url, json=payload) as resp:
                return await resp.json()
        else:
            async with session.get(url) as resp:
                return await resp.json()
    except Exception as e:
        log.error(f"Trade API error: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Entry computation
# ---------------------------------------------------------------------------

def compute_entries(stock_ctx: dict, direction: str, num_entries: int = 1) -> dict:
    """
    Compute entry prices from yfinance technical data.

    Returns:
      {
        "entries": [{"price", "pct", "label", "stop_loss"}, ...],
        "target": float,
        "current_price": float,
      }
    """
    price = stock_ctx.get("current_price", 0)
    ema8 = stock_ctx.get("ema8", price)
    ema21 = stock_ctx.get("ema21", price)
    ema50 = stock_ctx.get("ema50", price)

    if direction == "long":
        deep = min(ema50, price * 0.94)
        mid = max(min(ema21, price * 0.985), deep + 0.01)
        near = price * 0.995
        target = round(price * 1.08, 2)

        if num_entries == 1:
            entries = [_e(mid, 100, "Full entry (EMA21 support)", min(ema50, price * 0.95))]
        elif num_entries == 2:
            entries = [
                _e(mid, 40, "Scout — EMA21 support", deep * 0.98),
                _e(near, 60, "Confirm — price holds", mid * 0.98),
            ]
        else:
            entries = [
                _e(deep, 25, "Scout — deep support (EMA50)", deep * 0.96),
                _e(mid, 40, "Core — EMA21 support", deep * 0.98),
                _e(near, 35, "Confirm — momentum", mid * 0.98),
            ]
            if num_entries > 3:
                entries = _distribute(deep, near, num_entries)
    else:
        near_r = price * 1.005
        mid_r = min(max(ema8, price * 1.015), price * 1.04)
        high_r = max(ema21, price * 1.06) if ema21 > price else price * 1.06
        target = round(price * 0.92, 2)

        if num_entries == 1:
            entries = [_e(mid_r, 100, "Full entry (EMA8 resistance)", price * 1.05)]
        elif num_entries == 2:
            entries = [
                _e(near_r, 40, "Scout — initial short", high_r * 1.02),
                _e(mid_r, 60, "Confirm — rejection", mid_r * 1.03),
            ]
        else:
            entries = [
                _e(near_r, 25, "Scout — initial probe", high_r * 1.02),
                _e(mid_r, 40, "Core — EMA8 resistance", mid_r * 1.03),
                _e(high_r, 35, "Confirm — full rejection", high_r * 1.03),
            ]

    return {"entries": entries, "target": target, "current_price": round(price, 2)}


def _e(price, pct, label, stop):
    return {"price": round(price, 2), "pct": pct, "label": label, "stop_loss": round(stop, 2)}


def _distribute(low, high, n):
    step = (high - low) / (n - 1)
    pct = round(100 / n)
    entries = [_e(low + step * i, pct, f"Entry {i+1}/{n}", (low + step * i) * 0.96) for i in range(n)]
    entries[-1]["pct"] = 100 - sum(e["pct"] for e in entries[:-1])
    return entries


# ---------------------------------------------------------------------------
# Execute trade (main entry point)
# ---------------------------------------------------------------------------

async def execute(channel, cmd: TradeCommand, stock_ctx: dict) -> bool:
    """
    Execute a trade command and report results to a Discord channel.

    Args:
        channel: Discord channel to send messages to
        cmd: Parsed TradeCommand
        stock_ctx: yfinance stock context dict
    """
    action = "BUY" if cmd.direction == "long" else "SELL"

    # --- Compute entries ---
    plan = compute_entries(stock_ctx, cmd.direction, cmd.num_entries)
    entries = plan["entries"]
    target = plan["target"]
    current_price = plan["current_price"]

    for e in entries:
        if e["price"] <= 0 or math.isnan(e["price"]):
            await channel.send(f"❌ Invalid entry price for ${cmd.ticker}. Aborting.")
            return False

    # --- Build API payload ---
    payload = {
        "ticker": cmd.ticker,
        "action": action,
        "source": "discord-bot",
        "entries": [
            {"price": e["price"], "pct": e["pct"], "label": e["label"], "stop_loss": e["stop_loss"]}
            for e in entries
        ],
    }

    # Dollar amount: tell GCP to override share count
    if cmd.dollar_amount > 0:
        payload["dollar_amount"] = cmd.dollar_amount

    # Explicit share count: tell GCP to use it directly
    if cmd.share_count > 0:
        payload["share_count"] = cmd.share_count

    # Backward compat for single entry
    if cmd.num_entries == 1:
        payload["limit_price"] = entries[0]["price"]
        payload["stop_loss"] = entries[0]["stop_loss"]
        payload["target"] = target

    # --- Show plan ---
    if cmd.num_entries > 1:
        msg = f"📝 **Scaled entry plan for {cmd.ticker}** ({cmd.num_entries} orders):\n"
        for i, e in enumerate(entries, 1):
            msg += f"   {i}. **{e['pct']}%** @ ${e['price']:.2f} — {e['label']}\n"
        if cmd.dollar_amount > 0:
            msg += f"   💵 Budget: ${cmd.dollar_amount:,.0f}\n"
        msg += f"   🎯 Target: ${target:.2f} | Current: ${current_price:.2f}"
        await channel.send(msg)
    else:
        e = entries[0]
        size_info = ""
        if cmd.dollar_amount > 0:
            size_info = f" (${cmd.dollar_amount:,.0f})"
        elif cmd.share_count > 0:
            size_info = f" ({cmd.share_count} shares)"
        await channel.send(
            f"📝 **{action} {cmd.ticker}{size_info} @ ${e['price']:.2f} LMT**\n"
            f"   Stop: ${e['stop_loss']:.2f} | Target: ${target:.2f} | Current: ${current_price:.2f}"
        )

    # --- Call GCP ---
    result = await call_api("/api/trade", method="POST", payload=payload)

    # --- Report ---
    if result.get("success"):
        await _report_success(channel, cmd, result, entries, target)
        _log_trade(cmd, result, target, success=True)
        return True
    else:
        error = result.get("error", "Unknown error")
        await channel.send(f"\n❌ **{action} {cmd.ticker} FAILED**\n   Reason: {error}")
        _log_trade(cmd, result, target, success=False, error=error)
        return False


async def _report_success(channel, cmd, result, entries, target):
    action = "BUY" if cmd.direction == "long" else "SELL"
    is_scaled = result.get("scaled", False)

    if is_scaled and "orders" in result:
        orders = result["orders"]
        total_shares = result.get("total_shares", 0)
        total_value = result.get("total_value", 0)

        report = f"\n✅ **{action} {cmd.ticker} — scaled entry plan**\n\n"
        for i, o in enumerate(orders, 1):
            if not o.get("success"):
                report += f"   {i}. ❌ FAILED — {o.get('error', '?')}\n"
            elif o.get("status") == "Conditional":
                report += (
                    f"   {i}. ⏳ **{o['shares']} sh @ ${o['price']:.2f}** "
                    f"({o.get('pct', 0)}%) [{o.get('label', '')}]\n"
                    f"      *Conditional* — placed after prior entry fills + confirms\n"
                )
            else:
                report += (
                    f"   {i}. **{o['shares']} sh @ ${o['price']:.2f}** "
                    f"({o.get('pct', 0)}%) [{o.get('label', '')}]\n"
                    f"      Order **{o['status']}** (ID: {o['order_id']})\n"
                )
        report += f"\n   💰 Total planned: {total_shares} shares, ${total_value:,.2f}\n"
        report += f"   🎯 Target: ${target:.2f} | 📡 Dashboard"
        await channel.send(report)
    else:
        shares = result.get("shares", 0)
        order_id = result.get("order_id", "?")
        status = result.get("status", "?")
        value = result.get("position_value", 0)
        ep = entries[0]["price"]
        sl = entries[0]["stop_loss"]
        rps = abs(ep - sl) if sl > 0 else ep * 0.05

        await channel.send(
            f"\n✅ **{action} {shares} {cmd.ticker} @ ${ep:.2f} LMT**\n"
            f"   Order **{status}** (ID: {order_id})\n"
            f"   💰 ${value:,.2f} | 🎯 ${target:.2f} ({abs(target - ep) / ep * 100:.1f}%)\n"
            f"   🛑 ${sl:.2f} ({abs(sl - ep) / ep * 100:.1f}%) | "
            f"R:R {abs(target - ep) / max(rps, 0.01):.1f}:1\n"
            f"   📡 Visible in Trading Dashboard"
        )


def _log_trade(cmd, result, target, success, error=None):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": cmd.ticker,
        "action": "BUY" if cmd.direction == "long" else "SELL",
        "num_entries": cmd.num_entries,
        "dollar_amount": cmd.dollar_amount,
        "share_count": cmd.share_count,
        "target": target,
        "success": success,
    }
    if success:
        entry.update({k: result.get(k) for k in ("order_id", "shares", "total_shares", "orders", "status")})
    else:
        entry["error"] = error

    trades = []
    if os.path.exists(TRADE_LOG):
        try:
            with open(TRADE_LOG, "r") as f:
                trades = json.load(f)
        except Exception:
            pass
    trades.append(entry)
    os.makedirs(os.path.dirname(TRADE_LOG), exist_ok=True)
    with open(TRADE_LOG, "w") as f:
        json.dump(trades, f, indent=2, default=str)
