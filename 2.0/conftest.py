"""Pytest configuration for 2.0.

Adds Trading Desk root to sys.path so common/ is importable.
"""

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
