"""
Reliability Tests for 24/7 Operation.

Long-running tests to validate:
- Connection stability over time
- Memory stability (no leaks)
- Periodic disconnect/reconnect recovery
- High-frequency request handling
- Market hours transitions

WARNING: These tests take a long time to run.
Run with: pytest tests/e2e/test_reliability.py -v -m "reliability"

For quick validation: pytest tests/e2e/test_reliability.py -v -m "not slow"
"""

import asyncio
import gc
import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

from src.data.connection_manager import IBConnectionManager, ConnectionConfig, ConnectionState
from src.data.ibkr_async_client import IBKRAsyncClient


# =============================================================================
# Memory Monitoring Helpers
# =============================================================================

def get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    if not PSUTIL_AVAILABLE:
        return 0.0
    
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)


def get_memory_delta(start_mb: float) -> float:
    """Get memory change since start."""
    current = get_memory_usage_mb()
    return current - start_mb


class MemoryMonitor:
    """Monitor memory usage over time."""
    
    def __init__(self):
        self.samples = []
        self.start_mb = get_memory_usage_mb()
    
    def sample(self):
        """Take a memory sample."""
        self.samples.append({
            "time": datetime.now(),
            "memory_mb": get_memory_usage_mb(),
            "delta_mb": get_memory_delta(self.start_mb)
        })
    
    def get_max_delta(self) -> float:
        """Get maximum memory increase from start."""
        if not self.samples:
            return 0.0
        return max(s["delta_mb"] for s in self.samples)
    
    def get_trend(self) -> float:
        """Get memory growth trend (MB per sample)."""
        if len(self.samples) < 2:
            return 0.0
        
        deltas = [s["delta_mb"] for s in self.samples]
        return (deltas[-1] - deltas[0]) / len(self.samples)


# =============================================================================
# Long-Running Connection Tests
# =============================================================================

class TestLongRunningConnection:
    """Tests for connection stability over extended periods."""
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_connection_stable_30min(self, test_config: ConnectionConfig, skip_if_no_gateway):
        """Test connection stability for 30 minutes."""
        duration_minutes = 30
        check_interval_seconds = 60
        
        config = ConnectionConfig(
            host=test_config.host,
            port=test_config.port,
            client_id=90,  # Unique client ID
            heartbeat_interval=30.0
        )
        
        manager = IBConnectionManager(config)
        disconnects = []
        
        async def on_disconnected(error):
            disconnects.append({"time": datetime.now(), "error": str(error)})
        
        manager.on_disconnected.append(on_disconnected)
        
        try:
            success = await manager.connect()
            if not success:
                pytest.skip("Failed to connect")
            
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
            check_count = 0
            
            while datetime.now() < end_time:
                # Verify still connected
                assert manager.is_connected, f"Connection lost after {check_count} checks"
                
                # Log status
                elapsed = (datetime.now() - start_time).total_seconds() / 60
                print(f"\rRunning: {elapsed:.1f}/{duration_minutes} min, disconnects: {len(disconnects)}", end="")
                
                check_count += 1
                await asyncio.sleep(check_interval_seconds)
            
            print()  # New line after progress
            
            # Report
            print(f"\nConnection stability test completed:")
            print(f"  Duration: {duration_minutes} minutes")
            print(f"  Checks: {check_count}")
            print(f"  Disconnects: {len(disconnects)}")
            print(f"  Total reconnects: {manager.stats.total_reconnects}")
            
            # Should have minimal disconnects for a stable test
            assert len(disconnects) < 3, f"Too many disconnects: {disconnects}"
            
        finally:
            await manager.shutdown()
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_connection_stable_1hour(self, test_config: ConnectionConfig, skip_if_no_gateway):
        """Test connection stability for 1 hour."""
        duration_minutes = 60
        check_interval_seconds = 120
        
        config = ConnectionConfig(
            host=test_config.host,
            port=test_config.port,
            client_id=91,
            heartbeat_interval=30.0
        )
        
        manager = IBConnectionManager(config)
        
        try:
            success = await manager.connect()
            if not success:
                pytest.skip("Failed to connect")
            
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
            
            while datetime.now() < end_time:
                assert manager.is_connected
                await asyncio.sleep(check_interval_seconds)
            
        finally:
            await manager.shutdown()


# =============================================================================
# Memory Stability Tests
# =============================================================================

