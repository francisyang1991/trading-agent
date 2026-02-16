#!/usr/bin/env python3
"""
Thin CLI wrapper for cache health checks.

Core implementation lives in src.picker.cache_health.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.picker.cache_health import main


if __name__ == "__main__":
    main()
