"""
IBKR Client Portal Web API Client
Works with IBeam for authentication to the REST-based Web API.

IBeam (https://github.com/Voyz/ibeam) handles:
- Automated authentication to Client Portal Gateway
- Session maintenance
- Headless operation (no display required)

This client makes REST calls to the authenticated Gateway.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import pandas as pd
from loguru import logger

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    pass  # Optional: httpx only needed for IBKR Web API


class IBKRWebClient:
    """
    Client for IBKR Client Portal Web API.
    
    Requires IBeam to be running for authentication.
    IBeam Docker: docker run --env IBEAM_ACCOUNT=xxx --env IBEAM_PASSWORD=xxx -p 5000:5000 voyz/ibeam
    
    API Documentation: https://www.interactivebrokers.com/api/doc.html
    """
    
    def __init__(
        self,
        gateway_url: str = "https://localhost:5000",
        verify_ssl: bool = False,  # IBeam uses self-signed certs by default
        timeout: float = 30.0
    ):
        """
        Initialize IBKR Web API client.
        
        Args:
            gateway_url: IBeam Gateway URL (default https://localhost:5000)
            verify_ssl: Verify SSL certificates (False for self-signed)
            timeout: Request timeout in seconds
        """
        self.gateway_url = gateway_url.rstrip('/')
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        
        self._client: Optional[httpx.AsyncClient] = None
        self._authenticated = False
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
    
    async def connect(self) -> bool:
        """
        Initialize HTTP client and verify Gateway connection.
        
        Returns:
            True if connected and authenticated
        """
        if not HTTPX_AVAILABLE:
            logger.error("httpx not available")
            return False
        
        self._client = httpx.AsyncClient(
            base_url=self.gateway_url,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        
        # Check authentication status
        auth_status = await self.get_auth_status()
        
        if auth_status.get('authenticated'):
            self._authenticated = True
            logger.info(f"Connected to IBKR Web API at {self.gateway_url}")
            return True
        else:
            logger.warning("Gateway not authenticated. Ensure IBeam is running.")
            return False
    
    async def disconnect(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._authenticated = False
    
    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._client is not None and self._authenticated
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make HTTP request to Gateway.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            **kwargs: Additional request arguments
            
        Returns:
            JSON response as dict
        """
        if not self._client:
            raise RuntimeError("Client not connected")
        
        url = f"/v1/api{endpoint}"
        
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            
            if response.content:
                return response.json()
            return {}
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request error: {e}")
            raise
    
    # =========================================================================
    # Authentication & Session
    # =========================================================================
    
    async def get_auth_status(self) -> Dict[str, Any]:
        """
        Get authentication status.
        
        Returns:
            Dict with 'authenticated', 'competing', 'connected' fields
        """
        try:
            return await self._request("POST", "/iserver/auth/status")
        except Exception:
            return {"authenticated": False}
    
    async def tickle(self) -> Dict[str, Any]:
        """
        Keep session alive. Call every few minutes.
        
        Returns:
            Session status
        """
        return await self._request("POST", "/tickle")
    
    async def reauthenticate(self) -> Dict[str, Any]:
        """
        Trigger reauthentication.
        
        Returns:
            Reauthentication result
        """
        return await self._request("POST", "/iserver/reauthenticate")
    
    async def logout(self) -> Dict[str, Any]:
        """Logout from session."""
        return await self._request("POST", "/logout")
    
    # =========================================================================
    # Market Data
    # =========================================================================
    
    async def search_stocks(
        self,
        symbol: str,
        name: bool = True,
        sec_type: str = "STK"
    ) -> List[Dict[str, Any]]:
        """
        Search for stocks by symbol.
        
        Args:
            symbol: Stock symbol to search
            name: Search by name too
            sec_type: Security type (STK, OPT, FUT, etc.)
            
        Returns:
            List of matching contracts
        """
        response = await self._request(
            "POST",
            "/iserver/secdef/search",
            json={"symbol": symbol, "name": name, "secType": sec_type}
        )
        return response if isinstance(response, list) else []
    
    async def get_conid(self, symbol: str) -> Optional[int]:
        """
        Get contract ID for a stock symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Contract ID or None
        """
        results = await self.search_stocks(symbol)
        
        if results:
            # Find exact match
            for r in results:
                if r.get('symbol', '').upper() == symbol.upper():
                    return r.get('conid')
            # Return first result
            return results[0].get('conid')
        
        return None
    
    async def get_market_data_snapshot(
        self,
        conids: List[int],
        fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get market data snapshot for contracts.
        
        Args:
            conids: List of contract IDs
            fields: Fields to request (default: common fields)
            
        Returns:
            Market data for each contract
        """
        if fields is None:
            # Common fields: last price, bid, ask, volume, etc.
            fields = ["31", "84", "85", "86", "88", "7295"]
        
        conid_str = ",".join(str(c) for c in conids)
        fields_str = ",".join(fields)
        
        return await self._request(
            "GET",
            f"/iserver/marketdata/snapshot?conids={conid_str}&fields={fields_str}"
        )
    
    async def get_historical_data(
        self,
        conid: int,
        period: str = "1d",
        bar: str = "1min",
        outside_rth: bool = False
    ) -> pd.DataFrame:
        """
        Get historical market data.
        
        Args:
            conid: Contract ID
            period: Time period (1d, 1w, 1m, 3m, 6m, 1y, 2y, 3y, 5y)
            bar: Bar size (1min, 5min, 15min, 30min, 1h, 4h, 1d, 1w, 1m)
            outside_rth: Include outside regular trading hours
            
        Returns:
            DataFrame with OHLCV data
        """
        response = await self._request(
            "GET",
            f"/iserver/marketdata/history?conid={conid}&period={period}&bar={bar}&outsideRth={str(outside_rth).lower()}"
        )
        
        if not response or 'data' not in response:
            return pd.DataFrame()
        
        # Parse response
        data = response['data']
        
        df = pd.DataFrame(data)
        
        if df.empty:
            return df
        
        # Rename columns
        column_map = {
            'o': 'open',
            'h': 'high',
            'l': 'low',
            'c': 'close',
            'v': 'volume',
            't': 'timestamp'
        }
        df = df.rename(columns=column_map)
        
        # Convert timestamp
        if 'timestamp' in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            df = df.set_index('datetime')
            df = df.drop(columns=['timestamp'], errors='ignore')
        
        return df
    
    # =========================================================================
    # Orders
    # =========================================================================
    
    async def get_accounts(self) -> List[Dict[str, Any]]:
        """Get list of accounts."""
        response = await self._request("GET", "/portfolio/accounts")
        return response if isinstance(response, list) else []
    
    async def get_account_id(self) -> Optional[str]:
        """Get first account ID."""
        accounts = await self.get_accounts()
        if accounts:
            return accounts[0].get('accountId')
        return None
    
    async def place_order(
        self,
        account_id: str,
        conid: int,
        quantity: int,
        side: str,
        order_type: str = "MKT",
        price: Optional[float] = None,
        tif: str = "DAY"
    ) -> Dict[str, Any]:
        """
        Place an order.
        
        Args:
            account_id: Account ID
            conid: Contract ID
            quantity: Number of shares
            side: BUY or SELL
            order_type: MKT, LMT, STP, etc.
            price: Limit/stop price (required for LMT, STP)
            tif: Time in force (DAY, GTC, IOC, etc.)
            
        Returns:
            Order response
        """
        order = {
            "conid": conid,
            "secType": f"{conid}:STK",
            "orderType": order_type,
            "side": side,
            "quantity": quantity,
            "tif": tif
        }
        
        if price is not None:
            order["price"] = price
        
        response = await self._request(
            "POST",
            f"/iserver/account/{account_id}/orders",
            json={"orders": [order]}
        )
        
        # Handle order confirmation
        if isinstance(response, list) and response:
            first_response = response[0]
            
            # Check if confirmation needed
            if 'id' in first_response:
                # Confirm order
                confirm_response = await self._request(
                    "POST",
                    f"/iserver/reply/{first_response['id']}",
                    json={"confirmed": True}
                )
                return confirm_response
        
        return response
    
    async def place_market_order(
        self,
        symbol: str,
        quantity: int,
        side: str
    ) -> Dict[str, Any]:
        """
        Place a market order by symbol.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: BUY or SELL
            
        Returns:
            Order response
        """
        conid = await self.get_conid(symbol)
        if not conid:
            raise ValueError(f"Cannot find contract for {symbol}")
        
        account_id = await self.get_account_id()
        if not account_id:
            raise ValueError("No account found")
        
        return await self.place_order(
            account_id=account_id,
            conid=conid,
            quantity=quantity,
            side=side,
            order_type="MKT"
        )
    
    async def place_limit_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        limit_price: float
    ) -> Dict[str, Any]:
        """
        Place a limit order by symbol.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: BUY or SELL
            limit_price: Limit price
            
        Returns:
            Order response
        """
        conid = await self.get_conid(symbol)
        if not conid:
            raise ValueError(f"Cannot find contract for {symbol}")
        
        account_id = await self.get_account_id()
        if not account_id:
            raise ValueError("No account found")
        
        return await self.place_order(
            account_id=account_id,
            conid=conid,
            quantity=quantity,
            side=side,
            order_type="LMT",
            price=limit_price
        )
    
    async def get_orders(self) -> List[Dict[str, Any]]:
        """Get all open orders."""
        response = await self._request("GET", "/iserver/account/orders")
        return response.get('orders', []) if isinstance(response, dict) else []
    
    async def cancel_order(
        self,
        account_id: str,
        order_id: str
    ) -> Dict[str, Any]:
        """
        Cancel an order.
        
        Args:
            account_id: Account ID
            order_id: Order ID to cancel
            
        Returns:
            Cancellation response
        """
        return await self._request(
            "DELETE",
            f"/iserver/account/{account_id}/order/{order_id}"
        )
    
    # =========================================================================
    # Portfolio
    # =========================================================================
    
    async def get_positions(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get portfolio positions.
        
        Args:
            account_id: Account ID (uses first account if None)
            
        Returns:
            List of positions
        """
        if account_id is None:
            account_id = await self.get_account_id()
        
        if not account_id:
            return []
        
        response = await self._request(
            "GET",
            f"/portfolio/{account_id}/positions/0"
        )
        return response if isinstance(response, list) else []
    
    async def get_account_summary(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get account summary.
        
        Args:
            account_id: Account ID
            
        Returns:
            Account summary dict
        """
        if account_id is None:
            account_id = await self.get_account_id()
        
        if not account_id:
            return {}
        
        return await self._request(
            "GET",
            f"/portfolio/{account_id}/summary"
        )
    
    async def get_account_ledger(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get account ledger (cash balances, etc.).
        
        Args:
            account_id: Account ID
            
        Returns:
            Account ledger
        """
        if account_id is None:
            account_id = await self.get_account_id()
        
        if not account_id:
            return {}
        
        return await self._request(
            "GET",
            f"/portfolio/{account_id}/ledger"
        )


class IBeamManager:
    """
    Helper to manage IBeam Docker container.
    
    IBeam handles authentication to the Client Portal Gateway.
    https://github.com/Voyz/ibeam
    """
    
    def __init__(
        self,
        account: str,
        password: str,
        gateway_port: int = 5000,
        container_name: str = "ibeam"
    ):
        """
        Initialize IBeam manager.
        
        Args:
            account: IBKR account username
            password: IBKR account password
            gateway_port: Port to expose Gateway
            container_name: Docker container name
        """
        self.account = account
        self.password = password
        self.port = gateway_port
        self.container_name = container_name
    
    def get_docker_run_command(self) -> str:
        """
        Get Docker run command for IBeam.
        
        Returns:
            Docker command string
        """
        return (
            f"docker run -d "
            f"--name {self.container_name} "
            f"--env IBEAM_ACCOUNT={self.account} "
            f"--env IBEAM_PASSWORD={self.password} "
            f"-p {self.port}:5000 "
            f"voyz/ibeam"
        )
    
    def get_docker_compose_config(self) -> str:
        """
        Get Docker Compose configuration.
        
        Returns:
            docker-compose.yaml content
        """
        return f"""
services:
  ibeam:
    image: voyz/ibeam
    container_name: {self.container_name}
    environment:
      - IBEAM_ACCOUNT={self.account}
      - IBEAM_PASSWORD={self.password}
    ports:
      - "{self.port}:5000"
    network_mode: bridge
    restart: unless-stopped
"""
    
    async def check_gateway_status(self) -> bool:
        """
        Check if IBeam Gateway is running and authenticated.
        
        Returns:
            True if authenticated
        """
        try:
            async with IBKRWebClient(
                gateway_url=f"https://localhost:{self.port}"
            ) as client:
                return client.is_connected
        except Exception:
            return False
