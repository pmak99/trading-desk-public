"""
Weekly options detection filter.

Filter tickers to only those with weekly options available.

Why Weekly Options Matter for IV Crush:
- Better liquidity (higher volume/OI) for trades
- More flexible expiration timing around earnings
- Tighter bid-ask spreads
- Standard monthly options only expire 3rd Friday of each month

Detection Logic (from archive/1.0):
- Get expirations from Tradier API
- Filter to future dates within 21 days
- Count Friday expirations (weekday == 4)
- If fridays >= 2 -> has weekly options
"""

from datetime import datetime, timedelta
from typing import List, Tuple

# Detection parameters
WEEKLY_DETECTION_WINDOW_DAYS = 21
WEEKLY_DETECTION_MIN_FRIDAYS = 2


def has_weekly_options(expirations: List[str], reference_date: str = None) -> Tuple[bool, str]:
    """
    Detect if a ticker has weekly options available.

    Uses the presence of multiple Friday expirations within a 21-day window
    as a proxy for weekly options availability.

    Args:
        expirations: List of expiration dates as strings (YYYY-MM-DD format)
        reference_date: Reference date for filtering (YYYY-MM-DD). Defaults to today.

    Returns:
        Tuple of (has_weeklies: bool, reason: str)
        - has_weeklies: True if ticker has weekly options
        - reason: Human-readable explanation of the determination

    Examples:
        >>> has_weekly_options(["2026-01-24", "2026-01-31", "2026-02-07"], "2026-01-21")
        (True, "3 Friday expirations in next 21 days")

        >>> has_weekly_options(["2026-02-21"], "2026-01-21")
        (False, "Only 1 Friday expiration in next 21 days (need 2+)")
    """
    if not expirations:
        return False, "No expirations available"

    # Parse reference date
    if reference_date:
        try:
            ref_date = datetime.strptime(reference_date, "%Y-%m-%d").date()
        except ValueError:
            ref_date = datetime.now().date()
    else:
        ref_date = datetime.now().date()

    # Calculate window end date
    window_end = ref_date + timedelta(days=WEEKLY_DETECTION_WINDOW_DAYS)

    # Count Friday expirations within the window
    friday_count = 0
    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()

            # Skip past expirations
            if exp_date < ref_date:
                continue

            # Skip expirations beyond window
            if exp_date > window_end:
                continue

            # Check if Friday (weekday == 4)
            if exp_date.weekday() == 4:
                friday_count += 1

        except ValueError:
            # Skip invalid date formats
            continue

    # Determine weekly availability
    if friday_count >= WEEKLY_DETECTION_MIN_FRIDAYS:
        return True, f"{friday_count} Friday expirations in next {WEEKLY_DETECTION_WINDOW_DAYS} days"
    else:
        return False, f"Only {friday_count} Friday expiration(s) in next {WEEKLY_DETECTION_WINDOW_DAYS} days (need {WEEKLY_DETECTION_MIN_FRIDAYS}+)"
