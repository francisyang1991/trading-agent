#!/usr/bin/env python3
"""
Live Heartbeat Demo - Monitor IB Gateway connection with account status.

Usage:
    python -m tools.heartbeat_demo
"""

import asyncio
import os
import json
from datetime import datetime
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout

# Set client ID to avoid conflicts
os.environ.setdefault("IB_CLIENT_ID", "99")

from src.data import IBConnectionManager, ConnectionConfig, TradingMode


console = Console()


def create_status_panel(status: dict) -> Panel:
    """Create rich panel showing connection and account status."""
    
    # Connection info table
    conn_table = Table(title="Connection Status", show_header=False, box=None)
    conn_table.add_column("Field", style="cyan")
    conn_table.add_column("Value", style="green")
    
    conn_table.add_row("State", status.get("state", "unknown").upper())
    conn_table.add_row("Connected", "✓ YES" if status.get("is_connected") else "✗ NO")
    conn_table.add_row("Mode", status.get("trading_mode", "unknown").upper())
    conn_table.add_row("Host", f"{status.get('host')}:{status.get('port')}")
    conn_table.add_row("Client ID", str(status.get("client_id")))
    
    # Stats
    stats = status.get("stats", {})
    if stats.get("connect_time"):
        conn_table.add_row("Connected Since", stats["connect_time"][:19])
    if stats.get("last_heartbeat"):
        conn_table.add_row("Last Heartbeat", stats["last_heartbeat"][:19])
    conn_table.add_row("Reconnects", str(stats.get("total_reconnects", 0)))
    conn_table.add_row("Failed Heartbeats", str(stats.get("failed_heartbeats", 0)))
    
    if stats.get("last_error"):
        conn_table.add_row("Last Error", stats["last_error"][:50])
    
    # Account info table
    account = status.get("account")
    acct_table = Table(title="Account Status", show_header=False, box=None)
    acct_table.add_column("Field", style="cyan")
    acct_table.add_column("Value", style="yellow")
    
    if account:
        if account.get("net_liquidation"):
            acct_table.add_row("Net Liquidation", f"${account['net_liquidation']:,.2f}")
        if account.get("total_cash"):
            acct_table.add_row("Total Cash", f"${account['total_cash']:,.2f}")
        if account.get("buying_power"):
            acct_table.add_row("Buying Power", f"${account['buying_power']:,.2f}")
        if account.get("unrealized_pnl") is not None:
            pnl_color = "green" if account['unrealized_pnl'] >= 0 else "red"
            acct_table.add_row("Unrealized P&L", f"[{pnl_color}]${account['unrealized_pnl']:,.2f}[/]")
        if account.get("realized_pnl") is not None:
            pnl_color = "green" if account['realized_pnl'] >= 0 else "red"
            acct_table.add_row("Realized P&L", f"[{pnl_color}]${account['realized_pnl']:,.2f}[/]")
        acct_table.add_row("Positions", str(account.get("position_count", 0)))
        acct_table.add_row("Open Orders", str(account.get("open_order_count", 0)))
        if account.get("day_trades_remaining") is not None:
            acct_table.add_row("Day Trades Left", str(account['day_trades_remaining']))
        if account.get("updated_at"):
            acct_table.add_row("Updated", account["updated_at"][:19])
    else:
        acct_table.add_row("Status", "Loading...")
    
    # Combine tables
    layout = Layout()
    layout.split_row(
        Layout(conn_table, name="connection"),
        Layout(acct_table, name="account")
    )
    
    return Panel(
        layout,
        title=f"[bold blue]IB Gateway Monitor[/] - {datetime.now().strftime('%H:%M:%S')}",
        border_style="blue"
    )


async def heartbeat_demo():
    """Run the heartbeat demo with live status updates."""
    
    console.print("\n[bold cyan]IB Gateway Heartbeat Demo[/]\n")
    console.print("Connecting to IB Gateway...\n")
    
    config = ConnectionConfig.from_env()
    console.print(f"  Host: {config.host}:{config.port}")
    console.print(f"  Client ID: {config.client_id}")
    console.print(f"  Trading Mode: {config.trading_mode.value}\n")
    
    conn_manager = IBConnectionManager(config)
    
    # Register callbacks
    def on_connected():
        console.print("[green]✓ Connected to IB Gateway[/]")
    
    def on_disconnected(error):
        console.print(f"[yellow]⚠ Disconnected: {error}[/]")
    
    def on_reconnecting(attempt):
        console.print(f"[yellow]↻ Reconnecting (attempt {attempt})...[/]")
    
    conn_manager.on_connected.append(on_connected)
    conn_manager.on_disconnected.append(on_disconnected)
    conn_manager.on_reconnecting.append(on_reconnecting)
    
    try:
        # Connect
        success = await conn_manager.connect()
        
        if not success:
            console.print("[red]✗ Failed to connect to IB Gateway[/]")
            console.print("\nMake sure IB Gateway is running and accepting connections.")
            return
        
        console.print("\n[bold]Starting live monitor (Ctrl+C to stop)...[/]\n")
        
        # Live status updates
        with Live(create_status_panel(conn_manager.get_status()), refresh_per_second=1) as live:
            while True:
                await asyncio.sleep(1)
                status = conn_manager.get_status()
                live.update(create_status_panel(status))
                
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping monitor...[/]")
    finally:
        await conn_manager.shutdown()
        console.print("[green]Shutdown complete[/]")


def main():
    """Main entry point."""
    asyncio.run(heartbeat_demo())


if __name__ == "__main__":
    main()
