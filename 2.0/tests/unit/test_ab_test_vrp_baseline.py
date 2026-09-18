"""Pure helpers of scripts/ab_test_vrp_baseline.py.

No database: load_events is exercised by scripts/tests/test_research_events.py.
What matters here is that the backtest ranks through the live scorer rather than
a restated formula, joins the two baselines on the same events, and splits on a
date object -- the string comparison that crashed it after the Postgres cutover.
"""

from datetime import date

import pytest

import scripts.ab_test_vrp_baseline as ab  # noqa: E402
import scripts.scan.quality_scorer as quality_scorer  # noqa: E402


def _event(ticker, ed, vrp, straddle=6.0, actual=4.0):
    return {
        "ticker": ticker,
        "earnings_date": ed,
        "straddle": straddle,
        "actual": actual,
        "crushed": 1 if straddle > actual else 0,
        "edge": straddle - actual,
        "vrp": vrp,
    }


def test_imports_the_live_scorer_instead_of_restating_it():
    assert ab.calculate_scan_quality_score is quality_scorer.calculate_scan_quality_score


def test_live_lookback_is_twelve_quarters():
    assert ab.LOOKBACK_QUARTERS == 12


def test_live_score_matches_the_scorer_with_unknown_liquidity():
    expected = quality_scorer.calculate_scan_quality_score(
        {"vrp_ratio": 2.5, "implied_move_pct": "8.0%", "liquidity_tier": "WARNING"},
        vrp_target=4.0,
    )
    assert ab.live_score(2.5, 8.0) == expected


def test_live_score_gap_between_events_comes_from_vrp_and_straddle_only():
    """Liquidity is the same constant for every event, so at a fixed straddle
    the score gap is the VRP term's alone."""
    low, high = ab.live_score(1.5, 6.0), ab.live_score(3.0, 6.0)
    assert high - low == pytest.approx(1.5 / 4.0 * 55, abs=0.1)


def test_match_events_joins_on_ticker_and_date():
    d1, d2 = date(2025, 1, 15), date(2025, 4, 15)
    intraday = [_event("MU", d1, 3.0), _event("MU", d2, 2.0), _event("NVDA", d1, 1.8)]
    close = [_event("MU", d1, 1.5), _event("NVDA", d1, 1.0), _event("AAPL", d1, 0.9)]

    matched = ab.match_events(intraday, close)

    assert [(e["ticker"], e["earnings_date"]) for e in matched] == [("MU", d1), ("NVDA", d1)]
    assert matched[0]["vrp_intraday"] == 3.0
    assert matched[0]["vrp_close"] == 1.5
    assert matched[0]["crushed"] == 1
    assert matched[0]["edge"] == pytest.approx(2.0)


def test_match_events_refuses_an_outcome_that_differs_by_baseline():
    d = date(2025, 1, 15)
    with pytest.raises(ValueError, match="actual"):
        ab.match_events([_event("MU", d, 3.0, actual=4.0)], [_event("MU", d, 1.5, actual=5.0)])


def test_add_live_scores_scores_both_baselines():
    d = date(2025, 1, 15)
    events = ab.match_events([_event("MU", d, 4.0)], [_event("MU", d, 2.0)])
    ab.add_live_scores(events)
    assert events[0]["score_intraday"] == ab.live_score(4.0, 6.0)
    assert events[0]["score_close"] == ab.live_score(2.0, 6.0)


def test_top_k_takes_the_highest_ranked_share():
    events = [
        {"k": 5.0, "crushed": 1, "edge": 2.0},
        {"k": 4.0, "crushed": 0, "edge": -1.0},
        {"k": 3.0, "crushed": 1, "edge": 1.0},
        {"k": 1.0, "crushed": 0, "edge": -3.0},
    ]
    assert ab.top_k(events, "k", 50) == {"n": 2, "crush_pct": 50.0, "edge": 0.5}
    assert ab.top_k(events, "k", 20) == {"n": 1, "crush_pct": 100.0, "edge": 2.0}


def test_top_k_rejects_an_empty_set():
    with pytest.raises(ValueError):
        ab.top_k([], "k", 20)


def test_split_events_compares_dates_not_strings():
    events = [
        {"earnings_date": date(2025, 5, 31)},
        {"earnings_date": date(2025, 6, 1)},
        {"earnings_date": date(2026, 1, 2)},
    ]
    before, after = ab.split_events(events, date(2025, 6, 1))
    assert [e["earnings_date"] for e in before] == [date(2025, 5, 31)]
    assert [e["earnings_date"] for e in after] == [date(2025, 6, 1), date(2026, 1, 2)]


def test_median_vrp_scale():
    events = [
        {"vrp_intraday": 2.0, "vrp_close": 1.0},
        {"vrp_intraday": 3.0, "vrp_close": 1.0},
        {"vrp_intraday": 1.0, "vrp_close": 1.0},
    ]
    assert ab.median_vrp_scale(events) == pytest.approx(2.0)


def test_bootstrap_delta_is_zero_for_identical_rankings():
    events = [{"a": float(i), "b": float(i), "crushed": i % 2, "edge": 0.0} for i in range(50)]
    assert ab.bootstrap_top_k_delta(events, "a", "b", n_boot=200) == (0.0, 0.0)


def test_bootstrap_delta_is_deterministic_and_signed():
    """key_b ranks the crushed events first, so close-minus-intraday is positive."""
    events = [
        {"a": float(i), "b": float(-i), "crushed": 1 if i < 25 else 0, "edge": 0.0}
        for i in range(100)
    ]
    first = ab.bootstrap_top_k_delta(events, "a", "b", n_boot=300, seed=3)
    assert first == ab.bootstrap_top_k_delta(events, "a", "b", n_boot=300, seed=3)
    lo, hi = first
    assert 0 < lo <= hi