class TestMemoryStability:
    """Tests for memory stability and leak detection."""
    
    @pytest.mark.skipif(not PSUTIL_AVAILABLE, reason="psutil not available")
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_memory_stable_repeated_operations(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test memory stability with repeated operations."""
        monitor = MemoryMonitor()
        iterations = 50
        
        monitor.sample()  # Initial sample
        
        for i in range(iterations):
            # Fetch data
            df = await connected_client.get_historical_data(
                symbol=test_symbols[0],
                bar_size="1 day",
                duration="5 D"
            )
            
            # Force garbage collection
            del df
            gc.collect()
            
            # Sample memory periodically
            if i % 10 == 0:
                monitor.sample()
                print(f"\rIteration {i}/{iterations}, memory delta: {monitor.samples[-1]['delta_mb']:.1f} MB", end="")
            
            await asyncio.sleep(0.5)
        
        print()
        monitor.sample()  # Final sample
        
        # Analyze results
        max_delta = monitor.get_max_delta()
        trend = monitor.get_trend()
        
        print(f"\nMemory stability test completed:")
        print(f"  Iterations: {iterations}")
        print(f"  Max memory increase: {max_delta:.1f} MB")
        print(f"  Memory trend: {trend:.2f} MB/sample")
        
        # Memory should not grow significantly
        assert max_delta < 100, f"Memory grew too much: {max_delta} MB"
        assert trend < 1.0, f"Memory trending upward: {trend} MB/sample"
    
    @pytest.mark.skipif(not PSUTIL_AVAILABLE, reason="psutil not available")
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_memory_stable_subscriptions(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test memory stability with subscriptions."""
        monitor = MemoryMonitor()
        bars_received = []
        
        def on_bar(bar):
            bars_received.append(bar)
            # Keep only recent bars to test memory management
            if len(bars_received) > 100:
                bars_received.pop(0)
        
        monitor.sample()
        
        # Subscribe
        await connected_client.subscribe_bars(test_symbols[0], on_bar)
        
        # Run for a while
        for i in range(6):  # 6 x 10 = 60 seconds
            await asyncio.sleep(10)
            monitor.sample()
            gc.collect()
            print(f"\r{(i+1)*10}s, bars: {len(bars_received)}, memory delta: {monitor.samples[-1]['delta_mb']:.1f} MB", end="")
        
        print()
        
        # Unsubscribe
        await connected_client.unsubscribe_bars(test_symbols[0])
        
        # Check memory
        max_delta = monitor.get_max_delta()
        print(f"\nMemory after subscriptions: +{max_delta:.1f} MB")
        
        assert max_delta < 50, f"Memory grew too much with subscriptions: {max_delta} MB"


# =============================================================================
# Recovery Tests
# =============================================================================

class TestRecovery:
    """Tests for disconnect/reconnect recovery."""
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_periodic_disconnect_recovery(
        self,
        test_config: ConnectionConfig,
        skip_if_no_gateway
    ):
        """Test recovery from periodic disconnects."""
        config = ConnectionConfig(
            host=test_config.host,
            port=test_config.port,
            client_id=92,
            reconnect_delay=2.0,
            max_reconnect_attempts=5
        )
        
        manager = IBConnectionManager(config)
        recovery_times = []
        
        try:
            success = await manager.connect()
            if not success:
                pytest.skip("Failed to connect")
            
            # Simulate 3 disconnects
            for i in range(3):
                print(f"\nSimulating disconnect {i+1}/3...")
                
                start_time = datetime.now()
                
                # Trigger disconnect
                manager._on_ib_disconnected()
                
                # Wait for reconnection
                reconnected = await manager.wait_for_connection(timeout=60.0)
                
                if reconnected:
                    recovery_time = (datetime.now() - start_time).total_seconds()
                    recovery_times.append(recovery_time)
                    print(f"Recovered in {recovery_time:.1f}s")
                else:
                    print("Failed to recover")
                
                await asyncio.sleep(5)  # Wait before next disconnect
            
            # Report
            print(f"\nRecovery test completed:")
            print(f"  Recovery times: {recovery_times}")
            print(f"  Average recovery: {sum(recovery_times)/len(recovery_times):.1f}s")
            
            assert len(recovery_times) == 3, "Should have recovered from all disconnects"
            
        finally:
            await manager.shutdown()
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.asyncio
    async def test_operations_after_reconnect(
        self,
        test_config: ConnectionConfig,
        skip_if_no_gateway,
        test_symbols: list
    ):
        """Test that operations work correctly after reconnection."""
        config = ConnectionConfig(
            host=test_config.host,
            port=test_config.port,
            client_id=93,
            reconnect_delay=2.0
        )
        
        client = IBKRAsyncClient(config)
        
        try:
            await client.connect()
            if not client.is_connected:
                pytest.skip("Failed to connect")
            
            # Fetch data before disconnect
            df_before = await client.get_historical_data(
                symbol=test_symbols[0],
                bar_size="1 day",
                duration="5 D"
            )
            
            assert not df_before.empty, "Should get data before disconnect"
            
            # Simulate disconnect
            client.conn_manager._on_ib_disconnected()
            
            # Wait for reconnection
            await asyncio.sleep(10)
            
            if not client.is_connected:
                pytest.skip("Did not reconnect")
            
            # Fetch data after reconnect
            df_after = await client.get_historical_data(
                symbol=test_symbols[0],
                bar_size="1 day",
                duration="5 D"
            )
            
            assert not df_after.empty, "Should get data after reconnect"
            
        finally:
            await client.disconnect()


# =============================================================================
# High Frequency Tests
# =============================================================================

class TestHighFrequency:
    """Tests for high-frequency request handling."""
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_rapid_data_requests(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test handling rapid data requests."""
        request_count = 30
        success_count = 0
        error_count = 0
        
        start_time = datetime.now()
        
        for i in range(request_count):
            try:
                df = await connected_client.get_historical_data(
                    symbol=test_symbols[i % len(test_symbols)],
                    bar_size="1 day",
                    duration="1 D"
                )
                
                if not df.empty:
                    success_count += 1
                else:
                    error_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"Request {i} error: {e}")
            
            # Minimal delay (throttling should handle rate limiting)
            await asyncio.sleep(0.1)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        print(f"\nRapid request test:")
        print(f"  Requests: {request_count}")
        print(f"  Successful: {success_count}")
        print(f"  Errors: {error_count}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Rate: {request_count/elapsed:.1f} req/s")
        
        # Most should succeed
        success_rate = success_count / request_count
        assert success_rate > 0.8, f"Success rate too low: {success_rate:.1%}"
    
    @pytest.mark.live
    @pytest.mark.reliability
    @pytest.mark.asyncio
    async def test_concurrent_requests(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test handling concurrent requests."""
        async def fetch_data(symbol: str):
            return await connected_client.get_historical_data(
                symbol=symbol,
                bar_size="1 day",
                duration="5 D"
            )
        
        # Make concurrent requests
        tasks = [
            fetch_data(symbol)
            for symbol in test_symbols[:3]
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check results
        success_count = sum(
            1 for r in results
            if not isinstance(r, Exception) and not r.empty
        )
        
        print(f"\nConcurrent requests: {success_count}/{len(tasks)} successful")
        
        assert success_count > 0, "At least some concurrent requests should succeed"


# =============================================================================
# Status Monitoring Tests
# =============================================================================

class TestStatusMonitoring:
    """Tests for status and health monitoring."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_connection_status(self, connected_client: IBKRAsyncClient):
        """Test getting connection status."""
        status = connected_client.get_status()
        
        assert "connection" in status
        assert "subscriptions" in status
        assert "cached_contracts" in status
        
        conn_status = status["connection"]
        assert conn_status["is_connected"] is True
        assert "stats" in conn_status
        
        print(f"\nClient status:")
        print(f"  Connected: {conn_status['is_connected']}")
        print(f"  State: {conn_status['state']}")
        print(f"  Reconnects: {conn_status['stats']['total_reconnects']}")
        print(f"  Cached contracts: {status['cached_contracts']}")
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_heartbeat_tracking(self, connected_client: IBKRAsyncClient):
        """Test heartbeat tracking."""
        # Wait for a heartbeat
        await asyncio.sleep(15)
        
        status = connected_client.conn_manager.get_status()
        stats = status["stats"]
        
        assert stats["last_heartbeat"] is not None, "Should have heartbeat timestamp"
        assert stats["failed_heartbeats"] == 0, "Should have no failed heartbeats"
        
        print(f"\nHeartbeat status:")
        print(f"  Last heartbeat: {stats['last_heartbeat']}")
        print(f"  Failed heartbeats: {stats['failed_heartbeats']}")
