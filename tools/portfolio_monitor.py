#!/usr/bin/env python3
"""
Live Terminal Portfolio Monitor
================================
Connects to the GCloud Trading API and displays real-time
portfolio updates in a clean terminal UI with interactive commands.

Usage:
    python3 tools/portfolio_monitor.py
    python3 tools/portfolio_monitor.py --interval 3
    python3 tools/portfolio_monitor.py --url http://34.75.9.166:8080

Keys (in command mode, press Enter):
    buy TICKER [qty]   — Place buy order
    sell TICKER [qty]  — Place sell order
    flat TICKER        — Flatten (close) entire position
    q / quit           — Exit monitor
"""

import os
import sys
import time
import json
import signal
import select
import argparse
import threading
import requests
from datetime import datetime

# ────────────────────────────────────────────────────────────────────
# ANSI codes
# ────────────────────────────────────────────────────────────────────
RST   = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
RED   = "\033[31m"
GRN   = "\033[32m"
YEL   = "\033[33m"
BLU   = "\033[34m"
CYN   = "\033[36m"
WHT   = "\033[37m"
BRED  = "\033[91m"
BGRN  = "\033[92m"
CLR   = "\033[2J\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

# ────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────
API_URL = os.environ.get("TRADE_API_URL", "http://34.75.9.166:8080")
API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")
PIPELINE_FILE = os.path.join(os.path.dirname(__file__),
                             "../workspace/data/pipeline_candidates.json")

HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}

# Global state
command_output = ""  # Feedback from last command
command_input = ""   # Current input being typed


