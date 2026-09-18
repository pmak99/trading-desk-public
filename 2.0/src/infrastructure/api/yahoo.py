"""
Yahoo Finance through yfinance: every request goes through `yahoo_call`.

yfinance is lazy. `yf.Ticker(symbol)` asks Yahoo nothing; `.history()`, `.info`,
`.calendar`, `.earnings_dates`, `.fast_info.last_price` and `yf.download()` do.
Wrap the request:

    hist = yahoo_call(lambda: yf.Ticker(symbol).history(period="3mo"))

It waits on the process's Yahoo gate (src/utils/rate_limiter.py: one call a
second) and a YFRateLimitError is retried there, behind a backoff every caller
shares. Build the Ticker inside the call where yfinance marks a property fetched
before it asks (`.info`), so a retry does not read the refused attempt's empty
result. What still fails reaches the call site's own `except`: the last
YFRateLimitError, or VendorRateLimited when the gate is paused longer than the
call may wait.

`yf.download()` catches its per-symbol errors itself (a failed symbol comes back
NaN), so a download is held to the limit but never retried.

Until 2026-09-14 yfinance calls had no limit and no backoff (audit 3.10).
"""

import sys
from typing import Callable, Optional, TypeVar

from src.utils.rate_limiter import YAHOO, vendor_gate

T = TypeVar("T")


def yahoo_call(fn: Callable[[], T]) -> T:
    """Run one yfinance request under the process's Yahoo limit and backoff."""
    return vendor_gate(YAHOO).call(fn, _retry_after)


def _retry_after(error: Exception) -> Optional[float]:
    """0 -- the gate's exponential backoff -- for yfinance's rate-limit error; None
    for anything else. Yahoo sends no usable Retry-After through yfinance."""
    # Looked up, not imported: this module must not load yfinance for callers
    # that import it lazily, and an error from yfinance means it is loaded.
    exceptions = sys.modules.get("yfinance.exceptions")
    limited = getattr(exceptions, "YFRateLimitError", None)
    if limited is not None and isinstance(error, limited):
        return 0.0
    return None
