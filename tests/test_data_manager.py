"""
Tests for src/data_manager.py

Tier 2 - Integration Tests (Uses test SQLite DB, no external API calls)

Tests:
- Database initialization
- Data storage and retrieval
- Metadata tracking
- Cache freshness logic
"""

import pytest
import sqlite3
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

# We need to patch the DB_PATH before importing the module
# to use a test-specific database
try:
    import src.data_manager as dm_module
except ImportError:
    # If yfinance or other deps are missing, skip these tests gracefully
    pytest.skip("data_manager dependencies not available", allow_module_level=True)


@pytest.fixture
def test_db(tmp_path):
    """Create a temporary test database."""
    db_path = str(tmp_path / "test_stock_cache.db")
    # Patch the module-level DB_PATH
    with patch.object(dm_module, "DB_PATH", db_path):
        dm_module.init_database()
        yield db_path


@pytest.fixture
def test_connection(test_db):
    """Get connection to the test database."""
    conn = sqlite3.connect(test_db)
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


# =============================================================================
# Database Initialization
# =============================================================================

class TestDatabaseInit:
    """Tests for database initialization."""

    @pytest.mark.integration
    def test_tables_created(self, test_db):
        """Init creates the expected tables."""
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "stock_daily" in tables
        assert "stock_fundamentals" in tables
        assert "data_metadata" in tables

    @pytest.mark.integration
    def test_idempotent_init(self, test_db):
        """Calling init_database twice doesn't fail."""
        with patch.object(dm_module, "DB_PATH", test_db):
            dm_module.init_database()  # Second call
            conn = sqlite3.connect(test_db)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            conn.close()
            assert "stock_daily" in tables


# =============================================================================
# Data Storage
# =============================================================================

class TestDataStorage:
    """Tests for data storage in the SQLite cache."""

    @pytest.mark.integration
    def test_store_and_retrieve_daily(self, test_connection, test_db):
        """Store OHLCV data and retrieve it."""
        # Insert test data
        test_connection.execute(
            "INSERT INTO stock_daily (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("AAPL", "2026-01-15", 150.0, 155.0, 148.0, 153.0, 5000000),
        )
        test_connection.commit()

        # Retrieve
        cursor = test_connection.execute(
            "SELECT * FROM stock_daily WHERE symbol = ?", ("AAPL",)
        )
        rows = cursor.fetchall()
        assert len(rows) == 1

    @pytest.mark.integration
    def test_store_multiple_symbols(self, test_connection, test_db):
        """Store data for multiple symbols."""
        symbols = ["AAPL", "MSFT", "NVDA"]
        for sym in symbols:
            test_connection.execute(
                "INSERT INTO stock_daily (symbol, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym, "2026-01-15", 100.0, 110.0, 95.0, 105.0, 1000000),
            )
        test_connection.commit()

        cursor = test_connection.execute(
            "SELECT DISTINCT symbol FROM stock_daily"
        )
        stored_symbols = {row[0] for row in cursor.fetchall()}
        assert stored_symbols == set(symbols)

    @pytest.mark.integration
    def test_fundamentals_storage(self, test_connection, test_db):
        """Store and retrieve fundamental data."""
        test_connection.execute(
            "INSERT INTO stock_fundamentals (symbol, sector, industry, market_cap, last_updated) "
            "VALUES (?, ?, ?, ?, ?)",
            ("NVDA", "Technology", "Semiconductors", 2000000000000, "2026-01-15"),
        )
        test_connection.commit()

        cursor = test_connection.execute(
            "SELECT sector, industry FROM stock_fundamentals WHERE symbol = ?",
            ("NVDA",),
        )
        row = cursor.fetchone()
        assert row[0] == "Technology"
        assert row[1] == "Semiconductors"


# =============================================================================
# Metadata Tracking
# =============================================================================

class TestMetadataTracking:
    """Tests for data freshness metadata."""

    @pytest.mark.integration
    def test_metadata_insert(self, test_connection, test_db):
        """Metadata tracks last update time."""
        now = datetime.now().isoformat()
        test_connection.execute(
            "INSERT INTO data_metadata (symbol, daily_last_date, daily_last_updated) "
            "VALUES (?, ?, ?)",
            ("AAPL", "2026-01-15", now),
        )
        test_connection.commit()

        cursor = test_connection.execute(
            "SELECT daily_last_updated FROM data_metadata WHERE symbol = ?",
            ("AAPL",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == now

    @pytest.mark.integration
    def test_metadata_update(self, test_connection, test_db):
        """Metadata can be updated for existing symbols."""
        test_connection.execute(
            "INSERT INTO data_metadata (symbol, daily_last_date, daily_last_updated) "
            "VALUES (?, ?, ?)",
            ("AAPL", "2026-01-01", "2026-01-01T00:00:00"),
        )
        test_connection.commit()

        test_connection.execute(
            "UPDATE data_metadata SET daily_last_date = ?, daily_last_updated = ? WHERE symbol = ?",
            ("2026-01-15", "2026-01-15T00:00:00", "AAPL"),
        )
        test_connection.commit()

        cursor = test_connection.execute(
            "SELECT daily_last_date FROM data_metadata WHERE symbol = ?",
            ("AAPL",),
        )
        row = cursor.fetchone()
        assert row[0] == "2026-01-15"
