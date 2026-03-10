#!/usr/bin/env python3
"""
Thin CLI shim (tools/) — implementation lives in `src/`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.tools.pattern_backtest import main


if __name__ == "__main__":
    main()
