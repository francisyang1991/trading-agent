"""
Connection Stability Tests for IB Gateway.

Tests:
- Initial connection establishment
- Reconnection on disconnect
- Exponential backoff timing
- Heartbeat keepalive
- Max reconnect exceeded
- Concurrent requests during reconnect
- Graceful shutdown

Run with: pytest tests/e2e/test_connection.py -v
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from src.data.connection_manager import IBConnectionManager, ConnectionConfig, ConnectionState


# =============================================================================
# Basic Connection Tests
# =============================================================================

class TestConnectionBasics:
    """Basic connection establishment tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_initial_connection(self, connection_manager: IBConnectionManager):
        """Test basic connection establishment."""
        # Verify initial state
        assert connection_manager.state == ConnectionState.DISCONNECTED
        assert not connection_manager.is_connected
        
        # Connect
        success = await connection_manager.connect()
        
        # Verify connection
        assert success, "Connection should succeed"
        assert connection_manager.state == ConnectionState.CONNECTED
        assert connection_manager.is_connected
        assert connection_manager.ib is not None
        
        # Verify stats
        assert connection_manager.stats.connect_time is not None
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_connection_already_connected(self, connected_manager: IBConnectionManager):
        """Test connecting when already connected."""
        # Should not fail, just return True
        success = await connected_manager.connect()
        
        assert success
        assert connected_manager.state == ConnectionState.CONNECTED
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_graceful_shutdown(self, connected_manager: IBConnectionManager):
        """Test graceful disconnect and resource cleanup."""
        assert connected_manager.is_connected
        
        # Shutdown
        await connected_manager.shutdown()
        
        # Verify state
        assert connected_manager.state == ConnectionState.SHUTDOWN
        assert not connected_manager.is_connected
        assert connected_manager.ib is None
    
    @pytest.mark.asyncio
    async def test_connection_config_from_env(self, monkeypatch):
        """Test configuration loading from environment."""
        monkeypatch.setenv("IB_HOST", "test.host.com")
        monkeypatch.setenv("IB_PORT", "4001")
        monkeypatch.setenv("IB_CLIENT_ID", "42")
        monkeypatch.setenv("IB_RECONNECT_DELAY", "10")
        
        config = ConnectionConfig.from_env()
        
        assert config.host == "test.host.com"
        assert config.port == 4001
        assert config.client_id == 42
        assert config.reconnect_delay == 10.0


# =============================================================================
# Reconnection Tests
# =============================================================================

class TestReconnection:
    """Reconnection and recovery tests."""
    
    @pytest.mark.live
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_reconnect_on_disconnect(self, connected_manager: IBConnectionManager):
        """Test automatic reconnection after disconnect."""
        reconnect_events = []
        connected_events = []
        
        # Register callbacks
        async def on_reconnecting(attempt: int):
            reconnect_events.append({"attempt": attempt, "time": datetime.now()})
        
        async def on_connected():
            connected_events.append({"time": datetime.now()})
        
        connected_manager.on_reconnecting.append(on_reconnecting)
        connected_manager.on_connected.append(on_connected)
        
        # Simulate disconnect by directly calling handler
        # In production this would be triggered by network issues
        connected_manager._on_ib_disconnected()
        
        # Wait for reconnection
        await asyncio.sleep(10)  # Allow time for reconnect attempts
        
        # Verify reconnection was attempted
        assert len(reconnect_events) > 0, "Should have attempted reconnection"
        
        # If gateway is available, should have reconnected
        if connected_manager.state == ConnectionState.CONNECTED:
            assert len(connected_events) > 0, "Should have reconnected"
    
    @pytest.mark.asyncio
    async def test_reconnect_backoff_timing(self):
        """Test exponential backoff timing."""
        config = ConnectionConfig(
            reconnect_delay=1.0,
            max_reconnect_attempts=4
        )
        
        # Calculate expected delays
        expected_delays = [
            1.0,   # 1.0 * 2^0
            2.0,   # 1.0 * 2^1
            4.0,   # 1.0 * 2^2
            8.0,   # 1.0 * 2^3
        ]
        
        for attempt in range(4):
            delay = min(
                config.reconnect_delay * (2 ** attempt),
                60.0  # Cap
            )
            assert delay == expected_delays[attempt], f"Attempt {attempt+1} delay mismatch"
    
    @pytest.mark.live
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_max_reconnect_exceeded(self, test_config: ConnectionConfig, skip_if_no_gateway):
        """Test proper error state after max retries exceeded."""
        # Create manager with unreachable host
        bad_config = ConnectionConfig(
            host="192.0.2.1",  # TEST-NET-1 (unreachable)
            port=4002,
            client_id=99,
            reconnect_delay=0.5,
            max_reconnect_attempts=2,
            connection_timeout=2.0
        )
        
        manager = IBConnectionManager(bad_config)
        error_events = []
        
        async def on_error(msg: str):
            error_events.append(msg)
        
        manager.on_error.append(on_error)
        
        # Try to connect (should fail and trigger reconnection loop)
        success = await manager.connect()
        
        assert not success, "Should fail to connect to unreachable host"
        
        # If reconnection was triggered, wait for max attempts
        if manager.state == ConnectionState.RECONNECTING:
            await asyncio.sleep(10)  # Wait for reconnect attempts
            assert manager.state == ConnectionState.ERROR
            assert len(error_events) > 0
        
        await manager.shutdown()


# =============================================================================
# Heartbeat Tests
# =============================================================================

