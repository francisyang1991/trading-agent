"""
Stock universe configuration loader.

Loads config/stock_universe.yaml for scanner, picker, and backtest tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Default path relative to project root
DEFAULT_UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "config" / "stock_universe.yaml"


def load_stock_universe(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load stock universe from YAML config.

    Args:
        config_path: Path to stock_universe.yaml. Defaults to config/stock_universe.yaml.

    Returns:
        Dict with keys: all_symbols, themes, high_conviction, benchmark, etc.
    """
    path = config_path or DEFAULT_UNIVERSE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
