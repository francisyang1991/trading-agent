#!/usr/bin/env python3
"""
Trading GUI - Production-grade web interface for IBKR trading.

Features:
- Connection health monitoring (state, heartbeat, API rate)
- Risk monitoring panel (Excess Liquidity, Daily P&L limits)
- Order confirmation with sanity checks
- Quick trade buttons (close position, flatten all)
- Extended hours trading toggle
- Futures contract support (ES, NQ, CL, GC)
- View account balance, positions, and orders

Usage:
    python -m tools.trading_gui
    
Then open http://localhost:8080 in your browser.
"""

import os
import time
import threading
import asyncio
import hashlib
import hmac
from datetime import datetime
from typing import Dict, Any, Optional
from flask import Flask, render_template_string, jsonify, request
from loguru import logger

# Set client ID
os.environ.setdefault("IB_CLIENT_ID", "90")

app = Flask(__name__)

# Global IB connection and state
_ib = None
_loop = None
_ib_lock = threading.Lock()
_connect_time = None
_message_count = 0
_last_rate_reset = time.time()
_daily_start_equity = None
_pnl_subscriptions = {}  # Track PnL subscriptions for positions
_watchlist = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]  # Default watchlist
_market_data = {}  # Cache for market data
_pending_entries = []  # Conditional entries waiting for prior fill + confirmation
_pending_lock = threading.Lock()


def init_ib():
    """Initialize IB connection in main thread before Flask starts."""
    global _ib, _loop, _connect_time
    
    try:
        from ib_async import IB
        
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        
        _ib = IB()
        
        host = os.getenv("IB_HOST", "127.0.0.1")
        port = int(os.getenv("IB_PORT", "4002"))
        client_id = int(os.getenv("IB_CLIENT_ID", "90"))
        
        _ib.connect(host, port, clientId=client_id)
        _connect_time = datetime.now()
        logger.info(f"Connected to IB Gateway at {host}:{port}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to connect to IB: {e}")
        _ib = None
        return False


def get_ib():
    """Get IB connection."""
    return _ib


def track_api_call():
    """Track API message for rate limiting display."""
    global _message_count, _last_rate_reset
    current_time = time.time()
    if current_time - _last_rate_reset > 1.0:
        _message_count = 0
        _last_rate_reset = current_time
    _message_count += 1