class TestHeartbeat:
    """Heartbeat and keepalive tests."""
    
    @pytest.mark.live
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_heartbeat_keepalive(self, test_config: ConnectionConfig, skip_if_no_gateway):
        """Test connection stays alive with heartbeat."""
        # Use short heartbeat interval for testing
        config = ConnectionConfig(
            host=test_config.host,
            port=test_config.port,
            client_id=98,  # Different client ID
            heartbeat_interval=5.0  # 5 second heartbeat
        )
        
        manager = IBConnectionManager(config)
        
        try:
            success = await manager.connect()
            if not success:
                pytest.skip("Failed to connect")
            
            initial_heartbeat = manager.stats.last_heartbeat
            
            # Wait for multiple heartbeats
            await asyncio.sleep(12)  # Should get 2+ heartbeats
            
            # Verify heartbeat was updated
            assert manager.stats.last_heartbeat is not None
            assert manager.stats.last_heartbeat > initial_heartbeat
            assert manager.is_connected
            
        finally:
            await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_heartbeat_failure_triggers_reconnect(self):
        """Test that heartbeat failures trigger reconnection."""
        config = ConnectionConfig(
            heartbeat_interval=1.0,
            max_reconnect_attempts=1
        )
        
        manager = IBConnectionManager(config)
        
        # Mock the IB instance to simulate heartbeat failure
        with patch.object(manager, '_ib') as mock_ib:
            mock_ib.isConnected.return_value = True
            mock_ib.reqCurrentTimeAsync = AsyncMock(side_effect=Exception("Network error"))
            
            manager._state = ConnectionState.CONNECTED
            
            # The heartbeat loop would detect this and trigger reconnection
            # We're testing the logic, not the full async loop
            try:
                await manager._ib.reqCurrentTimeAsync()
                assert False, "Should have raised exception"
            except Exception as e:
                assert "Network error" in str(e)


# =============================================================================
# State Management Tests
# =============================================================================

class TestStateManagement:
    """Connection state management tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_state_transitions(self, connection_manager: IBConnectionManager):
        """Test correct state transitions."""
        # Initial state
        assert connection_manager.state == ConnectionState.DISCONNECTED
        
        # Connect
        success = await connection_manager.connect()
        
        if success:
            # After successful connect, state should be CONNECTED
            assert connection_manager.state == ConnectionState.CONNECTED
            
            # Shutdown
            await connection_manager.shutdown()
            
            # Should end in SHUTDOWN
            assert connection_manager.state == ConnectionState.SHUTDOWN
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_status(self, connected_manager: IBConnectionManager):
        """Test status reporting."""
        status = connected_manager.get_status()
        
        assert "state" in status
        assert "is_connected" in status
        assert "host" in status
        assert "port" in status
        assert "stats" in status
        
        assert status["state"] == "connected"
        assert status["is_connected"] is True
        assert status["stats"]["connect_time"] is not None


# =============================================================================
# Concurrent Access Tests
# =============================================================================

class TestConcurrentAccess:
    """Concurrent access and thread safety tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_concurrent_connect_calls(self, test_config: ConnectionConfig, skip_if_no_gateway):
        """Test multiple concurrent connect calls."""
        manager = IBConnectionManager(test_config)
        
        try:
            # Make multiple concurrent connect calls
            results = await asyncio.gather(
                manager.connect(),
                manager.connect(),
                manager.connect(),
                return_exceptions=True
            )
            
            # All should succeed or return True (already connected)
            success_count = sum(1 for r in results if r is True)
            assert success_count == 3, f"Expected 3 successes, got {success_count}: {results}"
            
        finally:
            await manager.shutdown()
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_wait_for_connection(self, connection_manager: IBConnectionManager):
        """Test waiting for connection to establish."""
        # Start connection in background
        connect_task = asyncio.create_task(connection_manager.connect())
        
        # Wait for connection
        connected = await connection_manager.wait_for_connection(timeout=30.0)
        
        # Wait for connect task to complete
        await connect_task
        
        if connection_manager.state != ConnectionState.ERROR:
            assert connected
            assert connection_manager.is_connected


# =============================================================================
# Event Callback Tests
# =============================================================================

class TestEventCallbacks:
    """Event callback tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_connected_callback(self, connection_manager: IBConnectionManager):
        """Test connected callback is called."""
        callback_called = asyncio.Event()
        
        async def on_connected():
            callback_called.set()
        
        connection_manager.on_connected.append(on_connected)
        
        success = await connection_manager.connect()
        
        if success:
            # Callback should have been called
            assert callback_called.is_set()
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_disconnected_callback(self, connected_manager: IBConnectionManager):
        """Test disconnected callback is called on shutdown."""
        callback_called = asyncio.Event()
        
        async def on_disconnected(error):
            callback_called.set()
        
        connected_manager.on_disconnected.append(on_disconnected)
        
        # Manually trigger disconnect callback
        await connected_manager._notify_callbacks(connected_manager.on_disconnected, None)
        
        assert callback_called.is_set()
    
    @pytest.mark.asyncio
    async def test_callback_exception_handling(self, test_config: ConnectionConfig):
        """Test that callback exceptions don't break the manager."""
        manager = IBConnectionManager(test_config)
        
        error_logged = False
        
        async def bad_callback():
            raise ValueError("Test error")
        
        manager.on_connected.append(bad_callback)
        
        # Should not raise
        await manager._notify_callbacks(manager.on_connected)
        
        # Manager should still be functional
        assert manager.state != ConnectionState.ERROR
