#!/usr/bin/env python3
"""
Basic Functionality Test
Test core imports and basic functionality without heavy dependencies.
"""

import sys
import os

def test_imports():
    """Test that all modules can be imported."""
    print("🧪 Testing Module Imports")

    try:
        # Test basic imports
        from src.utils.config import Config, load_config
        print("✅ Config module imported")

        from src.classifier.stock_classifier import StockType, StockClassifier
        print("✅ Classifier module imported")

        from src.signals.signal_engine import SignalType, SignalEngine
        print("✅ Signals module imported")

        from src.position.position_manager import PositionManager
        print("✅ Position management imported")

        from src.risk.risk_manager import RiskManager, RiskLevel
        print("✅ Risk management imported")

        from src.backtest.engine import BacktestEngine
        print("✅ Backtesting imported")

        return True

    except ImportError as e:
        print(f"❌ Import failed: {e}")
        print("   Install dependencies: pip install -r requirements.txt")
        return False

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


def test_config_loading():
    """Test configuration loading."""
    print("\n🧪 Testing Configuration Loading")

    try:
        from src.utils.config import load_config, load_symbols

        # Test config loading
        config = load_config()
        print("✅ Configuration loaded")
        print(f"   Trading mode: {config.trading.mode}")
        print(f"   Max positions: {config.risk.max_positions}")

        # Test symbols loading
        symbols = load_symbols()
        print("✅ Symbols configuration loaded")
        print(f"   Watchlist symbols: {len(symbols.get('watchlist', []))}")

        return True

    except Exception as e:
        print(f"❌ Config test failed: {e}")
        return False


def test_class_enums():
    """Test that enums and basic classes work."""
    print("\n🧪 Testing Core Classes & Enums")

    try:
        from src.classifier.stock_classifier import StockType
        from src.signals.signal_engine import SignalType
        from src.risk.risk_manager import RiskLevel
        from src.position.position_manager import PositionStatus

        # Test enums
        print(f"   Stock Types: {list(StockType)}")
        print(f"   Signal Types: {list(SignalType)}")
        print(f"   Risk Levels: {list(RiskLevel)}")
        print(f"   Position Status: {list(PositionStatus)}")

        # Test enum values
        assert StockType.TYPE_A_TREND.value == "A"
        assert SignalType.ENTRY_LONG.value == "entry_long"
        assert RiskLevel.NORMAL.value == "normal"

        print("✅ All enums working correctly")
        return True

    except Exception as e:
        print(f"❌ Enum test failed: {e}")
        return False


def check_environment():
    """Check development environment."""
    print("\n🧪 Environment Check")

    # Check Python version
    python_version = sys.version_info
    print(f"   Python: {python_version.major}.{python_version.minor}.{python_version.micro}")

    if python_version.major >= 3 and python_version.minor >= 8:
        print("✅ Python version OK")
    else:
        print("❌ Python 3.8+ required")
        return False

    # Check if in virtual environment
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if in_venv:
        print("✅ Running in virtual environment")
    else:
        print("⚠️  Not in virtual environment (consider using venv)")

    # Check directory structure
    required_dirs = ['src', 'config', 'tests', 'logs', 'data']
    missing_dirs = []

    for dir_name in required_dirs:
        if not os.path.exists(dir_name):
            missing_dirs.append(dir_name)

    if missing_dirs:
        print(f"❌ Missing directories: {missing_dirs}")
        return False
    else:
        print("✅ Directory structure OK")

    return True


def main():
    """Run basic tests."""
    print("🚀 SAIYAN Trading Agent - Basic Tests")
    print("="*50)

    # Test 1: Environment
    env_ok = check_environment()

    # Test 2: Imports
    import_ok = test_imports()

    # Test 3: Config
    config_ok = test_config_loading()

    # Test 4: Classes
    class_ok = test_class_enums()

    print("\n" + "="*50)
    print("📊 Test Results:")
    print(f"   Environment: {'✅ OK' if env_ok else '❌ FAIL'}")
    print(f"   Imports: {'✅ OK' if import_ok else '❌ FAIL'}")
    print(f"   Configuration: {'✅ OK' if config_ok else '❌ FAIL'}")
    print(f"   Core Classes: {'✅ OK' if class_ok else '❌ FAIL'}")

    all_ok = env_ok and import_ok and config_ok and class_ok
    print(f"\n🎯 Basic Setup: {'✅ READY' if all_ok else '❌ NEEDS FIXING'}")

    if all_ok:
        print("\n✅ Basic tests passed!")
        print("\n📋 Next Steps:")
        print("1. Install dependencies: pip install -r requirements.txt")
        print("2. Test data layer: python test_data_layer.py")
        print("3. Test indicators: python test_indicators.py")
        print("4. Run backtest: python backtest_runner.py --mode backtest --symbol AAPL")
    else:
        print("\n❌ Fix issues before proceeding:")
        if not env_ok:
            print("   - Check Python version and directory structure")
        if not import_ok:
            print("   - Install dependencies with: pip install -r requirements.txt")
        if not config_ok:
            print("   - Check config/settings.yaml and config/symbols.yaml")


if __name__ == "__main__":
    main()
