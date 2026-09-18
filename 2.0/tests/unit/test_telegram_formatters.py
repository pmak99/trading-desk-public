"""Telegram rendering of 2.0 scan result dicts.

The formatter's job is to READ the engine's output, never to recompute it. That
is the whole point of the migration: 5.0's digest calculated its own simplified
VRP and score, so the message on the phone could rank tickers differently than
`/whisper` did. Anything here that derives a number instead of displaying one is
a bug.
"""

from src.application.formatters.telegram import format_digest, format_ticker_line

ROW = {
    "ticker": "AAPL",
    "earnings_date": "2026-10-29",
    "vrp_ratio": 1.85,
    # str(Percentage) — the engine emits these WITH the % already.
    "implied_move_pct": "5.0%",
    "historical_mean_pct": "2.7%",
    "recommendation": "EXCELLENT",
    "liquidity_tier": "EXCELLENT",
    "is_tradeable": True,
    "_quality_score": 82.4,
    "status": "SUCCESS",
}


def test_ticker_line_contains_ticker_vrp_and_tier():
    line = format_ticker_line(ROW, rank=1)
    assert "AAPL" in line
    assert "1.85" in line
    # Liquidity tier is title-cased for display; assert on content, not case.
    assert "excellent" in line.lower()


def test_digest_orders_by_quality_score_descending():
    low = {**ROW, "ticker": "LOWSCORE", "_quality_score": 10.0}
    high = {**ROW, "ticker": "HIGHSCORE", "_quality_score": 90.0}

    out = format_digest([low, high], title="Morning Digest")

    assert out.index("HIGHSCORE") < out.index("LOWSCORE")


def test_digest_orders_by_quality_score_not_vrp():
    """Quality score is the authoritative ranking; a higher VRP must not jump
    the queue on its own. This is exactly where 5.0 and 2.0 disagreed."""
    big_vrp = {**ROW, "ticker": "BIGVRP", "vrp_ratio": 9.0, "_quality_score": 20.0}
    best = {**ROW, "ticker": "BESTQS", "vrp_ratio": 1.2, "_quality_score": 95.0}

    out = format_digest([big_vrp, best], title="Morning Digest")

    assert out.index("BESTQS") < out.index("BIGVRP")


def test_digest_excludes_non_tradeable():
    skip = {**ROW, "ticker": "SKIPME", "is_tradeable": False, "recommendation": "SKIP"}

    out = format_digest([skip, ROW], title="Morning Digest")

    assert "SKIPME" not in out
    assert "AAPL" in out


def test_digest_honours_max_rows():
    # Zero-padded: unpadded "TICK2" is a substring of "TICK29" and the
    # membership check would count it as shown.
    rows = [{**ROW, "ticker": f"TICK{i:02d}", "_quality_score": float(i)}
            for i in range(30)]

    out = format_digest(rows, title="Morning Digest", max_rows=5)

    shown = [i for i in range(30) if f"TICK{i:02d}" in out]
    assert len(shown) == 5
    # Top 5 by score are 29..25
    assert set(shown) == {25, 26, 27, 28, 29}


def test_digest_empty_returns_explicit_no_candidates_message():
    out = format_digest([], title="Morning Digest")

    assert "no" in out.lower()
    assert "Morning Digest" in out


def test_digest_tolerates_missing_quality_score():
    """_quality_score only exists after _precalculate_quality_scores() runs, and
    only on tradeable rows. A missing key must sort, not crash."""
    no_score = {k: v for k, v in ROW.items() if k != "_quality_score"}

    out = format_digest([no_score], title="Morning Digest")

    assert "AAPL" in out


def test_digest_tolerates_explicit_null_quality_score():
    """`.get(k, 0.0)` would NOT fire on an explicit None — a recurring trap in
    this codebase. The formatter must use `.get(k) or 0.0`."""
    null_score = {**ROW, "_quality_score": None}

    out = format_digest([null_score], title="Morning Digest")

    assert "AAPL" in out


def test_digest_escapes_html_in_ticker():
    evil = {**ROW, "ticker": "<b>PWN</b>"}

    out = format_digest([evil], title="Morning Digest")

    assert "<b>PWN</b>" not in out
    assert "&lt;b&gt;PWN&lt;/b&gt;" in out


def test_digest_reports_counts_of_tradeable_and_analyzed():
    skip = {**ROW, "ticker": "SKIPME", "is_tradeable": False}

    out = format_digest([ROW, skip], title="Morning Digest")

    assert "1" in out and "2" in out


def test_ticker_line_does_not_double_the_percent_sign():
    """implied_move_pct/historical_mean_pct are str(Percentage) and already end
    in '%'. Appending another rendered "7.48%%" in the first live dry-run."""
    line = format_ticker_line(ROW, rank=1)
    assert "%%" not in line
    assert "5.0%" in line
    assert "2.7%" in line


def test_an_oi_only_liquidity_tier_keeps_its_emoji():
    """market_data marks a tier scored on open interest alone with a trailing *
    (e.g. "WARNING*"); the line must still carry the tier's emoji and the marker."""
    line = format_ticker_line({**ROW, "liquidity_tier": "WARNING*"}, 1)
    assert "⚠️ Warning*" in line
