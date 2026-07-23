"""
Backward-compatibility shim — split into:
  analysis_common.py  (shared helpers + constants)
  scan.py             (/api/scan route)
  analyze.py          (/api/analyze route)
  whisper.py          (/api/whisper route)
  council.py          (/api/council route)

This shim re-exports the names that external callers still import from here.
Remove this file once all callers have been updated.
"""
from src.api.routers.analysis_common import (  # noqa: F401
    _analyze_single_ticker,
    _scan_tickers_for_whisper,
    MAX_SCAN_TIME_SECONDS,
    MAX_CONCURRENT_ANALYSIS,
)

# router is intentionally absent — main.py now registers 4 separate routers.
# Accessing analysis.router will raise AttributeError immediately (fast failure).
