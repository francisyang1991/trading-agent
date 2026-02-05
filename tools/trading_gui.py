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
                <div class="btn-group">
                    <button class="btn danger btn-sm" onclick="flattenAll()">Flatten All Positions</button>
                    <button class="btn warning btn-sm" onclick="cancelAllOrders()">Cancel All Orders</button>
                </div>
            </div>
            
            <!-- Place Order -->
            <div class="card">
                <h2>Place Order</h2>
                
                <!-- Asset Type Tabs -->
                <div class="asset-tabs">
                    <div class="asset-tab active" data-type="stock" onclick="setAssetType('stock')">Stocks</div>
                    <div class="asset-tab" data-type="futures" onclick="setAssetType('futures')">Futures</div>
                </div>
                
                <form id="order-form" onsubmit="showOrderConfirmation(event)">
                    <input type="hidden" id="asset-type" value="stock">
                    
                    <!-- Stock Symbol -->
                    <div id="stock-fields">
                        <div class="form-row">
                            <div>
                                <label>Symbol</label>
                                <input type="text" id="symbol" placeholder="AAPL" style="text-transform: uppercase;">
                            </div>
                            <div>
                                <label>Quantity</label>
                                <input type="number" id="quantity" placeholder="100" min="1">
                            </div>
                        </div>
                    </div>
                    
                    <!-- Futures Fields -->
                    <div id="futures-fields" style="display: none;">
                        <div class="form-row">
                            <div>
                                <label>Contract</label>
                                <select id="futures-contract">
                                    <option value="ES">ES - S&P 500</option>
                                    <option value="NQ">NQ - Nasdaq 100</option>
                                    <option value="YM">YM - Dow Jones</option>
                                    <option value="CL">CL - Crude Oil</option>
                                    <option value="GC">GC - Gold</option>
                                </select>
                            </div>
                            <div>
                                <label>Expiry</label>
                                <select id="futures-expiry">
                                    <option value="">Select expiry...</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-row">
                            <div>
                                <label>Contracts</label>
                                <input type="number" id="futures-qty" placeholder="1" min="1" value="1">
                            </div>
                        </div>
                    </div>
                    
                    <div class="form-row">
                        <div>
                            <label>Action</label>
                            <select id="action">
                                <option value="BUY">BUY</option>
                                <option value="SELL">SELL</option>
                            </select>
                        </div>
                        <div>
                            <label>Order Type</label>
                            <select id="order-type" onchange="toggleLimitPrice()">
                                <option value="MKT">Market</option>
                                <option value="LMT">Limit</option>
                                <option value="STP">Stop</option>
                                <option value="STP_LMT">Stop Limit</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="form-row" id="price-fields" style="display: none;">
                        <div>
                            <label>Limit Price</label>
                            <input type="number" id="limit-price" placeholder="0.00" step="0.01">
                        </div>
                        <div id="stop-price-field" style="display: none;">
                            <label>Stop Price</label>
                            <input type="number" id="stop-price" placeholder="0.00" step="0.01">
                        </div>
                    </div>
                    
                    <!-- Extended Hours Toggle -->
                    <div class="form-row" style="align-items: center;">
                        <div class="toggle-container">
                            <div class="toggle" id="extended-hours-toggle" onclick="toggleExtendedHours()"></div>
                            <span class="text-muted">Extended Hours Trading</span>
                        </div>
                    </div>
                    
                    <button type="submit" class="btn primary" style="width: 100%; margin-top: 12px;">Preview Order</button>
                </form>
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
                            <th>Market Value</th>
                            <th>P&L</th>
                            <th>% Port</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="positions-table">
                        <tr><td colspan="7" class="text-muted" style="text-align:center;">No positions</td></tr>
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
        function setAssetType(type) {
            document.getElementById('asset-type').value = type;
            document.querySelectorAll('.asset-tab').forEach(tab => {
                tab.classList.toggle('active', tab.dataset.type === type);
            });
            document.getElementById('stock-fields').style.display = type === 'stock' ? 'block' : 'none';
            document.getElementById('futures-fields').style.display = type === 'futures' ? 'block' : 'none';
            
            if (type === 'futures') {
                updateFuturesExpiries();
            }
        }
        
        function updateFuturesExpiries() {
            const contract = document.getElementById('futures-contract').value;
            const expiry = document.getElementById('futures-expiry');
            
            // Generate next 4 quarterly expiries
            // IBKR uses single-digit year format: ESH6 = March 2026
            const months = ['H', 'M', 'U', 'Z']; // Mar, Jun, Sep, Dec
            const now = new Date();
            const year = now.getFullYear() % 10;  // Single digit year (2026 -> 6)
            const month = now.getMonth();
            
            expiry.innerHTML = '';
            for (let i = 0; i < 4; i++) {
                const idx = Math.floor((month + i * 3) / 3) % 4;
                const y = (year + Math.floor((month + i * 3) / 12)) % 10;
                const code = months[idx] + y;
                const fullYear = 2020 + (year + Math.floor((month + i * 3) / 12));
                const opt = document.createElement('option');
                opt.value = code;
                opt.textContent = code + ' (' + fullYear + ')';
                expiry.appendChild(opt);
            }
        }
        
        // Extended Hours Toggle
        function toggleExtendedHours() {
            extendedHours = !extendedHours;
            document.getElementById('extended-hours-toggle').classList.toggle('active', extendedHours);
        }
        
        // Order Type Toggle
        function toggleLimitPrice() {
            const orderType = document.getElementById('order-type').value;
            const priceFields = document.getElementById('price-fields');
            const stopField = document.getElementById('stop-price-field');
            
            priceFields.style.display = (orderType !== 'MKT') ? 'grid' : 'none';
            stopField.style.display = (orderType === 'STP_LMT') ? 'block' : 'none';
        }
        
        // Order Confirmation Modal
        function showOrderConfirmation(event) {
            event.preventDefault();
            
            const assetType = document.getElementById('asset-type').value;
            const action = document.getElementById('action').value;
            const orderType = document.getElementById('order-type').value;
            
            let symbol, quantity;
            if (assetType === 'stock') {
                symbol = document.getElementById('symbol').value.toUpperCase();
                quantity = parseInt(document.getElementById('quantity').value);
            } else {
                const contract = document.getElementById('futures-contract').value;
                const expiry = document.getElementById('futures-expiry').value;
                symbol = contract + expiry;
                quantity = parseInt(document.getElementById('futures-qty').value);
            }
            
            if (!symbol || !quantity) {
                showMessage('Please fill in all required fields', 'error');
                return;
            }
            
            const limitPrice = parseFloat(document.getElementById('limit-price').value) || null;
            const stopPrice = parseFloat(document.getElementById('stop-price').value) || null;
            
            pendingOrder = {
                asset_type: assetType,
                symbol: symbol,
                quantity: quantity,
                action: action,
                order_type: orderType,
                limit_price: limitPrice,
                stop_price: stopPrice,
                extended_hours: extendedHours
            };
            
            // Build preview
            let preview = `
                <div class="order-preview-row">
                    <span class="order-preview-label">Symbol</span>
                    <span class="order-preview-value">${symbol}</span>
                </div>
                <div class="order-preview-row">
                    <span class="order-preview-label">Action</span>
                    <span class="order-preview-value ${action === 'BUY' ? 'text-green' : 'text-red'}">${action}</span>
                </div>
                <div class="order-preview-row">
                    <span class="order-preview-label">Quantity</span>
                    <span class="order-preview-value">${quantity}${assetType === 'futures' ? ' contracts' : ' shares'}</span>
                </div>
                <div class="order-preview-row">
                    <span class="order-preview-label">Order Type</span>
                    <span class="order-preview-value">${orderType}</span>
                </div>
            `;
            
            if (limitPrice) {
                preview += `
                    <div class="order-preview-row">
                        <span class="order-preview-label">Limit Price</span>
                        <span class="order-preview-value">${formatCurrency(limitPrice)}</span>
                    </div>
                `;
            }
            
            if (extendedHours) {
                preview += `
                    <div class="order-preview-row">
                        <span class="order-preview-label">Extended Hours</span>
                        <span class="order-preview-value text-yellow">Enabled</span>
                    </div>
                `;
            }
            
            document.getElementById('order-preview').innerHTML = preview;
            
            // Add warnings
            let warnings = [];
            if (orderType === 'MKT') {
                warnings.push('Market orders execute at current market price');
            }
            if (extendedHours) {
                warnings.push('Extended hours may have lower liquidity and wider spreads');
            }
            if (assetType === 'futures') {
                warnings.push('Futures use leverage - risk management is critical');
            }
            
            document.getElementById('order-warnings').innerHTML = warnings.length 
                ? '<div class="warning-text">' + warnings.join('<br>') + '</div>'
                : '';
            
            document.getElementById('confirm-modal').classList.add('show');
        }
        
        function closeModal() {
            document.getElementById('confirm-modal').classList.remove('show');
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
                    tbody.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center;">No positions</td></tr>';
                    return;
                }
                
                const netLiq = accountData.net_liquidation || 1;
                
                tbody.innerHTML = positionsData.map(pos => {
                    const pctPort = ((pos.market_value || 0) / netLiq * 100).toFixed(1);
                    const pctClass = pctPort > 10 ? 'text-yellow' : '';
                    const secType = pos.sec_type || 'STK';
                    
                    return `
                        <tr>
                            <td><strong>${pos.symbol}</strong><span class="text-muted" style="font-size:10px;margin-left:4px;">${secType}</span></td>
                            <td class="${pos.quantity >= 0 ? 'text-green' : 'text-red'}">${pos.quantity}</td>
                            <td>${formatCurrency(pos.avg_cost)}</td>
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
            if (!pendingOrder) return;
            
            try {
                const response = await fetch('/api/order', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pendingOrder)
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showMessage(`Order placed: ${pendingOrder.action} ${pendingOrder.quantity} ${pendingOrder.symbol}`, 'success');
                    document.getElementById('order-form').reset();
                    closeModal();
                    fetchOrders();
                } else {
                    showMessage(`Order failed: ${data.error}`, 'error');
                }
            } catch (e) {
                showMessage('Failed to place order', 'error');
            }
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
                                <button class="btn btn-sm" onclick="quickBuy('${item.symbol}')">Buy</button>
                                <button class="btn btn-sm text-red" onclick="removeFromWatchlist('${item.symbol}')" style="padding:4px 6px;">✕</button>
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
            // Pre-fill the order form
            setAssetType('stock');
            document.getElementById('symbol').value = symbol;
            document.getElementById('quantity').value = 100;
            document.getElementById('action').value = 'BUY';
            document.getElementById('order-type').value = 'MKT';
            
            // Scroll to order form
            document.getElementById('order-form').scrollIntoView({ behavior: 'smooth' });
            showMessage(`Order form pre-filled for ${symbol}`, 'success');
        }
        
        // Setup
        document.getElementById('confirm-order-btn').addEventListener('click', placeOrder);
        document.getElementById('futures-contract').addEventListener('change', updateFuturesExpiries);
        
        // Initial load and auto-refresh
        refreshAll();
        fetchWatchlist();
        updateFuturesExpiries();
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
    """Get current positions with P&L using reqPnLSingle for accurate data."""
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify([])
    
    try:
        with _ib_lock:
            track_api_call()
            ib.reqPositions()
            ib.sleep(0.3)
            
            positions = []
            for pos in ib.positions():
                if pos.position != 0:
                    # Get contract details
                    contract = pos.contract
                    
                    # Try to get PnL using reqPnLSingle
                    pnl = 0
                    unrealized_pnl = 0
                    market_price = pos.avgCost  # Fallback
                    
                    try:
                        # Request single position PnL
                        ib.qualifyContracts(contract)
                        
                        # Use portfolio() which includes unrealizedPnL
                        for pv in ib.portfolio():
                            if pv.contract.conId == contract.conId:
                                unrealized_pnl = pv.unrealizedPNL or 0
                                market_price = pv.marketPrice or pos.avgCost
                                pnl = unrealized_pnl
                                break
                    except Exception as e:
                        logger.debug(f"Could not get PnL for {contract.symbol}: {e}")
                    
                    market_value = abs(pos.position * market_price)
                    
                    positions.append({
                        "symbol": pos.contract.symbol,
                        "quantity": int(pos.position),
                        "avg_cost": pos.avgCost,
                        "market_price": market_price,
                        "market_value": market_value,
                        "pnl": pnl,
                        "sec_type": pos.contract.secType,
                        "con_id": contract.conId
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
    """Get market data for watchlist symbols."""
    global _watchlist, _market_data
    
    ib = get_ib()
    
    if not ib or not ib.isConnected():
        return jsonify({"symbols": _watchlist, "data": {}})
    
    try:
        from ib_async import Stock
        
        with _ib_lock:
            track_api_call()
            
            result = []
            for symbol in _watchlist:
                contract = Stock(symbol, 'SMART', 'USD')
                
                try:
                    ib.qualifyContracts(contract)
                    
                    # Request market data snapshot
                    ticker = ib.reqMktData(contract, '', True, False)
                    ib.sleep(0.3)
                    
                    last_price = ticker.last or ticker.close or 0
                    change = 0
                    change_pct = 0
                    
                    if ticker.close and ticker.close > 0:
                        if ticker.last:
                            change = ticker.last - ticker.close
                            change_pct = (change / ticker.close) * 100
                    
                    result.append({
                        "symbol": symbol,
                        "last": last_price,
                        "bid": ticker.bid or 0,
                        "ask": ticker.ask or 0,
                        "change": change,
                        "change_pct": change_pct,
                        "volume": ticker.volume or 0
                    })
                    
                    # Cancel market data to avoid rate limits
                    ib.cancelMktData(contract)
                    
                except Exception as e:
                    logger.debug(f"Could not get data for {symbol}: {e}")
                    result.append({
                        "symbol": symbol,
                        "last": 0,
                        "bid": 0,
                        "ask": 0,
                        "change": 0,
                        "change_pct": 0,
                        "volume": 0,
                        "error": str(e)
                    })
        
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


def main():
    """Run the trading GUI server."""
    # Connect to IB on startup
    init_ib()
    
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