def api_get(endpoint, timeout=8):
    try:
        r = requests.get(f"{API_URL}{endpoint}", headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def api_post(endpoint, payload, timeout=10):
    try:
        r = requests.post(f"{API_URL}{endpoint}", headers=HEADERS,
                          json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def safe_float(val, default=0.0):
    """Safely convert to float, handling None."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0):
    """Safely convert to int, handling None."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ────────────────────────────────────────────────────────────────────
# Formatting helpers  (all handle None gracefully)
# ────────────────────────────────────────────────────────────────────

def fc(val):
    """Format currency — plain."""
    v = safe_float(val)
    return f"${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def fp(val):
    """Format P&L with color."""
    v = safe_float(val)
    if v > 0:   return f"{GRN}+${v:,.2f}{RST}"
    if v < 0:   return f"{RED}-${abs(v):,.2f}{RST}"
    return f"${v:,.2f}"


def fpct(val):
    """Format percent with color."""
    v = safe_float(val)
    if v > 0:   return f"{GRN}+{v:.1f}%{RST}"
    if v < 0:   return f"{RED}{v:.1f}%{RST}"
    return f"{v:.1f}%"


def arrow(cur, prev):
    v1, v2 = safe_float(cur), safe_float(prev)
    if v2 > 0:
        if v1 > v2: return f"{GRN}▲{RST}"
        if v1 < v2: return f"{RED}▼{RST}"
    return " "


def pad(s, width):
    """Pad string accounting for ANSI escape sequences."""
    import re
    visible = re.sub(r'\033\[[0-9;]*m', '', str(s))
    return str(s) + " " * max(0, width - len(visible))


def rpad(s, width):
    """Right-align string accounting for ANSI escapes."""
    import re
    visible = re.sub(r'\033\[[0-9;]*m', '', str(s))
    return " " * max(0, width - len(visible)) + str(s)


# ────────────────────────────────────────────────────────────────────
# Pipeline candidates (radar / watchlist)
# ────────────────────────────────────────────────────────────────────

def load_radar():
    """Load pipeline candidates from JSON file."""
    if not os.path.exists(PIPELINE_FILE):
        return []
    try:
        with open(PIPELINE_FILE, 'r') as f:
            data = json.load(f)
        return data[:8]  # Top 8
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────────
# Command execution
# ────────────────────────────────────────────────────────────────────

def _get_market_price(ticker, positions):
    """Get current market price from positions, IBKR quotes, or analyze API."""
    # Check existing positions first
    for p in (positions or []):
        if p.get("symbol", "").upper() == ticker.upper():
            price = safe_float(p.get("market_price"))
            if price > 0:
                return price

    # Try IBKR /api/quotes (real broker data, works 24/7)
    try:
        resp = requests.post(
            f"{API_BASE}/api/quotes",
            json={"symbols": [ticker.upper()]},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            price = safe_float(data.get(ticker.upper()))
            if price > 0:
                return price
    except Exception:
        pass

    # Fall back to scanner/analyze endpoint
    try:
        data = api_get(f"/api/analyze/{ticker}", timeout=10)
        if isinstance(data, dict):
            tech = data.get("technical", {})
            price = safe_float(tech.get("price"))
            if price > 0:
                return price
            scan = data.get("scan", {})
            price = safe_float(scan.get("price"))
            if price > 0:
                return price
    except Exception:
        pass
    return 0.0


def execute_command(cmd_str, positions):
    """Execute a trade command. Returns feedback string."""
    parts = cmd_str.strip().split()
    if not parts:
        return ""

    action = parts[0].lower()

    if action in ("q", "quit", "exit"):
        print(f"\n{SHOW_CURSOR}")
        sys.exit(0)

    if action == "help":
        return (f"{CYN}Commands:{RST}  buy TICKER [qty]  |  sell TICKER [qty]  "
                f"|  flat TICKER  |  q(uit)")

    if action in ("buy", "sell") and len(parts) >= 2:
        ticker = parts[1].upper()
        qty = safe_int(parts[2]) if len(parts) >= 3 else 0

        # Need a limit price — fetch current market price from watchlist/analyze
        mkt_price = _get_market_price(ticker, positions)
        if mkt_price <= 0:
            return f"{RED}Cannot get price for {ticker}. Try again.{RST}"

        # Adjust limit slightly: buy a bit above, sell a bit below (to get fills)
        if action == "buy":
            limit = round(mkt_price * 1.002, 2)  # +0.2%
        else:
            limit = round(mkt_price * 0.998, 2)  # -0.2%

        payload = {
            "ticker": ticker,
            "action": action.upper(),
            "limit_price": limit,
        }
        if qty > 0:
            payload["share_count"] = qty

        result = api_post("/api/trade", payload)
        if result.get("success") or result.get("orderId"):
            return f"{GRN}OK:{RST} {action.upper()} {ticker} {qty if qty else '(2%)'} — {result.get('message', 'sent')}"
        else:
            return f"{RED}ERR:{RST} {result.get('error', 'unknown')}"

    if action == "flat" and len(parts) >= 2:
        ticker = parts[1].upper()
        # Find position quantity
        pos_qty = 0
        for p in (positions or []):
            if p.get("symbol", "").upper() == ticker:
                pos_qty = safe_int(p.get("quantity"))
                break
        if pos_qty == 0:
            return f"{YEL}No open position for {ticker}{RST}"

        sell_action = "SELL" if pos_qty > 0 else "BUY"

        # Get market price for limit order
        mkt_price = _get_market_price(ticker, positions)
        if mkt_price <= 0:
            return f"{RED}Cannot get price for {ticker}. Try again.{RST}"

        # Aggressive limit to ensure fill (sell slightly below, buy slightly above)
        if sell_action == "SELL":
            limit = round(mkt_price * 0.998, 2)
        else:
            limit = round(mkt_price * 1.002, 2)

        payload = {
            "ticker": ticker,
            "action": sell_action,
            "limit_price": limit,
            "share_count": abs(pos_qty),
        }
        result = api_post("/api/trade", payload)
        if result.get("success") or result.get("orderId"):
            return f"{GRN}FLAT:{RST} {sell_action} {abs(pos_qty)} {ticker} — {result.get('message', 'sent')}"
        else:
            return f"{RED}ERR:{RST} {result.get('error', 'unknown')}"

    return f"{YEL}Unknown command. Type 'help' for options.{RST}"


# ────────────────────────────────────────────────────────────────────
# Render
# ────────────────────────────────────────────────────────────────────

W = 82  # Total width

def hr(char="─"):
    return char * W

def render(prev_prices, interval):
    """Render one frame. Returns updated prev_prices."""
    global command_output

    health    = api_get("/api/health")
    positions = api_get("/api/positions")
    account   = api_get("/api/account")
    orders    = api_get("/api/orders")
    radar     = load_radar()

    now = datetime.now().strftime("%H:%M:%S")
    connected = health.get("connected", False)
    mode = (health.get("trading_mode") or "?").upper()

    # ═══════════════════════════════════════════════════════════════
    # HEADER
    # ═══════════════════════════════════════════════════════════════
    conn_dot  = f"{GRN}●{RST}" if connected else f"{RED}●{RST}"
    conn_text = "CONNECTED" if connected else "DISCONNECTED"
    mode_clr  = YEL if mode == "PAPER" else RED if mode == "LIVE" else DIM

    print(f"{BOLD}{'═' * W}{RST}")
    print(f"{BOLD}  IBKR Portfolio Monitor{RST}                           "
          f"{DIM}{now}{RST}  {conn_dot} {conn_text}  {mode_clr}{mode}{RST}")
    print(f"{BOLD}{'═' * W}{RST}")

    if not connected:
        print(f"\n  {RED}IB Gateway disconnected. Retrying in {interval}s...{RST}\n")
        return prev_prices

    # ═══════════════════════════════════════════════════════════════
    # ACCOUNT SUMMARY
    # ═══════════════════════════════════════════════════════════════
    if isinstance(account, dict) and not account.get("error"):
        net_liq   = safe_float(account.get("net_liquidation"))
        cash      = safe_float(account.get("total_cash", account.get("available_funds")))
        bp        = safe_float(account.get("buying_power"))
        day_pnl   = safe_float(account.get("day_pnl", account.get("daily_pnl")))
        unr_pnl   = safe_float(account.get("unrealized_pnl"))
        real_pnl  = safe_float(account.get("realized_pnl"))
        start_eq  = safe_float(account.get("start_equity"))
        day_ret   = ((net_liq / start_eq) - 1) * 100 if start_eq > 0 else 0
        pos_count = safe_int(account.get("position_count"))

        print(f"\n  {BOLD}Account{RST}")
        print(f"  {DIM}{hr()}{RST}")
        print(f"  Net Liq     {BOLD}{fc(net_liq):>14}{RST}"
              f"    Cash       {fc(cash):>14}"
              f"    Buying Pwr  {fc(bp):>14}")
        print(f"  Day P&L     {rpad(fp(day_pnl), 14)}"
              f"    Unrealized {rpad(fp(unr_pnl), 14)}"
              f"    Day Return  {rpad(fpct(day_ret), 14)}")
        print(f"  {DIM}{hr()}{RST}")

    # ═══════════════════════════════════════════════════════════════
    # POSITIONS TABLE
    # ═══════════════════════════════════════════════════════════════
    if isinstance(positions, list) and positions:
        net_liq = safe_float((account or {}).get("net_liquidation", 1))
        total_pnl = 0.0
        total_val = 0.0

        print(f"\n  {BOLD}Positions ({len(positions)}){RST}")
        # Header
        print(f"  {CYN}{'Symbol':<8}{'Qty':>6}{'Avg Cost':>11}{'Mkt Price':>11}"
              f" {'Mkt Value':>12}{'P&L':>12}{'P&L %':>9}{'Port %':>8}{RST}")
        print(f"  {DIM}{hr()}{RST}")

        for pos in sorted(positions, key=lambda p: abs(safe_float(p.get("pnl"))), reverse=True):
            sym  = pos.get("symbol", "???")
            qty  = safe_int(pos.get("quantity"))
            avg  = safe_float(pos.get("avg_cost"))
            mkt  = safe_float(pos.get("market_price"))
            val  = safe_float(pos.get("market_value"))
            pnl  = safe_float(pos.get("pnl"))
            total_pnl += pnl
            total_val += abs(val)

            pnl_pct  = ((mkt / avg) - 1) * 100 if avg > 0 and mkt > 0 else 0
            port_pct = (abs(val) / net_liq * 100) if net_liq > 0 else 0

            ar = arrow(mkt, prev_prices.get(sym, 0))
            prev_prices[sym] = mkt

            qty_c = GRN if qty > 0 else RED
            print(f"  {BOLD}{sym:<8}{RST}"
                  f"{qty_c}{qty:>6}{RST}"
                  f"{fc(avg):>11}"
                  f"  {BOLD}{fc(mkt):>9}{RST}{ar}"
                  f"{fc(val):>12}"
                  f"  {rpad(fp(pnl), 10)}"
                  f"  {rpad(fpct(pnl_pct), 7)}"
                  f"{port_pct:>7.1f}%")

        print(f"  {DIM}{hr()}{RST}")
        print(f"  {BOLD}{'TOTAL':<8}{RST}"
              f"{'':>6}{'':>11}{'':>11}"
              f" {fc(total_val):>12}"
              f"  {rpad(fp(total_pnl), 10)}")
    else:
        print(f"\n  {DIM}No open positions{RST}")

    # ═══════════════════════════════════════════════════════════════
    # OPEN ORDERS
    # ═══════════════════════════════════════════════════════════════
    if isinstance(orders, list) and orders:
        print(f"\n  {BOLD}Open Orders ({len(orders)}){RST}")
        print(f"  {CYN}{'Symbol':<8}{'Action':>7}{'Qty':>6}{'Type':>7}{'Price':>10}{'Status':>10}{RST}")
        print(f"  {DIM}{hr()}{RST}")
        for o in orders[:8]:
            osym = o.get("symbol", "?")
            oact = o.get("action", "?")
            oqty = safe_int(o.get("quantity"))
            otyp = o.get("order_type", "?")
            oprc = safe_float(o.get("price"))
            ost  = o.get("status", "?")
            act_c = GRN if oact.upper() == "BUY" else RED
            print(f"  {BOLD}{osym:<8}{RST}"
                  f"{act_c}{oact:>7}{RST}"
                  f"{oqty:>6}"
                  f"{otyp:>7}"
                  f"{fc(oprc):>10}"
                  f"  {ost:<10}")

    # ═══════════════════════════════════════════════════════════════
    # RADAR — Pipeline candidates / opportunities on watch
    # ═══════════════════════════════════════════════════════════════
    held = set()
    if isinstance(positions, list):
        held = {p.get("symbol", "") for p in positions}

    if radar:
        watch = [r for r in radar if r.get("ticker", "") not in held
                 and safe_float(r.get("conviction_score")) >= 3.0][:5]
        if watch:
            print(f"\n  {BOLD}Radar — Potential Opportunities{RST}")
            print(f"  {CYN}{'Ticker':<8}{'Score':>6}{'Action':>8}{'Price':>10}"
                  f"{'Buy Zone':>16}{'Stop':>10}{'EV':>7}{'Mentions':>9}{RST}")
            print(f"  {DIM}{hr()}{RST}")
            for c in watch:
                tk    = c.get("ticker", "?")
                score = safe_float(c.get("conviction_score"))
                act   = c.get("action", "?")
                price = safe_float(c.get("current_price"))
                bl    = safe_float(c.get("scanner_buy_low"))
                bh    = safe_float(c.get("scanner_buy_high"))
                stop  = safe_float(c.get("scanner_stop"))
                ev    = safe_float(c.get("scanner_ev"))
                ment  = safe_int(c.get("discord_mentions"))

                act_c = GRN if act == "BUY" else YEL if act == "WAIT" else RED
                bz = f"${bl:.0f}-${bh:.0f}" if bl > 0 else "--"

                print(f"  {BOLD}{tk:<8}{RST}"
                      f"{score:>5.1f}"
                      f"  {act_c}{act:>6}{RST}"
                      f"{fc(price):>10}"
                      f"{bz:>16}"
                      f"{fc(stop) if stop > 0 else '--':>10}"
                      f"{ev:>6.1f}%"
                      f"{ment:>9}")

    # ═══════════════════════════════════════════════════════════════
    # COMMAND BAR
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  {DIM}{hr()}{RST}")
    if command_output:
        print(f"  {command_output}")
        command_output = ""
    print(f"  {DIM}Commands: buy TICKER [qty] | sell TICKER [qty] | flat TICKER | q(uit){RST}")
    print(f"  {DIM}> {RST}", end="", flush=True)

    return prev_prices


# ────────────────────────────────────────────────────────────────────
# Input thread
# ────────────────────────────────────────────────────────────────────

def input_thread(positions_ref):
    """Background thread that reads commands from stdin."""
    global command_output
    while True:
        try:
            line = input()
            if line.strip():
                command_output = execute_command(line, positions_ref.get("pos", []))
        except (EOFError, KeyboardInterrupt):
            break


# ────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live IBKR Portfolio Monitor")
    parser.add_argument("--interval", "-i", type=int, default=3,
                        help="Refresh interval in seconds (default: 3)")
    parser.add_argument("--url", "-u", type=str, default=None,
                        help="Trading API URL")
    args = parser.parse_args()

    global API_URL
    if args.url:
        API_URL = args.url

    prev_prices = {}
    positions_ref = {"pos": []}

    def handle_sigint(sig, frame):
        print(f"\n{SHOW_CURSOR}\n  {YEL}Shutting down...{RST}\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    # Start input thread for interactive commands
    t = threading.Thread(target=input_thread, args=(positions_ref,), daemon=True)
    t.start()

    print(f"{CLR}{HIDE_CURSOR}")

    while True:
        try:
            print(CLR, end="")
            prev_prices = render(prev_prices, args.interval)
            # Update positions ref for command thread
            pos = api_get("/api/positions")
            if isinstance(pos, list):
                positions_ref["pos"] = pos
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n  {RED}Error: {e}{RST}")
            time.sleep(5)

    print(f"{SHOW_CURSOR}\n{DIM}Monitor stopped.{RST}")


if __name__ == "__main__":
    main()
