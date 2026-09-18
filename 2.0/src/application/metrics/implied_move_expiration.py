"""Which listed expiration the implied move, and so VRP, is priced at.

One rule for every path: the first listed expiration that contains the earnings
reaction. /analyze used to price at the trading expiration instead, the 3-10 DTE
Friday calculate_expiration_date picks for the spread, while scan, whisper and the
Telegram bot priced at the first expiration after the report. A later expiration
adds ordinary diffusion on top of the event: at an 8% event move and 50% vol a
Wednesday report prices 8.81% at that Friday and 11.21% at the next, a 27% higher
VRP, so one ticker could read SKIP on scan and GOOD on /analyze. The trading
expiration still carries strategy construction, skew, liquidity and term
structure on both paths; only the implied move moved.
"""

from datetime import date, timedelta

from src.domain.enums import EarningsTiming
from src.domain.errors import AppError, Result


def first_reaction_date(earnings_date: date, timing: EarningsTiming) -> date:
    """The earliest date an expiration can fall on and still contain the reaction.

    BMO reacts in the report date's own session, so that day's expiration holds
    it. AMC reacts the next session. DMH and UNKNOWN take the next day too: an
    expiration on the report date closes before an AMC report and would price no
    event at all, while one a session later contains the reaction either way. A
    stored BMO that is really AMC therefore prices a small implied move and reads
    SKIP, the safe direction; CLAUDE.md's "a stored BMO/AMC tag is not evidence"
    is why UNKNOWN is never promoted to BMO here.

    No trading-day arithmetic: listed expirations only fall on sessions, so the
    first one on or after a weekend or holiday is the first one after it.
    """
    if timing == EarningsTiming.BMO:
        return earnings_date
    return earnings_date + timedelta(days=1)


def implied_move_expiration(
    provider, ticker: str, earnings_date: date, timing: EarningsTiming
) -> Result[date, AppError]:
    """The listed expiration both analysis paths price the implied move at.

    `provider` is container.cached_options_provider, so the listing is one cached
    fetch (1 h) shared with the trading-expiration lookup.
    """
    return provider.find_nearest_expiration(ticker, first_reaction_date(earnings_date, timing))
