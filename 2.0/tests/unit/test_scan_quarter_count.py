"""Scan, whisper and Telegram show how many quarters a VRP tier was graded on.

/analyze prints "[EXCELLENT · 12 quarters]" since main's 1bf0dc3, but the scan
and whisper tables and the Telegram line read analyze_ticker()'s dict, which had
no quarters key: a tier graded on 4 quarters and one graded on 12 looked the same.
"""

from pathlib import Path

from scripts.scan.formatters import format_recommendation  # noqa: E402
from src.application.formatters.telegram import format_ticker_line  # noqa: E402


def _row(**kw):
    row = {"ticker": "MU", "recommendation": "excellent", "vrp_ratio": 2.5,
           "implied_move_pct": "10.00%", "historical_mean_pct": "4.00%",
           "liquidity_tier": "GOOD", "earnings_date": "2026-09-30", "historical_quarters": 8}
    row.update(kw)
    return row


def test_recommendation_carries_the_quarter_count():
    assert format_recommendation(_row()) == "EXCELLENT 8Q"


def test_recommendation_without_a_count_is_the_bare_tier():
    assert format_recommendation(_row(historical_quarters=None)) == "EXCELLENT"
    row = _row()
    del row["historical_quarters"]
    assert format_recommendation(row) == "EXCELLENT"


def test_recommendation_fits_the_table_column():
    assert len(format_recommendation(_row(recommendation="marginal", historical_quarters=12))) <= 15


def test_telegram_line_shows_the_quarters_compactly():
    line = format_ticker_line(_row(), rank=1)
    assert "VRP 2.50x (8Q)" in line


def test_telegram_line_without_a_count_is_unchanged():
    assert "VRP 2.50x  ·" in format_ticker_line(_row(historical_quarters=None), rank=1)


def test_every_table_renders_the_tier_through_the_shared_formatter():
    root = Path(__file__).resolve().parents[2] / "scripts" / "scan"
    for rel in ("formatters.py", "workflows/scan_mode.py",
                "workflows/ticker_mode.py", "workflows/whisper_mode.py"):
        src = (root / rel).read_text()
        assert "r['recommendation'].upper()" not in src, rel
        assert "format_recommendation(r)" in src, rel