# HTML Template with all improvements
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
            background: #0d1117; 
            color: #c9d1d9; 
            padding: 20px;
            font-size: 14px;
        }
        .container { max-width: 1600px; margin: 0 auto; }
        
        /* Header */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #30363d;
        }
        h1 { color: #58a6ff; font-size: 20px; font-weight: 600; }
        h2 { color: #8b949e; font-size: 14px; font-weight: 600; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        
        /* Connection Health Bar */
        .health-bar {
            display: flex;
            gap: 15px;
            align-items: center;
            background: #161b22;
            padding: 10px 15px;
            border-radius: 6px;
            border: 1px solid #30363d;
            margin-bottom: 20px;
        }
        .health-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .health-label { color: #8b949e; font-size: 11px; }
        .health-value { font-weight: 600; }
        .health-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        .health-dot.green { background: #3fb950; box-shadow: 0 0 8px #3fb950; }
        .health-dot.yellow { background: #d29922; box-shadow: 0 0 8px #d29922; }
        .health-dot.red { background: #f85149; box-shadow: 0 0 8px #f85149; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        
        /* Grid Layout */
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 20px; }
        .card {
            background: #161b22;
            border-radius: 8px;
            padding: 16px;
            border: 1px solid #30363d;
        }
        .card.risk { border-color: #d29922; }
        .card.risk.danger { border-color: #f85149; background: #1c1410; }
        
        /* Account Summary */
        .account-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .account-item {
            background: #21262d;
            padding: 12px;
            border-radius: 6px;
            text-align: center;
        }
        .account-label { color: #8b949e; font-size: 11px; margin-bottom: 4px; }
        .account-value { font-size: 18px; font-weight: 700; }
        .account-value.positive { color: #3fb950; }
        .account-value.negative { color: #f85149; }
        
        /* Risk Panel */
        .risk-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }
        .risk-item {
            background: #21262d;
            padding: 10px;
            border-radius: 6px;
        }
        .risk-bar {
            height: 4px;
            background: #30363d;
            border-radius: 2px;
            margin-top: 8px;
            overflow: hidden;
        }
        .risk-bar-fill {
            height: 100%;
            border-radius: 2px;
            transition: width 0.3s ease;
        }
        .risk-bar-fill.safe { background: #3fb950; }
        .risk-bar-fill.warning { background: #d29922; }
        .risk-bar-fill.danger { background: #f85149; }
        
        /* Tables */
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px 8px; text-align: left; border-bottom: 1px solid #21262d; }
        th { color: #8b949e; font-weight: 500; font-size: 11px; text-transform: uppercase; }
        tr:hover { background: #21262d; }
        
        /* Buttons */
        .btn {
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-family: inherit;
            transition: all 0.15s ease;
        }
        .btn:hover { background: #30363d; border-color: #8b949e; }
        .btn.primary { background: #238636; border-color: #238636; color: #fff; }
        .btn.primary:hover { background: #2ea043; }
        .btn.danger { background: #da3633; border-color: #da3633; color: #fff; }
        .btn.danger:hover { background: #f85149; }
        .btn.warning { background: #9e6a03; border-color: #9e6a03; color: #fff; }
        .btn-sm { padding: 4px 8px; font-size: 11px; }
        .btn-group { display: flex; gap: 8px; margin-top: 10px; }
        
        /* Forms */
        input, select {
            background: #0d1117;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 12px;
            border-radius: 6px;
            width: 100%;
            font-family: inherit;
            font-size: 13px;
        }
        input:focus, select:focus { outline: none; border-color: #58a6ff; }
        label { display: block; color: #8b949e; font-size: 11px; margin-bottom: 4px; text-transform: uppercase; }
        .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 12px; margin-bottom: 12px; }
        
        /* Toggle Switch */
        .toggle-container { display: flex; align-items: center; gap: 8px; }
        .toggle {
            position: relative;
            width: 40px;
            height: 20px;
            background: #30363d;
            border-radius: 10px;
            cursor: pointer;
            transition: background 0.2s;
        }
        .toggle.active { background: #238636; }
        .toggle::after {
            content: '';
            position: absolute;
            width: 16px;
            height: 16px;
            background: #fff;
            border-radius: 50%;
            top: 2px;
            left: 2px;
            transition: transform 0.2s;
        }
        .toggle.active::after { transform: translateX(20px); }
        
        /* Modal */
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        .modal.show { display: flex; }
        .modal-content {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 24px;
            max-width: 400px;
            width: 90%;
        }
        .modal-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #c9d1d9; }
        .modal-body { margin-bottom: 20px; }
        .order-preview {
            background: #21262d;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 12px;
        }
        .order-preview-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
        .order-preview-label { color: #8b949e; }
        .order-preview-value { font-weight: 600; }
        .warning-text { color: #d29922; font-size: 12px; margin-top: 8px; }
        
        /* Toast Messages */
        #message {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 6px;
            display: none;
            z-index: 1001;
            font-weight: 500;
        }
        #message.success { background: #238636; color: #fff; }
        #message.error { background: #da3633; color: #fff; }
        
        /* Asset Type Tabs */
        .asset-tabs {
            display: flex;
            gap: 4px;
            margin-bottom: 12px;
            background: #21262d;
            padding: 4px;
            border-radius: 6px;
        }
        .asset-tab {
            flex: 1;
            padding: 6px 12px;
            text-align: center;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: all 0.15s;
        }
        .asset-tab:hover { background: #30363d; }
        .asset-tab.active { background: #0d1117; color: #58a6ff; }
        
        /* Position Actions */
        .position-actions { display: flex; gap: 4px; white-space: nowrap; }
        
        /* Table column widths */
        table { width: 100%; border-collapse: collapse; table-layout: auto; }
        table th:last-child, table td:last-child { 
            text-align: right; 
            min-width: 100px;
        }
        
        /* Utilities */
        .text-green { color: #3fb950; }
        .text-red { color: #f85149; }
        .text-yellow { color: #d29922; }
        .text-muted { color: #8b949e; }
        .refresh-btn { margin-left: auto; }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <h1>Trading Dashboard</h1>
            <button class="btn" onclick="refreshAll()">Refresh</button>
        </div>
        
        <!-- Connection Health Bar -->
        <div class="health-bar">
            <div class="health-item">
                <div class="health-dot" id="conn-dot"></div>
                <span class="health-label">Status</span>
                <span class="health-value" id="conn-state">DISCONNECTED</span>
            </div>
            <div class="health-item">
                <span class="health-label">Mode</span>
                <span class="health-value" id="trading-mode">PAPER</span>
            </div>
            <div class="health-item">
                <span class="health-label">Heartbeat</span>
                <span class="health-value" id="heartbeat-time">--</span>
            </div>
            <div class="health-item">
                <span class="health-label">API Rate</span>
                <span class="health-value" id="api-rate">0/50</span>
            </div>
            <div class="health-item">
                <span class="health-label">Uptime</span>
                <span class="health-value" id="uptime">--</span>
            </div>
        </div>
        
        <!-- Toast Message -->
        <div id="message"></div>
        
        <!-- Confirmation Modal -->
        <div class="modal" id="confirm-modal">
            <div class="modal-content">
                <div class="modal-title">Confirm Order</div>
                <div class="modal-body">
                    <div class="order-preview" id="order-preview"></div>
                    <div id="order-warnings"></div>
                </div>
                <div class="btn-group" style="justify-content: flex-end;">
                    <button class="btn" onclick="closeModal()">Cancel</button>
                    <button class="btn primary" id="confirm-order-btn">Place Order</button>
                </div>
            </div>
        </div>
        
        <!-- Account Summary -->
        <div class="account-grid">
            <div class="account-item">
                <div class="account-label">Net Liquidation</div>
                <div class="account-value" id="net-liq">$0.00</div>
            </div>
            <div class="account-item">
                <div class="account-label">Cash</div>
                <div class="account-value" id="cash">$0.00</div>
            </div>
            <div class="account-item">
                <div class="account-label">Buying Power</div>
                <div class="account-value" id="buying-power">$0.00</div>
            </div>
            <div class="account-item">
                <div class="account-label">Unrealized P&L</div>
                <div class="account-value" id="unrealized-pnl">$0.00</div>
            </div>
            <div class="account-item">
                <div class="account-label">Day P&L</div>
                <div class="account-value" id="day-pnl">$0.00</div>
            </div>
            <div class="account-item">
                <div class="account-label">Day P&L %</div>
                <div class="account-value" id="day-pnl-pct">0.00%</div>
            </div>
        </div>
        
        <div class="grid">
            <!-- Risk Monitoring Panel -->
            <div class="card risk" id="risk-card">
                <h2>Risk Monitor</h2>
                <div class="risk-grid">
                    <div class="risk-item">
                        <div class="account-label">Excess Liquidity</div>
                        <div class="account-value" id="excess-liq">$0.00</div>
                        <div class="risk-bar">
                            <div class="risk-bar-fill safe" id="excess-liq-bar" style="width: 100%"></div>
                        </div>
                    </div>
                    <div class="risk-item">
                        <div class="account-label">Margin Used</div>
                        <div class="account-value" id="margin-used">0%</div>
                        <div class="risk-bar">
                            <div class="risk-bar-fill safe" id="margin-bar" style="width: 0%"></div>
                        </div>
                    </div>
                    <div class="risk-item">
                        <div class="account-label">Daily Loss Limit (-2%)</div>
                        <div class="account-value" id="daily-loss">$0.00</div>
                        <div class="risk-bar">
                            <div class="risk-bar-fill safe" id="daily-loss-bar" style="width: 0%"></div>
                        </div>
                    </div>
                    <div class="risk-item">
                        <div class="account-label">Position Count</div>
                        <div class="account-value" id="position-count">0</div>
                    </div>
                </div>
                </div>
            </div>
            
            <!-- Order Info (orders are placed via Discord bot) -->
            <div class="card">
                <h2>Order Execution</h2>
                <p class="text-muted" style="margin: 8px 0; font-size: 13px;">
                    Orders are placed via the <strong>Discord bot</strong>.<br>
                    Use commands like: <code>@bot buy HOOD 5000usd</code>
                </p>
                <div class="btn-group" style="gap: 8px;">
                    <button class="btn warning btn-sm" onclick="cancelAllOrders()">Cancel All Orders</button>
                    <button class="btn danger btn-sm" onclick="flattenAll()">Flatten All</button>
                </div>
            </div>
            
            <!-- Positions -->
            <div class="card">
                <h2>Positions</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Avg Cost</th>
                            <th>Mkt Price</th>
                            <th>Market Value</th>
                            <th>P&amp;L</th>
                            <th>% Port</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="positions-table">
                        <tr><td colspan="8" class="text-muted" style="text-align:center;">No positions</td></tr>
                    </tbody>
                </table>
            </div>
            
            <!-- Open Orders -->
            <div class="card">
                <h2>Open Orders</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Action</th>
                            <th>Qty</th>
                            <th>Type</th>
                            <th>Price</th>
                            <th>Status</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody id="orders-table">
                        <tr><td colspan="7" class="text-muted" style="text-align:center;">No open orders</td></tr>
                    </tbody>
                </table>
            </div>
            
            <!-- Market Watchlist -->
            <div class="card">
                <h2>Market Watch
                    <button class="btn btn-sm refresh-btn" onclick="fetchWatchlist()" style="float:right;">Refresh</button>
                </h2>
                <div style="margin-bottom: 12px;">
                    <input type="text" id="watchlist-input" placeholder="Add symbol (e.g. AAPL)" 
                           style="width: calc(100% - 80px); display: inline-block;"
                           onkeypress="if(event.key==='Enter'){addToWatchlist();}">
                    <button class="btn btn-sm primary" onclick="addToWatchlist()" style="width: 70px;">Add</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Last</th>
                            <th>Bid</th>
                            <th>Ask</th>
                            <th>Change</th>
                            <th>%</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody id="watchlist-table">
                        <tr><td colspan="7" class="text-muted" style="text-align:center;">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <script>
        // State
        let extendedHours = false;
        let pendingOrder = null;
        let accountData = {};
        let positionsData = [];
        
        // Utilities
        function showMessage(text, type) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = type;
            msg.style.display = 'block';
            setTimeout(() => { msg.style.display = 'none'; }, 3000);
        }
        
        function formatCurrency(value) {
            if (value === null || value === undefined || isNaN(value)) return '$0.00';
            return '$' + parseFloat(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
        
        function formatPct(value) {
            if (value === null || value === undefined || isNaN(value)) return '0.00%';
            const pct = parseFloat(value).toFixed(2);
            return (value >= 0 ? '+' : '') + pct + '%';
        }
        
        function formatPnl(value) {
            if (value === null || value === undefined || isNaN(value)) return '<span class="text-muted">$0.00</span>';
            const cls = value >= 0 ? 'text-green' : 'text-red';
            return `<span class="${cls}">${value >= 0 ? '+' : ''}${formatCurrency(value)}</span>`;
        }
        
        // Asset Type Switching
        // Order placement removed — orders go through Discord bot.
        // Stub functions to avoid JS errors from leftover references.
        function setAssetType() {}
        function updateFuturesExpiries() {}
        function toggleExtendedHours() {}
        function toggleLimitPrice() {}
        
        // Order form removed — stubs for leftover references
        function showOrderConfirmation(event) { if (event) event.preventDefault(); }
        function closeModal() {
            const modal = document.getElementById('confirm-modal');
            if (modal) modal.classList.remove('show');
            pendingOrder = null;
        }
        
        // API Calls
        async function fetchConnectionHealth() {
            try {
                const response = await fetch('/api/health');
                const data = await response.json();
                
                const dot = document.getElementById('conn-dot');
                const state = document.getElementById('conn-state');
                
                if (data.connected) {
                    dot.className = 'health-dot green';
                    state.textContent = data.state.toUpperCase();
                } else {
                    dot.className = 'health-dot red';
                    state.textContent = 'DISCONNECTED';
                }
                
                document.getElementById('trading-mode').textContent = (data.trading_mode || 'paper').toUpperCase();
                document.getElementById('heartbeat-time').textContent = data.last_heartbeat || '--';
                document.getElementById('api-rate').textContent = (data.api_rate || 0) + '/50';
                document.getElementById('uptime').textContent = data.uptime || '--';
                
            } catch (e) {
                document.getElementById('conn-dot').className = 'health-dot red';
                document.getElementById('conn-state').textContent = 'ERROR';
            }
        }
        
        async function fetchAccount() {
            try {
                const response = await fetch('/api/account');
                accountData = await response.json();
                
                if (accountData.connected) {
                    document.getElementById('net-liq').textContent = formatCurrency(accountData.net_liquidation);
                    document.getElementById('cash').textContent = formatCurrency(accountData.total_cash);
                    document.getElementById('buying-power').textContent = formatCurrency(accountData.buying_power);
                    
                    // Unrealized P&L from open positions
                    const unrealizedPnl = accountData.unrealized_pnl || 0;
                    document.getElementById('unrealized-pnl').textContent = formatCurrency(unrealizedPnl);
                    document.getElementById('unrealized-pnl').className = 'account-value ' + (unrealizedPnl >= 0 ? 'positive' : 'negative');
                    
                    // Day P&L = realized + unrealized (total daily change)
                    const dayPnl = accountData.day_pnl || (accountData.realized_pnl || 0) + (accountData.unrealized_pnl || 0);
                    document.getElementById('day-pnl').textContent = formatCurrency(dayPnl);
                    document.getElementById('day-pnl').className = 'account-value ' + (dayPnl >= 0 ? 'positive' : 'negative');
                    
                    // Calculate day P&L % based on start equity
                    const startEquity = accountData.start_equity || accountData.net_liquidation || 1;
                    const dayPnlPct = (dayPnl / startEquity) * 100;
                    document.getElementById('day-pnl-pct').textContent = formatPct(dayPnlPct);
                    document.getElementById('day-pnl-pct').className = 'account-value ' + (dayPnlPct >= 0 ? 'positive' : 'negative');
                    
                    // Update risk panel
                    updateRiskPanel(accountData);
                }
            } catch (e) {
                console.error('Failed to fetch account:', e);
            }
        }
        
        function updateRiskPanel(data) {
            const netLiq = data.net_liquidation || 1;
            const excessLiq = data.excess_liquidity || 0;
            const buyingPower = data.buying_power || 0;
            // Use day_pnl which includes both realized and unrealized
            const dayPnl = data.day_pnl || ((data.realized_pnl || 0) + (data.unrealized_pnl || 0));
            
            // Excess Liquidity
            document.getElementById('excess-liq').textContent = formatCurrency(excessLiq);
            const excessPct = (excessLiq / netLiq) * 100;
            const excessBar = document.getElementById('excess-liq-bar');
            excessBar.style.width = Math.min(excessPct, 100) + '%';
            excessBar.className = 'risk-bar-fill ' + (excessPct > 20 ? 'safe' : excessPct > 5 ? 'warning' : 'danger');
            
            // Margin Used
            const marginUsed = Math.max(0, 100 - (buyingPower / netLiq / 4) * 100);
            document.getElementById('margin-used').textContent = marginUsed.toFixed(1) + '%';
            const marginBar = document.getElementById('margin-bar');
            marginBar.style.width = marginUsed + '%';
            marginBar.className = 'risk-bar-fill ' + (marginUsed < 50 ? 'safe' : marginUsed < 80 ? 'warning' : 'danger');
            
            // Daily Loss
            const dailyLossLimit = netLiq * 0.02;
            const dailyLossUsed = Math.abs(Math.min(0, dayPnl));
            document.getElementById('daily-loss').textContent = formatCurrency(dailyLossUsed) + ' / ' + formatCurrency(dailyLossLimit);
            const lossBar = document.getElementById('daily-loss-bar');
            const lossPct = (dailyLossUsed / dailyLossLimit) * 100;
            lossBar.style.width = Math.min(lossPct, 100) + '%';
            lossBar.className = 'risk-bar-fill ' + (lossPct < 50 ? 'safe' : lossPct < 80 ? 'warning' : 'danger');
            
            // Position count
            document.getElementById('position-count').textContent = data.position_count || 0;
            
            // Update card styling based on risk level
            const riskCard = document.getElementById('risk-card');
            if (excessPct < 5 || lossPct > 80) {
                riskCard.classList.add('danger');
            } else {
                riskCard.classList.remove('danger');
            }
        }
        
        async function fetchPositions() {
            try {
                const response = await fetch('/api/positions');
                positionsData = await response.json();
                
                const tbody = document.getElementById('positions-table');
                if (positionsData.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" class="text-muted" style="text-align:center;">No positions</td></tr>';
                    return;
                }
                
                const netLiq = accountData.net_liquidation || 1;
                
                tbody.innerHTML = positionsData.map(pos => {
                    const pctPort = ((pos.market_value || 0) / netLiq * 100).toFixed(1);
                    const pctClass = pctPort > 10 ? 'text-yellow' : '';
                    const secType = pos.sec_type || 'STK';
                    const mktPrice = pos.market_price || 0;
                    
                    return `
                        <tr>
                            <td><strong>${pos.symbol}</strong><span class="text-muted" style="font-size:10px;margin-left:4px;">${secType}</span></td>
                            <td class="${pos.quantity >= 0 ? 'text-green' : 'text-red'}">${pos.quantity}</td>
                            <td>${formatCurrency(pos.avg_cost)}</td>
                            <td>${formatCurrency(mktPrice)}</td>
                            <td>${formatCurrency(pos.market_value)}</td>
                            <td>${formatPnl(pos.pnl)}</td>
                            <td class="${pctClass}">${pctPort}%</td>
                            <td class="position-actions">
                                <button class="btn btn-sm" onclick="closePosition('${pos.symbol}', ${pos.quantity}, 0.5, '${secType}')">50%</button>
                                <button class="btn btn-sm danger" onclick="closePosition('${pos.symbol}', ${pos.quantity}, 1, '${secType}')">Close</button>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (e) {
                console.error('Failed to fetch positions:', e);
            }
        }
        
        async function fetchOrders() {
            try {
                const response = await fetch('/api/orders');
                const data = await response.json();
                
                const tbody = document.getElementById('orders-table');
                if (data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;">No open orders</td></tr>';
                    return;
                }
                
                tbody.innerHTML = data.map(order => `
                    <tr>
                        <td><strong>${order.symbol}</strong></td>
                        <td class="${order.action === 'BUY' ? 'text-green' : 'text-red'}">${order.action}</td>
                        <td>${order.quantity}</td>
                        <td>${order.order_type}</td>
                        <td>${order.price ? formatCurrency(order.price) : 'MKT'}</td>
                        <td>${order.status}</td>
                        <td><button class="btn btn-sm danger" onclick="cancelOrder(${order.order_id})">Cancel</button></td>
                    </tr>
                `).join('');
            } catch (e) {
                console.error('Failed to fetch orders:', e);
            }
        }
        
        async function placeOrder() {
            // Order placement via UI removed — use Discord bot.
            showMessage('Orders are placed via Discord bot', 'error');
        }
        
        async function cancelOrder(orderId) {
            try {
                const response = await fetch(`/api/order/${orderId}`, { method: 'DELETE' });
                const data = await response.json();
                
                if (data.success) {
                    showMessage('Order cancelled', 'success');
                    fetchOrders();
                } else {
                    showMessage('Failed to cancel order', 'error');
                }
            } catch (e) {
                showMessage('Failed to cancel order', 'error');
            }
        }
        
        async function closePosition(symbol, quantity, fraction, secType) {
            const qty = Math.abs(Math.floor(quantity * fraction));
            if (qty === 0) return;
            
            const action = quantity > 0 ? 'SELL' : 'BUY';
            
            // Determine asset type - futures have secType 'FUT'
            const assetType = (secType === 'FUT') ? 'futures' : 'stock';
            
            try {
                const response = await fetch('/api/order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        asset_type: assetType,
                        symbol: symbol,
                        quantity: qty,
                        action: action,
                        order_type: 'MKT'
                    })
                });
                
                const data = await response.json();
                if (data.success) {
                    showMessage(`Closing ${qty} ${symbol}`, 'success');
                    // Auto-refresh after delay to allow order to fill
                    setTimeout(() => {
                        fetchPositions();
                        fetchAccount();
                    }, 1500);
                    fetchOrders();
                } else {
                    showMessage(`Failed: ${data.error}`, 'error');
                }
            } catch (e) {
                showMessage('Failed to close position', 'error');
            }
        }
        
        async function flattenAll() {
            if (!confirm('Flatten ALL positions? This will close all open positions at market.')) return;
            
            try {
                const response = await fetch('/api/flatten', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showMessage(`Flattening ${data.count} positions`, 'success');
                    setTimeout(refreshAll, 2000);
                } else {
                    showMessage(`Failed: ${data.error}`, 'error');
                }
            } catch (e) {
                showMessage('Failed to flatten positions', 'error');
            }
        }
        
        async function cancelAllOrders() {
            if (!confirm('Cancel ALL open orders?')) return;
            
            try {
                const response = await fetch('/api/cancel-all', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    showMessage(`Cancelled ${data.count} orders`, 'success');
                    fetchOrders();
                } else {
                    showMessage(`Failed: ${data.error}`, 'error');
                }
            } catch (e) {
                showMessage('Failed to cancel orders', 'error');
            }
        }
        
        function refreshAll() {
            fetchConnectionHealth();
            fetchAccount();
            fetchPositions();
            fetchOrders();
        }
        
        // Watchlist functions
        let watchlistSymbols = [];
        
        async function fetchWatchlist() {
            try {
                const response = await fetch('/api/watchlist');
                const data = await response.json();
                
                watchlistSymbols = data.symbols || [];
                const tbody = document.getElementById('watchlist-table');
                
                if (!data.data || data.data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;">No symbols in watchlist</td></tr>';
                    return;
                }
                
                tbody.innerHTML = data.data.map(item => {
                    const changeClass = item.change >= 0 ? 'text-green' : 'text-red';
                    const changePrefix = item.change >= 0 ? '+' : '';
                    
                    return `
                        <tr>
                            <td><strong>${item.symbol}</strong></td>
                            <td>${item.last > 0 ? formatCurrency(item.last) : '--'}</td>
                            <td class="text-muted">${item.bid > 0 ? formatCurrency(item.bid) : '--'}</td>
                            <td class="text-muted">${item.ask > 0 ? formatCurrency(item.ask) : '--'}</td>
                            <td class="${changeClass}">${item.last > 0 ? changePrefix + formatCurrency(item.change) : '--'}</td>
                            <td class="${changeClass}">${item.last > 0 ? changePrefix + item.change_pct.toFixed(2) + '%' : '--'}</td>
                            <td>
                                <button class="btn btn-sm text-red" onclick="removeFromWatchlist('${item.symbol}')" style="padding:4px 6px;" title="Remove">✕</button>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (e) {
                console.error('Failed to fetch watchlist:', e);
            }
        }
        
        async function addToWatchlist() {
            const input = document.getElementById('watchlist-input');
            const symbol = input.value.toUpperCase().trim();
            
            if (!symbol) return;
            if (watchlistSymbols.includes(symbol)) {
                showMessage(`${symbol} already in watchlist`, 'error');
                return;
            }
            
            watchlistSymbols.push(symbol);
            input.value = '';
            
            try {
                await fetch('/api/watchlist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbols: watchlistSymbols })
                });
                showMessage(`Added ${symbol} to watchlist`, 'success');
                fetchWatchlist();
            } catch (e) {
                showMessage('Failed to update watchlist', 'error');
            }
        }
        
        async function removeFromWatchlist(symbol) {
            watchlistSymbols = watchlistSymbols.filter(s => s !== symbol);
            
            try {
                await fetch('/api/watchlist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbols: watchlistSymbols })
                });
                fetchWatchlist();
            } catch (e) {
                showMessage('Failed to update watchlist', 'error');
            }
        }
        
        function quickBuy(symbol) {
            showMessage(`Use Discord: @bot buy ${symbol}`, 'success');
        }
        
        // Setup
        const confirmBtn = document.getElementById('confirm-order-btn');
        if (confirmBtn) confirmBtn.addEventListener('click', placeOrder);
        
        // Initial load and auto-refresh
        refreshAll();
        fetchWatchlist();
        setInterval(refreshAll, 5000);
        setInterval(fetchWatchlist, 30000);  // Refresh watchlist every 30 seconds
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    """Serve the main dashboard."""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/health')
def api_health():
    """Get connection health status."""
    global _message_count, _connect_time
    
    ib = get_ib()
    connected = ib is not None and ib.isConnected()
    
    # Calculate uptime
    uptime = "--"
    if _connect_time and connected:
        delta = datetime.now() - _connect_time
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        uptime = f"{hours}h {minutes}m"
    
    # Determine trading mode from port
    port = int(os.getenv("IB_PORT", "4002"))
    trading_mode = "paper" if port == 4002 else "live"
    
    return jsonify({
        "connected": connected,
        "state": "connected" if connected else "disconnected",
        "trading_mode": trading_mode,
        "last_heartbeat": datetime.now().strftime("%H:%M:%S"),
        "api_rate": _message_count,
        "uptime": uptime
    })


@app.route('/api/account')
def api_account():
    """Get account summary with risk metrics and proper P&L calculation."""
    global _message_count, _daily_start_equity
    
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"connected": False})
    
    try:
        with _ib_lock:
            track_api_call()
            
            # Cancel any previous subscription first
            try:
                ib.cancelAccountSummary()
            except Exception:
                pass
            
            ib.reqAccountSummary()
            ib.sleep(0.5)
            
            summary = {}
            for item in ib.accountSummary():
                summary[item.tag] = item.value
            
            # Cancel to avoid rate limit issues
            try:
                ib.cancelAccountSummary()
            except Exception:
                pass
            
            # Get position count and calculate total unrealized P&L from portfolio
            ib.reqPositions()
            ib.sleep(0.3)
            position_count = 0
            total_unrealized_pnl = 0
            total_daily_pnl = 0
            
            # Use portfolio() for accurate P&L data
            for pv in ib.portfolio():
                if pv.position != 0:
                    position_count += 1
                    total_unrealized_pnl += pv.unrealizedPNL or 0
            
            # Get realized P&L from account summary
            realized_pnl = float(summary.get("RealizedPnL", 0) or 0)
            
            # Day P&L = Realized P&L (closed trades) + Unrealized P&L change today
            # For a more accurate day P&L, use both values
            # Note: IBKR's RealizedPnL resets daily, UnrealizedPnL is current
            day_pnl = realized_pnl + total_unrealized_pnl
            
            # Store start equity if not set (for percentage calculation)
            net_liq = float(summary.get("NetLiquidation", 0) or 0)
            if _daily_start_equity is None:
                _daily_start_equity = net_liq - day_pnl
        
        return jsonify({
            "connected": True,
            "net_liquidation": net_liq,
            "total_cash": float(summary.get("TotalCashValue", 0) or 0),
            "buying_power": float(summary.get("BuyingPower", 0) or 0),
            "unrealized_pnl": total_unrealized_pnl,
            "realized_pnl": realized_pnl,
            "day_pnl": day_pnl,
            "excess_liquidity": float(summary.get("ExcessLiquidity", 0) or 0),
            "position_count": position_count,
            "start_equity": _daily_start_equity
        })
    except Exception as e:
        logger.error(f"Failed to get account: {e}")
        return jsonify({"connected": False, "error": str(e)})


@app.route('/api/positions')
def api_positions():
    """Get positions with accurate P&L from portfolio().

    portfolio() returns marketPrice, marketValue, and unrealizedPNL
    directly — much more reliable than reqPnLSingle.
    """
    ib = get_ib()

    if not ib or not ib.isConnected():
        return jsonify([])

    try:
        with _ib_lock:
            track_api_call()
            positions = []
            for pv in ib.portfolio():
                if pv.position == 0:
                    continue

                market_price = pv.marketPrice or 0
                market_value = pv.marketValue or abs(pv.position * market_price)
                unrealized_pnl = pv.unrealizedPNL or 0

                # avgCost from portfolio is total cost / qty
                avg_cost = pv.averageCost or 0

                positions.append({
                    "symbol": pv.contract.symbol,
                    "quantity": int(pv.position),
                    "avg_cost": round(avg_cost, 2),
                    "market_price": round(market_price, 2),
                    "market_value": round(abs(market_value), 2),
                    "pnl": round(unrealized_pnl, 2),
                    "sec_type": pv.contract.secType or "STK",
                    "con_id": pv.contract.conId,
                })

        return jsonify(positions)
    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        return jsonify([])


@app.route('/api/orders')
def api_orders():
    """Get open orders."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify([])
    
    try:
        with _ib_lock:
            track_api_call()
            ib.reqOpenOrders()
            ib.sleep(0.3)
            
            orders = []
            for trade in ib.openTrades():
                order = trade.order
                orders.append({
                    "order_id": order.orderId,
                    "symbol": trade.contract.symbol,
                    "action": order.action,
                    "quantity": int(order.totalQuantity),
                    "order_type": order.orderType,
                    "price": order.lmtPrice if order.orderType == "LMT" else order.auxPrice if order.orderType in ["STP", "STP_LMT"] else None,
                    "status": trade.orderStatus.status
                })
        
        return jsonify(orders)
    except Exception as e:
        logger.error(f"Failed to get orders: {e}")
        return jsonify([])


@app.route('/api/order', methods=['POST'])
def api_place_order():
    """Place a new order with sanity checks."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"success": False, "error": "Not connected to IB Gateway"})
    
    try:
        data = request.json
        asset_type = data.get('asset_type', 'stock')
        symbol = data['symbol'].upper()
        quantity = int(data['quantity'])
        action = data['action']
        order_type = data['order_type']
        limit_price = data.get('limit_price')
        stop_price = data.get('stop_price')
        extended_hours = data.get('extended_hours', False)
        
        from ib_async import Stock, Future, MarketOrder, LimitOrder, StopOrder, Order
        
        # Create contract based on asset type
        if asset_type == 'futures':
            # Parse symbol like "ESH6" -> ES with localSymbol ESH6
            # Extract base symbol (first 2 chars for standard, or up to first digit)
            import re
            match = re.match(r'^([A-Z]+)([A-Z]\d)$', symbol)
            if match:
                base_symbol = match.group(1)
                local_symbol = symbol
            else:
                base_symbol = symbol[:2]
                local_symbol = symbol
            
            # Map to correct exchange
            exchange_map = {
                'ES': 'CME',
                'NQ': 'CME',
                'YM': 'CME',
                'MES': 'CME',  # Micro ES
                'MNQ': 'CME',  # Micro NQ
                'CL': 'NYMEX',
                'GC': 'COMEX',
                'SI': 'COMEX',
            }
            exchange = exchange_map.get(base_symbol, 'CME')
            contract = Future(localSymbol=local_symbol, exchange=exchange)
            logger.info(f"Created futures contract: {local_symbol} on {exchange}")
        else:
            contract = Stock(symbol, 'SMART', 'USD')
            logger.info(f"Created stock contract: {symbol}")
        
        with _ib_lock:
            track_api_call()
            ib.qualifyContracts(contract)
            
            # Create order
            if order_type == 'MKT':
                order = MarketOrder(action, quantity)
            elif order_type == 'LMT':
                if not limit_price:
                    return jsonify({"success": False, "error": "Limit price required"})
                order = LimitOrder(action, quantity, limit_price)
            elif order_type == 'STP':
                if not limit_price:
                    return jsonify({"success": False, "error": "Stop price required"})
                order = StopOrder(action, quantity, limit_price)
            elif order_type == 'STP_LMT':
                if not limit_price or not stop_price:
                    return jsonify({"success": False, "error": "Both stop and limit prices required"})
                order = Order()
                order.action = action
                order.orderType = 'STP LMT'
                order.totalQuantity = quantity
                order.lmtPrice = limit_price
                order.auxPrice = stop_price
            else:
                return jsonify({"success": False, "error": "Invalid order type"})
            
            # Extended hours
            if extended_hours:
                order.outsideRth = True
            
            trade = ib.placeOrder(contract, order)
            ib.sleep(0.5)
        
        return jsonify({
            "success": True,
            "order_id": trade.order.orderId,
            "status": trade.orderStatus.status
        })
        
    except Exception as e:
        logger.error(f"Failed to place order: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/order/<int:order_id>', methods=['DELETE'])
def api_cancel_order(order_id):
    """Cancel an order."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"success": False, "error": "Not connected"})
    
    try:
        with _ib_lock:
            track_api_call()
            for trade in ib.openTrades():
                if trade.order.orderId == order_id:
                    ib.cancelOrder(trade.order)
                    ib.sleep(0.3)
                    return jsonify({"success": True})
        
        return jsonify({"success": False, "error": "Order not found"})
    except Exception as e:
        logger.error(f"Failed to cancel order: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/flatten', methods=['POST'])
def api_flatten_all():
    """Flatten all positions."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"success": False, "error": "Not connected"})
    
    try:
        from ib_async import Stock, Future, MarketOrder
        
        with _ib_lock:
            track_api_call()
            ib.reqPositions()
            ib.sleep(0.3)
            
            count = 0
            for pos in ib.positions():
                if pos.position != 0:
                    action = "SELL" if pos.position > 0 else "BUY"
                    quantity = abs(int(pos.position))
                    
                    contract = pos.contract
                    ib.qualifyContracts(contract)
                    
                    order = MarketOrder(action, quantity)
                    ib.placeOrder(contract, order)
                    count += 1
            
            ib.sleep(0.5)
        
        return jsonify({"success": True, "count": count})
    except Exception as e:
        logger.error(f"Failed to flatten: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/cancel-all', methods=['POST'])
def api_cancel_all():
    """Cancel all open orders."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"success": False, "error": "Not connected"})
    
    try:
        with _ib_lock:
            track_api_call()
            ib.reqOpenOrders()
            ib.sleep(0.3)
            
            count = 0
            for trade in ib.openTrades():
                ib.cancelOrder(trade.order)
                count += 1
            
            ib.sleep(0.3)
        
        return jsonify({"success": True, "count": count})
    except Exception as e:
        logger.error(f"Failed to cancel all: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/watchlist')
def api_watchlist():
    """Get market data for watchlist symbols.

    Uses reqMktData with delayed data (type 3) which works without
    paid market data subscriptions.  Falls back to portfolio() for
    any symbol that is currently held.
    """
    global _watchlist, _market_data

    ib = get_ib()

    if not ib or not ib.isConnected():
        return jsonify({"symbols": _watchlist, "data": []})

    try:
        from ib_async import Stock

        # Build a lookup of current portfolio prices (always accurate)
        portfolio_prices = {}
        with _ib_lock:
            track_api_call()
            for pv in ib.portfolio():
                portfolio_prices[pv.contract.symbol] = {
                    "last": pv.marketPrice or 0,
                    "value": pv.marketValue or 0,
                    "pnl": pv.unrealizedPNL or 0,
                }

        result = []
        with _ib_lock:
            # Switch to delayed-frozen data so snapshots work on paper
            try:
                ib.reqMarketDataType(3)  # 3 = delayed-frozen
            except Exception:
                pass

            for symbol in _watchlist:
                # If we already hold this symbol, use portfolio data
                if symbol in portfolio_prices:
                    pp = portfolio_prices[symbol]
                    result.append({
                        "symbol": symbol,
                        "last": pp["last"],
                        "bid": 0,
                        "ask": 0,
                        "change": pp["pnl"],
                        "change_pct": 0,
                        "volume": 0,
                        "source": "portfolio",
                    })
                    continue

                contract = Stock(symbol, 'SMART', 'USD')
                try:
                    track_api_call()
                    ib.qualifyContracts(contract)

                    ticker = ib.reqMktData(contract, '', True, False)
                    ib.sleep(0.5)  # give delayed data a moment

                    last = ticker.last if (ticker.last and ticker.last > 0) else \
                           ticker.close if (ticker.close and ticker.close > 0) else \
                           ticker.marketPrice() if hasattr(ticker, 'marketPrice') else 0
                    bid = ticker.bid if (ticker.bid and ticker.bid > 0) else 0
                    ask = ticker.ask if (ticker.ask and ticker.ask > 0) else 0
                    close = ticker.close if (ticker.close and ticker.close > 0) else 0
                    change = (last - close) if (close > 0 and last > 0) else 0
                    change_pct = (change / close * 100) if close > 0 else 0

                    result.append({
                        "symbol": symbol,
                        "last": last,
                        "bid": bid,
                        "ask": ask,
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": ticker.volume or 0,
                    })

                    ib.cancelMktData(contract)
                except Exception as e:
                    logger.debug(f"Watchlist data error for {symbol}: {e}")
                    result.append({
                        "symbol": symbol, "last": 0, "bid": 0, "ask": 0,
                        "change": 0, "change_pct": 0, "volume": 0,
                        "error": str(e),
                    })

            # Restore live data type
            try:
                ib.reqMarketDataType(1)
            except Exception:
                pass

        return jsonify({"symbols": _watchlist, "data": result})
    except Exception as e:
        logger.error(f"Failed to get watchlist: {e}")
        return jsonify({"symbols": _watchlist, "data": [], "error": str(e)})


@app.route('/api/watchlist', methods=['POST'])
def api_update_watchlist():
    """Update watchlist symbols."""
    global _watchlist
    
    try:
        data = request.json
        symbols = data.get('symbols', [])
        
        # Validate and clean symbols
        _watchlist = [s.upper().strip() for s in symbols if s.strip()][:10]  # Max 10 symbols
        
        return jsonify({"success": True, "watchlist": _watchlist})
    except Exception as e:
        logger.error(f"Failed to update watchlist: {e}")
        return jsonify({"success": False, "error": str(e)})


# ---------------------------------------------------------------------------
# API Key authentication for remote trade API
# ---------------------------------------------------------------------------
TRADE_API_KEY = os.environ.get("TRADE_API_KEY", "saiyan-trade-2026")


def require_api_key(f):
    """Decorator: require X-API-Key header for remote endpoints."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key, TRADE_API_KEY):
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated


@app.route('/api/trade', methods=['POST'])
@require_api_key
def api_trade():
    """
    Remote trade endpoint — supports single AND scaled (multi-entry) orders.
    
    Accepts a trade request, computes position size using real account data,
    places one or more limit orders, and returns the results.
    
    === Single entry ===
    Request:
        {"ticker":"HOOD", "action":"BUY", "limit_price":25.50, "stop_loss":24.00}
    
    === Scaled entries (sent by Discord bot) ===
    Request:
        {
            "ticker": "HOOD",
            "action": "BUY",
            "entries": [
                {"price": 24.50, "pct": 40, "label": "Scout", "stop_loss": 23.00},
                {"price": 25.50, "pct": 60, "label": "Confirm", "stop_loss": 24.00}
            ],
            "source": "discord-bot"
        }
    
    Response (scaled):
        {
            "success": true,
            "orders": [
                {"order_id": 7, "status": "PreSubmitted", "shares": 15, "price": 24.50, "label": "Scout"},
                {"order_id": 8, "status": "PreSubmitted", "shares": 24, "price": 25.50, "label": "Confirm"}
            ],
            "total_shares": 39,
            "total_value": 994.50,
            "account_capital": 100000
        }
    """
    ib = get_ib()
    if not ib or not ib.isConnected():
        return jsonify({"success": False, "error": "Not connected to IB Gateway"})

    try:
        data = request.json
        ticker = data.get("ticker", "").upper().strip()
        action = data.get("action", "BUY").upper().strip()
        source = data.get("source", "api")
        entries = data.get("entries", None)  # Scaled entries array

        # --- Validation ---
        if not ticker:
            return jsonify({"success": False, "error": "Missing ticker"})
        if action not in ("BUY", "SELL"):
            return jsonify({"success": False, "error": f"Invalid action: {action}"})

        # --- Get account capital for position sizing ---
        with _ib_lock:
            track_api_call()
            try:
                ib.cancelAccountSummary()
            except Exception:
                pass
            ib.reqAccountSummary()
            ib.sleep(0.5)

            summary = {}
            for item in ib.accountSummary():
                summary[item.tag] = item.value

            try:
                ib.cancelAccountSummary()
            except Exception:
                pass

        net_liq = float(summary.get("NetLiquidation", 0) or 0)
        account_capital = max(net_liq, 50000)

        from ib_async import Stock, LimitOrder

        contract = Stock(ticker, 'SMART', 'USD')

        with _ib_lock:
            track_api_call()
            ib.qualifyContracts(contract)

        # ---- Sizing overrides from caller ----
        dollar_amount = float(data.get("dollar_amount", 0))
        share_count = int(data.get("share_count", 0))

        # ================================================================
        # SCALED ENTRIES: multiple orders at different prices
        # ================================================================
        if entries and isinstance(entries, list) and len(entries) > 1:
            avg_price = sum(e["price"] for e in entries) / len(entries)

            # Determine total_shares: dollar > explicit > risk-based
            if dollar_amount > 0:
                total_shares = max(1, -(-int(dollar_amount / avg_price)))  # ceil
            elif share_count > 0:
                total_shares = share_count
            else:
                risk_per_trade = 0.02
                max_position_pct = 0.10
                widest_stop = min(e.get("stop_loss", avg_price * 0.95) for e in entries)
                risk_per_share = abs(avg_price - widest_stop) if widest_stop > 0 else avg_price * 0.05
                total_by_risk = int((account_capital * risk_per_trade) / risk_per_share) if risk_per_share > 0 else 1
                total_by_max = int((account_capital * max_position_pct) / avg_price) if avg_price > 0 else 1
                total_shares = max(1, min(total_by_risk, total_by_max))

            # --- Scaled entries: place Order 1 now, queue the rest ---
            # Order 2+ are CONDITIONAL — only placed after prior fill + price confirm.
            # A background monitor thread handles the queueing.
            order_results = []
            total_value = 0
            queued_entries = []

            for idx, entry in enumerate(entries):
                entry_price = float(entry["price"])
                pct = int(entry.get("pct", round(100 / len(entries))))
                label = entry.get("label", "")
                entry_shares = max(1, int(total_shares * pct / 100))

                if entry_price <= 0:
                    order_results.append({
                        "success": False, "price": entry_price,
                        "label": label, "error": "Invalid price",
                    })
                    continue

                if idx == 0:
                    # --- Place first order immediately ---
                    with _ib_lock:
                        track_api_call()
                        order = LimitOrder(action, entry_shares, entry_price)
                        trade = ib.placeOrder(contract, order)
                        ib.sleep(0.3)

                    order_results.append({
                        "success": True,
                        "order_id": trade.order.orderId,
                        "status": trade.orderStatus.status,
                        "shares": entry_shares,
                        "price": entry_price,
                        "pct": pct,
                        "label": label,
                    })
                    total_value += entry_shares * entry_price
                    first_order_id = trade.order.orderId

                    logger.info(
                        f"[TRADE API] {source}: {action} {entry_shares} {ticker} "
                        f"@ ${entry_price:.2f} [{label}] → ID={trade.order.orderId}"
                    )
                else:
                    # --- Queue remaining entries as conditional ---
                    stop_loss = float(entry.get("stop_loss", 0))
                    queued_entries.append({
                        "ticker": ticker,
                        "action": action,
                        "shares": entry_shares,
                        "price": entry_price,
                        "pct": pct,
                        "label": label,
                        "stop_loss": stop_loss,
                        "source": source,
                    })
                    order_results.append({
                        "success": True,
                        "order_id": "PENDING",
                        "status": "Conditional",
                        "shares": entry_shares,
                        "price": entry_price,
                        "pct": pct,
                        "label": f"{label} (waits for entry {idx} fill)",
                    })
                    total_value += entry_shares * entry_price

            # Queue conditional entries for the background monitor
            if queued_entries:
                first_stop = float(entries[0].get("stop_loss", 0))
                with _pending_lock:
                    _pending_entries.append({
                        "ticker": ticker,
                        "action": action,
                        "first_order_id": first_order_id,
                        "first_stop": first_stop,
                        "first_price": float(entries[0]["price"]),
                        "remaining": queued_entries,
                        "created": time.time(),
                        "state": "waiting_fill",  # waiting_fill → confirming → done/cancelled
                    })
                logger.info(
                    f"[TRADE API] {len(queued_entries)} conditional entries queued "
                    f"for {ticker} (waiting for order {first_order_id} fill)"
                )

            any_success = any(o.get("success") for o in order_results)
            return jsonify({
                "success": any_success,
                "scaled": True,
                "orders": order_results,
                "total_shares": sum(o.get("shares", 0) for o in order_results if o.get("success")),
                "total_value": round(total_value, 2),
                "account_capital": round(account_capital, 2),
                "message": f"{action} {ticker} — entry 1 placed, {len(queued_entries)} conditional",
            })

        # ================================================================
        # SINGLE ENTRY: original behavior
        # ================================================================
        else:
            limit_price = float(data.get("limit_price", 0))
            stop_loss = float(data.get("stop_loss", 0))

            # If entries array has exactly 1 entry, use it
            if entries and len(entries) == 1:
                limit_price = float(entries[0].get("price", limit_price))
                stop_loss = float(entries[0].get("stop_loss", stop_loss))

            if limit_price <= 0:
                return jsonify({"success": False, "error": "Invalid limit_price"})

            # Position sizing: dollar > explicit > risk-based
            if dollar_amount > 0:
                import math as _math
                shares = max(1, _math.ceil(dollar_amount / limit_price))
            elif share_count > 0:
                shares = share_count
            else:
                risk_per_trade = 0.02
                max_position_pct = 0.10
                risk_per_share = abs(limit_price - stop_loss) if stop_loss > 0 else limit_price * 0.05
                shares_by_risk = int((account_capital * risk_per_trade) / risk_per_share) if risk_per_share > 0 else 1
                shares_by_max = int((account_capital * max_position_pct) / limit_price) if limit_price > 0 else 1
                shares = max(1, min(shares_by_risk, shares_by_max))

            with _ib_lock:
                track_api_call()
                order = LimitOrder(action, shares, limit_price)
                trade = ib.placeOrder(contract, order)
                ib.sleep(0.5)

            order_id = trade.order.orderId
            status = trade.orderStatus.status

            logger.info(f"[TRADE API] {source}: {action} {shares} {ticker} @ ${limit_price:.2f} → ID={order_id} status={status}")

            return jsonify({
                "success": True,
                "scaled": False,
                "order_id": order_id,
                "status": status,
                "shares": shares,
                "entry_price": limit_price,
                "position_value": round(shares * limit_price, 2),
                "account_capital": round(account_capital, 2),
                "message": f"{action} {shares} {ticker} @ ${limit_price:.2f} LMT",
            })

    except Exception as e:
        logger.error(f"[TRADE API] Error: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/trade/status', methods=['GET'])
@require_api_key
def api_trade_status():
    """
    Get current account status for remote bots.
    Returns positions, open orders, and account summary.
    """
    ib = get_ib()
    if not ib or not ib.isConnected():
        return jsonify({"connected": False})

    try:
        with _ib_lock:
            track_api_call()

            # Account summary
            try:
                ib.cancelAccountSummary()
            except Exception:
                pass
            ib.reqAccountSummary()
            ib.sleep(0.5)
            summary = {}
            for item in ib.accountSummary():
                summary[item.tag] = item.value
            try:
                ib.cancelAccountSummary()
            except Exception:
                pass

            # Positions
            ib.reqPositions()
            ib.sleep(0.3)
            positions = []
            for pos in ib.positions():
                if pos.position != 0:
                    positions.append({
                        "symbol": pos.contract.symbol,
                        "quantity": int(pos.position),
                        "avg_cost": pos.avgCost,
                    })

            # Open orders
            ib.reqOpenOrders()
            ib.sleep(0.3)
            orders = []
            for trade in ib.openTrades():
                o = trade.order
                orders.append({
                    "order_id": o.orderId,
                    "symbol": trade.contract.symbol,
                    "action": o.action,
                    "quantity": int(o.totalQuantity),
                    "order_type": o.orderType,
                    "price": o.lmtPrice if o.orderType == "LMT" else None,
                    "status": trade.orderStatus.status,
                })

        return jsonify({
            "connected": True,
            "net_liquidation": float(summary.get("NetLiquidation", 0) or 0),
            "buying_power": float(summary.get("BuyingPower", 0) or 0),
            "positions": positions,
            "open_orders": orders,
        })
    except Exception as e:
        logger.error(f"[TRADE STATUS] Error: {e}")
        return jsonify({"connected": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Background monitor for conditional scaled entries
# ---------------------------------------------------------------------------

def _conditional_entry_monitor():
    """
    Background thread that monitors pending conditional entries.

    Flow for each pending group:
      1. WAITING_FILL — poll until the first order fills.
         If the first order is cancelled or the stock drops below
         the first entry's stop-loss, cancel the whole group.
      2. CONFIRMING — after fill, wait for a price "confirmation":
         price must stay above the first entry price for 60s.
         This prevents adding to a position that immediately reverses.
      3. Place the next conditional order and repeat for remaining.
      4. DONE — all entries placed or group cancelled.

    The monitor runs every 10 seconds.
    """
    logger.info("[MONITOR] Conditional entry monitor started")

    while True:
        time.sleep(10)

        ib = get_ib()
        if not ib or not ib.isConnected():
            continue

        with _pending_lock:
            groups = list(_pending_entries)

        completed = []

        for group in groups:
            state = group.get("state", "waiting_fill")
            ticker = group["ticker"]
            action = group["action"]
            age_min = (time.time() - group["created"]) / 60

            # --- Timeout: cancel after 24 hours ---
            if age_min > 24 * 60:
                logger.warning(f"[MONITOR] {ticker} conditional entries expired (24h)")
                group["state"] = "cancelled"
                completed.append(group)
                continue

            if state == "waiting_fill":
                # Check if first order has filled
                first_id = group["first_order_id"]
                filled = False
                cancelled = False

                try:
                    with _ib_lock:
                        track_api_call()
                        for trade in ib.trades():
                            if trade.order.orderId == first_id:
                                status = trade.orderStatus.status
                                if status == "Filled":
                                    filled = True
                                elif status in ("Cancelled", "ApiCancelled", "Inactive"):
                                    cancelled = True
                                break
                except Exception as e:
                    logger.debug(f"[MONITOR] Error checking order {first_id}: {e}")
                    continue

                if cancelled:
                    logger.info(f"[MONITOR] {ticker} entry 1 cancelled → skipping remaining entries")
                    group["state"] = "cancelled"
                    completed.append(group)
                elif filled:
                    logger.info(f"[MONITOR] {ticker} entry 1 filled → starting confirmation window")
                    group["state"] = "confirming"
                    group["confirm_start"] = time.time()

            elif state == "confirming":
                # Price confirmation: wait 60s with price above entry
                confirm_start = group.get("confirm_start", time.time())
                elapsed = time.time() - confirm_start
                first_price = group["first_price"]
                first_stop = group["first_stop"]

                # Get current price
                try:
                    from ib_async import Stock as _Stock
                    contract = _Stock(ticker, 'SMART', 'USD')
                    with _ib_lock:
                        track_api_call()
                        ib.qualifyContracts(contract)
                        t = ib.reqMktData(contract, '', True, False)
                        ib.sleep(0.5)
                        cur_price = t.last or t.close or 0
                        ib.cancelMktData(contract)
                except Exception as e:
                    logger.debug(f"[MONITOR] Price check failed for {ticker}: {e}")
                    continue

                is_long = (action == "BUY")

                # Check stop hit → cancel
                if first_stop > 0:
                    if (is_long and cur_price < first_stop) or (not is_long and cur_price > first_stop):
                        logger.info(f"[MONITOR] {ticker} hit stop ${first_stop:.2f} → cancelling remaining entries")
                        group["state"] = "cancelled"
                        completed.append(group)
                        continue

                # Check confirmation (price above entry for longs, below for shorts)
                price_ok = (cur_price >= first_price) if is_long else (cur_price <= first_price)

                if price_ok and elapsed >= 60:
                    # Confirmed — place next conditional order
                    remaining = group.get("remaining", [])
                    if remaining:
                        next_entry = remaining.pop(0)
                        try:
                            from ib_async import Stock as _Stock2, LimitOrder as _LO
                            contract = _Stock2(next_entry["ticker"], 'SMART', 'USD')
                            with _ib_lock:
                                track_api_call()
                                ib.qualifyContracts(contract)
                                order = _LO(next_entry["action"], next_entry["shares"], next_entry["price"])
                                trade = ib.placeOrder(contract, order)
                                ib.sleep(0.3)

                            logger.info(
                                f"[MONITOR] Placed conditional: {next_entry['action']} "
                                f"{next_entry['shares']} {next_entry['ticker']} @ ${next_entry['price']:.2f} "
                                f"[{next_entry.get('label', '')}] → ID={trade.order.orderId}"
                            )
                        except Exception as e:
                            logger.error(f"[MONITOR] Failed to place conditional order for {ticker}: {e}")

                        if remaining:
                            # More entries — go back to waiting for this one to fill
                            group["first_order_id"] = trade.order.orderId
                            group["first_price"] = next_entry["price"]
                            group["first_stop"] = next_entry.get("stop_loss", 0)
                            group["state"] = "waiting_fill"
                        else:
                            group["state"] = "done"
                            completed.append(group)
                    else:
                        group["state"] = "done"
                        completed.append(group)
                elif not price_ok:
                    # Price reversed — reset confirmation timer
                    group["confirm_start"] = time.time()

        # Clean up completed groups
        if completed:
            with _pending_lock:
                for g in completed:
                    if g in _pending_entries:
                        _pending_entries.remove(g)


@app.route('/api/analyze/<ticker>')
@require_api_key
def api_analyze(ticker):
    """
    Remote stock analysis endpoint — runs scanner + technical analyzer.

    Returns comprehensive analysis JSON:
      - Regime classification, volatility, strategy recommendation
      - Technical indicators (RSI, ATR, EMAs, VPES)
      - Entry zones, stop loss, targets, EV, R:R
      - Volume pullback pattern detection
      - Support/resistance levels
      - Position sizing (Kelly, vol-adjusted)

    Usage:
      GET /api/analyze/AAPL  (with X-API-Key header)
    """
    ticker = ticker.upper().strip()
    result = {"ticker": ticker, "success": False}

    # --- 1. Run the unified scanner (regime, strategy, EV, entries) ---
    try:
        import importlib
        import sys as _sys

        # Import scanner dynamically (avoids circular imports)
        scanner_path = os.path.join(os.path.dirname(__file__), "scanner.py")
        spec = importlib.util.spec_from_file_location("scanner", scanner_path)
        scanner_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner_mod)

        scan = scanner_mod.scan_stock(ticker)
        if scan:
            from dataclasses import asdict as _asdict
            scan_dict = _asdict(scan)
            # Remove None/NaN values for clean JSON
            for k, v in list(scan_dict.items()):
                if v is None:
                    scan_dict[k] = None
                elif isinstance(v, float) and (v != v):  # NaN check
                    scan_dict[k] = None
            result["scan"] = scan_dict
            result["success"] = True
    except Exception as e:
        logger.warning(f"Scanner error for {ticker}: {e}")
        result["scan_error"] = str(e)

    # --- 2. Run technical analyzer (EMAs, RSI, VPES, support/resistance) ---
    try:
        analyzer_path = os.path.join(os.path.dirname(__file__), "stock_analyzer.py")
        spec2 = importlib.util.spec_from_file_location("stock_analyzer", analyzer_path)
        analyzer_mod = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(analyzer_mod)

        tech = analyzer_mod.analyze_stock(ticker)
        if tech:
            info = analyzer_mod.get_stock_info(ticker)
            earnings = analyzer_mod.get_earnings(ticker)
            result["technical"] = {
                "price": round(tech.price, 2),
                "change_1d": round(tech.change_1d, 2),
                "change_1w": round(tech.change_1w, 2),
                "change_1m": round(tech.change_1m, 2),
                "trend": tech.trend_position.value,
                "rsi": round(tech.rsi, 2),
                "atr": round(tech.atr, 2),
                "atr_pct": round(tech.atr_pct, 2),
                "volume_ratio": round(tech.volume_ratio, 2),
                "vpes": round(tech.vpes, 4),
                "support": round(tech.support_level, 2),
                "resistance": round(tech.resistance_level, 2),
                "emas": {
                    str(e.ema_period): {
                        "value": round(e.ema_value, 2),
                        "position": e.position,
                        "distance_pct": round(e.distance_pct, 2),
                    }
                    for e in tech.ema_analysis
                },
            }
            if info:
                result["info"] = {
                    "name": info.get("name", ticker),
                    "sector": info.get("sector", ""),
                    "industry": info.get("industry", ""),
                    "market_cap": info.get("market_cap"),
                    "pe_ratio": info.get("pe_ratio"),
                    "forward_pe": info.get("forward_pe"),
                    "52w_high": info.get("52w_high"),
                    "52w_low": info.get("52w_low"),
                }
            if earnings:
                result["earnings"] = {
                    "upcoming": earnings.get("upcoming"),
                    "recent": earnings.get("recent", [])[:2],
                }
            result["success"] = True
    except Exception as e:
        logger.warning(f"Technical analyzer error for {ticker}: {e}")
        result["technical_error"] = str(e)

    return jsonify(result)


@app.route('/api/pending_entries')
def api_pending_entries():
    """Get status of conditional/pending scaled entries."""
    with _pending_lock:
        return jsonify([
            {
                "ticker": g["ticker"],
                "action": g["action"],
                "state": g["state"],
                "first_order_id": g.get("first_order_id"),
                "remaining_count": len(g.get("remaining", [])),
                "age_min": round((time.time() - g["created"]) / 60, 1),
            }
            for g in _pending_entries
        ])


def main():
    """Run the trading GUI server."""
    # Connect to IB on startup
    init_ib()

    # Start conditional entry monitor
    monitor = threading.Thread(target=_conditional_entry_monitor, daemon=True)
    monitor.start()
    
    port = int(os.getenv("GUI_PORT", "8080"))
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║                  Trading Dashboard v2.0                      ║
╠══════════════════════════════════════════════════════════════╣
║  Open your browser to: http://localhost:{port}                 ║
║                                                              ║
║  Features:                                                   ║
║    • Connection health monitoring                            ║
║    • Risk metrics panel (Excess Liquidity, Daily P&L)        ║
║    • Order confirmation with sanity checks                   ║
║    • Quick trade buttons (close position, flatten all)       ║
║    • Extended hours trading toggle                           ║
║    • Futures support (ES, NQ, YM, CL, GC)                    ║
║                                                              ║
║  Press Ctrl+C to stop the server                             ║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)


if __name__ == "__main__":
    main()
