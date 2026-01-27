import pytest
from src.formatters.telegram import format_ticker_line, format_digest
from src.formatters.cli import format_ticker_line_cli, format_digest_cli

def test_format_ticker_line():
    """Format single ticker for digest."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "tailwinds": "AI demand",
        "headwinds": "China risk",
        "strategy": "Bull Put 165/160",
        "credit": 2.10,
    }

    line = format_ticker_line(ticker_data, rank=1)

    assert "AVGO" in line
    assert "7.2x" in line
    assert "82" in line
    assert "BULLISH" in line
    assert "AI demand" in line or "✅" in line

def test_format_digest():
    """Format full morning digest."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160", "credit": 2.10
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "tailwinds": "Holiday", "headwinds": "Inventory",
            "strategy": "IC 380/420", "credit": 3.50
        },
    ]

    digest = format_digest("2025-12-12", tickers, budget_calls=12, budget_remaining=4.85)

    assert "Dec 12" in digest or "2025-12-12" in digest
    assert "AVGO" in digest
    assert "LULU" in digest
    assert "12/40" in digest or "12" in digest

def test_format_ticker_line_cli():
    """Format single ticker for CLI."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "tailwinds": "AI demand",
        "headwinds": "China risk",
        "strategy": "Bull Put 165/160",
    }

    line = format_ticker_line_cli(ticker_data, rank=1)

    assert "AVGO" in line
    assert "7.2x" in line
    # Should NOT have HTML tags
    assert "<b>" not in line

def test_format_digest_cli():
    """Format full digest for CLI with ASCII borders."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160"
        },
    ]

    digest = format_digest_cli("2025-12-12", tickers, 12, 4.85)

    # Should have ASCII borders
    assert "═" in digest or "─" in digest
    assert "AVGO" in digest


def test_format_digest_grouped_by_date():
    """Format Telegram digest grouped by earnings date with sub-headers."""
    tickers = [
        {
            "ticker": "SBUX", "earnings_date": "2026-01-27", "vrp_ratio": 3.73, "score": 62.1,
            "direction": "NEUTRAL", "tailwinds": "Revenue growth", "headwinds": "EPS decline",
            "strategy": "80.0P/85.0P - 105.0C/110.0C", "credit": 1.79
        },
        {
            "ticker": "MSFT", "earnings_date": "2026-01-28", "vrp_ratio": 2.63, "score": 68.5,
            "direction": "BULLISH", "tailwinds": "Cloud growth", "headwinds": "Valuation",
            "strategy": "Bull Put 400/395", "credit": 2.10
        },
        {
            "ticker": "TXN", "earnings_date": "2026-01-27", "vrp_ratio": 3.0, "score": 58.7,
            "direction": "NEUTRAL", "tailwinds": "Auto demand", "headwinds": "Inventory",
            "strategy": "IC 180/200", "credit": 1.50
        },
        {
            "ticker": "IBM", "earnings_date": "2026-01-28", "vrp_ratio": 3.81, "score": 65.5,
            "direction": "NEUTRAL", "tailwinds": "AI consulting", "headwinds": "Legacy drag",
            "strategy": "IC 220/240", "credit": 1.80
        },
    ]

    digest = format_digest("2026-01-27", tickers, budget_calls=15, budget_remaining=4.91)

    # Should use grouped header
    assert "Earnings Digest" in digest
    assert "4 qualified" in digest

    # Should have date sub-headers
    assert "Jan 27" in digest
    assert "Jan 28" in digest

    # Jan 27 should appear before Jan 28
    pos_27 = digest.index("Jan 27")
    pos_28 = digest.index("Jan 28")
    assert pos_27 < pos_28

    # Within Jan 27 group, SBUX (62.1) before TXN (58.7) — sorted by score desc
    pos_sbux = digest.index("SBUX")
    pos_txn = digest.index("TXN")
    assert pos_sbux < pos_txn

    # Within Jan 28 group, MSFT (68.5) before IBM (65.5)
    pos_msft = digest.index("MSFT")
    pos_ibm = digest.index("IBM")
    assert pos_msft < pos_ibm

    # Global sequential numbering: SBUX=1, TXN=2, MSFT=3, IBM=4
    lines = digest.split("\n")
    ticker_lines = [l for l in lines if "<b>" in l and "|" in l]
    assert ticker_lines[0].startswith("1.")
    assert ticker_lines[1].startswith("2.")
    assert ticker_lines[2].startswith("3.")
    assert ticker_lines[3].startswith("4.")

    # Budget line
    assert "15/40" in digest
    assert "$4.91" in digest


def test_format_digest_cli_grouped_by_date():
    """Format CLI digest grouped by earnings date with ASCII sub-headers."""
    tickers = [
        {
            "ticker": "SBUX", "earnings_date": "2026-01-27", "vrp_ratio": 3.73, "score": 62.1,
            "direction": "NEUTRAL", "tailwinds": "Revenue growth", "headwinds": "EPS decline",
            "strategy": "IC 80/110"
        },
        {
            "ticker": "MSFT", "earnings_date": "2026-01-28", "vrp_ratio": 2.63, "score": 68.5,
            "direction": "BULLISH", "tailwinds": "Cloud growth", "headwinds": "Valuation",
            "strategy": "Bull Put 400/395"
        },
        {
            "ticker": "TXN", "earnings_date": "2026-01-27", "vrp_ratio": 3.0, "score": 58.7,
            "direction": "NEUTRAL", "tailwinds": "Auto demand", "headwinds": "Inventory",
            "strategy": "IC 180/200"
        },
    ]

    digest = format_digest_cli("2026-01-27", tickers, budget_calls=15, budget_remaining=4.91)

    # Should use grouped header
    assert "EARNINGS DIGEST" in digest
    assert "3 qualified" in digest

    # Date sub-headers
    assert "Jan 27" in digest
    assert "Jan 28" in digest

    # Jan 27 before Jan 28
    pos_27 = digest.index("Jan 27")
    pos_28 = digest.index("Jan 28")
    assert pos_27 < pos_28

    # Within Jan 27, SBUX (62.1) before TXN (58.7)
    pos_sbux = digest.index("SBUX")
    pos_txn = digest.index("TXN")
    assert pos_sbux < pos_txn

    # No HTML tags
    assert "<b>" not in digest

    # ASCII borders
    assert "═" in digest


def test_format_digest_fallback_without_earnings_date():
    """Tickers without earnings_date fall back to single-date format."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160", "credit": 2.10
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "tailwinds": "Holiday", "headwinds": "Inventory",
            "strategy": "IC 380/420", "credit": 3.50
        },
    ]

    # Telegram fallback
    digest_tg = format_digest("2025-12-12", tickers, budget_calls=12, budget_remaining=4.85)
    assert "Dec 12 EARNINGS" in digest_tg
    assert "Earnings Digest" not in digest_tg

    # CLI fallback
    digest_cli = format_digest_cli("2025-12-12", tickers, 12, 4.85)
    assert "Dec 12 EARNINGS" in digest_cli
    assert "EARNINGS DIGEST" not in digest_cli
