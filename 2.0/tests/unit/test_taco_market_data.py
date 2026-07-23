"""Unit tests for scripts/taco/market_data.py — frozen refs + entry signals."""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.market_data import (
    EntryRefs,
    compute_refs,
    default_event_date,
    drawdown_from_event,
    drawdown_from_level,
    drawdown_pct,
    runup_zscore,
    runup_zscore_from_level,
    up_to,
    vix_spike,
    vix_spike_from_level,
    with_live_override,
)


def series(start: date, closes):
    """Build a Series of consecutive days (weekends ignored — fine for tests)."""
    return [(start + timedelta(days=i), float(c)) for i, c in enumerate(closes)]


# Panic scenario: 25 flat sessions at 100, then 5-session crash to 90,
# entry on the last crash day. Event date = first crash session.
SPX_PANIC = series(date(2026, 6, 1), [100.0] * 25 + [98.0, 96.0, 93.0, 91.0, 90.0])
VIX_PANIC = series(date(2026, 6, 1), [15.0] * 25 + [18.0, 22.0, 28.0, 33.0, 35.0])
EVENT = date(2026, 6, 26)   # first crash session
ENTRY = date(2026, 6, 30)   # last session (SPX 90)


class TestRefs:
    def test_call_refs_frozen_values(self):
        refs = compute_refs("CALL", SPX_PANIC, VIX_PANIC, EVENT, ENTRY)
        assert refs.pre_event_high == 100.0     # 20d high as of event date
        assert refs.panic_low == 90.0           # lowest close event..entry
        assert refs.euphoria_high is None
        assert refs.pre_event_vix == 15.0       # median of 10 pre-event closes

    def test_put_refs(self):
        spx_rip = series(date(2026, 6, 1),
                         [100.0] * 25 + [103.0, 106.0, 108.0, 110.0, 111.0])
        refs = compute_refs("PUT", spx_rip, VIX_PANIC, EVENT, ENTRY)
        assert refs.euphoria_high == 111.0
        # 20d window as of event date includes the first rip session (103)
        assert refs.rip_base == pytest.approx((19 * 100.0 + 103.0) / 20)
        assert refs.panic_low is None

    def test_refs_are_immutable(self):
        refs = compute_refs("CALL", SPX_PANIC, VIX_PANIC, EVENT, ENTRY)
        with pytest.raises(Exception):
            refs.panic_low = 1.0

    def test_insufficient_history_raises(self):
        short = series(date(2026, 6, 20), [100.0] * 5)
        with pytest.raises(ValueError):
            compute_refs("CALL", short, VIX_PANIC, EVENT, ENTRY)


class TestSignals:
    def test_drawdown_pct(self):
        assert drawdown_pct(SPX_PANIC, ENTRY) == pytest.approx(10.0)

    def test_drawdown_zero_at_high(self):
        flat = series(date(2026, 6, 1), [100.0] * 30)
        assert drawdown_pct(flat, flat[-1][0]) == 0.0

    def test_vix_spike(self):
        # last 35 vs prior-10 mean ((15*6 + 18 + 22 + 28 + 33) / 10 = 19.1)
        assert vix_spike(VIX_PANIC, ENTRY) == pytest.approx(35.0 / 19.1, rel=1e-3)

    def test_runup_zscore_positive_on_rip(self):
        base = [100.0 + 0.05 * i for i in range(240)]        # slow drift
        rip = [base[-1] + 2.0 * i for i in range(1, 21)]      # violent 20d rip
        s = series(date(2025, 7, 1), base + rip)
        assert runup_zscore(s, s[-1][0]) > 2.0

    def test_default_event_date_is_last_session_at_the_high(self):
        # last 20 sessions ending 6/30 span 6/11..6/30; the 100.0 high's most
        # recent session is 6/25 (the top before the break)
        assert default_event_date(SPX_PANIC, ENTRY) == date(2026, 6, 25)

    def test_up_to_excludes_future(self):
        assert up_to(SPX_PANIC, date(2026, 6, 3))[-1][0] == date(2026, 6, 3)

    def test_drawdown_from_event_beats_window_in_long_slide(self):
        # 25 sessions at 100, then 22 declining to 78. The plain 20-session
        # window no longer contains the pre-crash high and understates depth;
        # the event-anchored version measures from the high as of event date.
        long_slide = series(date(2026, 6, 1),
                            [100.0] * 25 + [100.0 - i for i in range(1, 23)])
        as_of = long_slide[-1][0]
        event = date(2026, 6, 26)          # first decline session
        anchored = drawdown_from_event(long_slide, event, as_of)
        assert anchored == pytest.approx(22.0)
        assert drawdown_pct(long_slide, as_of) < anchored

    def test_drawdown_from_event_matches_plain_when_high_in_window(self):
        assert drawdown_from_event(SPX_PANIC, EVENT, ENTRY) == pytest.approx(
            drawdown_pct(SPX_PANIC, ENTRY))


class TestLiveLevelVariants:
    """The *_from_level functions must match the daily-close originals when
    fed the series' own last value — live quotes are a drop-in override,
    not a different calculation."""

    def test_drawdown_from_level_matches_drawdown_pct(self):
        last = SPX_PANIC[-1][1]
        assert drawdown_from_level(SPX_PANIC, ENTRY, last) == pytest.approx(
            drawdown_pct(SPX_PANIC, ENTRY))

    def test_drawdown_from_level_reflects_lower_live_price(self):
        # live quote below the last close deepens the drawdown
        last = SPX_PANIC[-1][1]
        deeper = drawdown_from_level(SPX_PANIC, ENTRY, last - 5)
        assert deeper > drawdown_pct(SPX_PANIC, ENTRY)

    def test_vix_spike_from_level_matches_vix_spike(self):
        last = VIX_PANIC[-1][1]
        assert vix_spike_from_level(VIX_PANIC, ENTRY, last) == pytest.approx(
            vix_spike(VIX_PANIC, ENTRY))

    def test_runup_zscore_from_level_matches_runup_zscore(self):
        long_series = series(date(2024, 1, 1),
                             [100.0 + i * 0.1 for i in range(80)])
        as_of = long_series[-1][0]
        last = long_series[-1][1]
        assert runup_zscore_from_level(long_series, as_of, last) == pytest.approx(
            runup_zscore(long_series, as_of))


class TestWithLiveOverride:
    def test_none_is_noop(self):
        s = SPX_PANIC
        assert with_live_override(s, ENTRY, None) == s

    def test_replaces_last_when_date_matches(self):
        s = SPX_PANIC
        out = with_live_override(s, ENTRY, 12345.0)
        assert out[-1] == (ENTRY, 12345.0)
        assert len(out) == len(s)
        assert out[:-1] == s[:-1]

    def test_appends_when_date_absent(self):
        s = SPX_PANIC
        future = ENTRY + timedelta(days=3)
        out = with_live_override(s, future, 12345.0)
        assert out[-1] == (future, 12345.0)
        assert len(out) == len(s) + 1
