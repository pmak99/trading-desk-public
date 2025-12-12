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
