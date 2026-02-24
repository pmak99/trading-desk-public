import pytest
from src.formatters.telegram import format_ticker_line, format_digest, format_alert, format_council
from src.formatters.cli import format_ticker_line_cli, format_digest_cli, format_council_cli
from src.domain.council import CouncilMember, CouncilResult


def test_format_ticker_line():
    """Format single ticker for digest — compact one-line format."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "strategy_type": "Put",
        "credit": 2.10,
        "timing": "AMC",
    }

    line = format_ticker_line(ticker_data, rank=1)

    assert "AVGO" in line
    assert "7.2x" in line
    assert "82" in line
    assert "BULL" in line
    assert "Put $2.10" in line
    assert "AMC" in line
    # Should be a single line
    assert "\n" not in line


def test_format_ticker_line_with_timing():
    """BMO/AMC timing shows in output."""
    ticker_data = {
        "ticker": "MCO",
        "vrp_ratio": 1.8,
        "score": 47,
        "direction": "NEUTRAL",
        "strategy_type": "IC",
        "credit": 7.88,
        "timing": "BMO",
    }
    line = format_ticker_line(ticker_data, rank=5)
    assert "BMO" in line
    assert "5." in line


def test_format_ticker_line_trr_warning():
    """TRR HIGH flag shows warning emoji."""
    ticker_data = {
        "ticker": "TSLA",
        "vrp_ratio": 3.5,
        "score": 62,
        "direction": "NEUTRAL",
        "strategy_type": "IC",
        "credit": 5.00,
        "trr_high": True,
    }
    line = format_ticker_line(ticker_data, rank=1)
    assert "\u26a0\ufe0f" in line  # Warning emoji


def test_format_ticker_line_tradeable_marker():
    """Score >= 55 shows tradeable checkmark."""
    # Above threshold
    high = format_ticker_line({"ticker": "X", "vrp_ratio": 2, "score": 56, "direction": "NEUTRAL"}, 1)
    assert "\u2705" in high

    # Below threshold
    low = format_ticker_line({"ticker": "X", "vrp_ratio": 2, "score": 54, "direction": "NEUTRAL"}, 1)
    assert "\u2705" not in low


def test_format_ticker_line_no_tailwinds_headwinds():
    """New format has no tailwinds/headwinds lines."""
    ticker_data = {
        "ticker": "AVGO",
        "vrp_ratio": 7.2,
        "score": 82,
        "direction": "BULLISH",
        "strategy_type": "Put",
        "credit": 2.10,
    }
    line = format_ticker_line(ticker_data, rank=1)
    # Should NOT have old format artifacts
    assert "tailwinds" not in line.lower()
    assert "headwinds" not in line.lower()


def test_format_ticker_line_abbreviated_direction():
    """Direction is abbreviated to save space."""
    for direction, expected in [
        ("BULLISH", "BULL"),
        ("BEARISH", "BEAR"),
        ("NEUTRAL", "NEUT"),
        ("STRONG_BULLISH", "STR BULL"),
    ]:
        line = format_ticker_line({"ticker": "X", "vrp_ratio": 2, "score": 50, "direction": direction}, 1)
        assert expected in line


def test_format_digest():
    """Format full morning digest with new compact format."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "strategy_type": "Put", "credit": 2.10
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "strategy_type": "IC", "credit": 3.50
        },
    ]

    digest = format_digest("2025-12-12", tickers)

    assert "Dec 12" in digest or "2025-12-12" in digest
    assert "AVGO" in digest
    assert "LULU" in digest


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

    digest = format_digest_cli("2025-12-12", tickers)

    # Should have ASCII borders
    assert "=" in digest or "-" in digest
    assert "AVGO" in digest


def test_format_digest_grouped_by_date():
    """Format Telegram digest grouped by earnings date with sub-headers."""
    tickers = [
        {
            "ticker": "SBUX", "earnings_date": "2026-01-27", "vrp_ratio": 3.73, "score": 62.1,
            "direction": "NEUTRAL", "strategy_type": "IC", "credit": 1.79
        },
        {
            "ticker": "MSFT", "earnings_date": "2026-01-28", "vrp_ratio": 2.63, "score": 68.5,
            "direction": "BULLISH", "strategy_type": "Put", "credit": 2.10
        },
        {
            "ticker": "TXN", "earnings_date": "2026-01-27", "vrp_ratio": 3.0, "score": 58.7,
            "direction": "NEUTRAL", "strategy_type": "IC", "credit": 1.50
        },
        {
            "ticker": "IBM", "earnings_date": "2026-01-28", "vrp_ratio": 3.81, "score": 65.5,
            "direction": "NEUTRAL", "strategy_type": "IC", "credit": 1.80
        },
    ]

    digest = format_digest("2026-01-27", tickers)

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
    ticker_lines = [l for l in lines if "<b>" in l and "x |" in l]
    assert ticker_lines[0].startswith("1.")
    assert ticker_lines[1].startswith("2.")
    assert ticker_lines[2].startswith("3.")
    assert ticker_lines[3].startswith("4.")


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

    digest = format_digest_cli("2026-01-27", tickers)

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
    assert "\u2550" in digest


def test_format_digest_fallback_without_earnings_date():
    """Tickers without earnings_date fall back to single-date format."""
    tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "strategy_type": "Put", "credit": 2.10
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "strategy_type": "IC", "credit": 3.50
        },
    ]

    # Telegram fallback
    digest_tg = format_digest("2025-12-12", tickers)
    assert "Dec 12 EARNINGS" in digest_tg
    assert "Earnings Digest" not in digest_tg

    # CLI fallback
    cli_tickers = [
        {
            "ticker": "AVGO", "vrp_ratio": 7.2, "score": 82,
            "direction": "BULLISH", "tailwinds": "AI demand", "headwinds": "China risk",
            "strategy": "Bull Put 165/160"
        },
        {
            "ticker": "LULU", "vrp_ratio": 4.8, "score": 71,
            "direction": "NEUTRAL", "tailwinds": "Holiday", "headwinds": "Inventory",
            "strategy": "IC 380/420"
        },
    ]
    digest_cli = format_digest_cli("2025-12-12", cli_tickers)
    assert "Dec 12 EARNINGS" in digest_cli
    assert "EARNINGS DIGEST" not in digest_cli


def test_format_alert_full():
    """Format /analyze result with all data populated."""
    alert_data = {
        "ticker": "PANW",
        "price": 163.50,
        "earnings_date": "2026-02-17",
        "timing": "AMC",
        "vrp_ratio": 2.01,
        "vrp_tier": "EXCELLENT",
        "score": 48,
        "direction": "BEARISH",
        "sentiment_score": -0.3,
        "tailwinds": "Revenue guidance beats consensus",
        "headwinds": "EPS guidance misses consensus",
        "strategy": "Bear Call Spread",
        "strategy_desc": "Sell 180C / Buy 190C",
        "credit": 2.27,
        "max_risk": 7.73,
        "pop": 68,
        "liquidity_tier": "GOOD",
        "implied_move_pct": 7.9,
        "hist_mean_pct": 3.9,
        "hist_count": 12,
        "trr_ratio": 2.09,
        "trr_level": "NORMAL",
        "skew_bias": "neutral",
    }

    output = format_alert(alert_data)

    # Header has ticker, VRP tier, score, direction
    assert "PANW" in output
    assert "EXCELLENT" in output
    assert "2.0x" in output
    assert "48" in output
    assert "BEARISH" in output

    # Earnings date + timing + price
    assert "2026-02-17" in output
    assert "AMC" in output
    assert "$163.50" in output

    # Sentiment + skew
    assert "-0.3" in output
    assert "neutral" in output

    # Tailwinds/headwinds shown
    assert "Revenue guidance" in output
    assert "EPS guidance" in output

    # Strategy with strikes
    assert "Bear Call Spread" in output
    assert "Sell 180C / Buy 190C" in output
    assert "$2.27" in output
    assert "$7.73" in output
    assert "68%" in output

    # Historical context
    assert "12Q" in output
    assert "3.9%" in output
    assert "7.9%" in output
    assert "TRR 2.1x" in output
    assert "GOOD" in output


def test_format_alert_empty_tailwinds():
    """Empty tailwinds/headwinds should not show bare emoji."""
    alert_data = {
        "ticker": "TEST",
        "vrp_ratio": 1.5,
        "vrp_tier": "GOOD",
        "score": 50,
        "direction": "NEUTRAL",
        "tailwinds": "",
        "headwinds": "",
    }
    output = format_alert(alert_data)
    # Should NOT have lonely checkmark or warning emoji lines
    lines = output.split("\n")
    for line in lines:
        stripped = line.strip()
        assert stripped != "\u2705"      # No bare checkmark
        assert stripped != "\u26a0\ufe0f"  # No bare warning


def test_format_alert_trr_high_warning():
    """HIGH TRR shows warning emoji."""
    alert_data = {
        "ticker": "TSLA",
        "vrp_ratio": 3.5,
        "vrp_tier": "EXCELLENT",
        "score": 62,
        "direction": "NEUTRAL",
        "trr_ratio": 3.8,
        "trr_level": "HIGH",
    }
    output = format_alert(alert_data)
    assert "HIGH" in output
    assert "\u26a0\ufe0f" in output


def test_format_alert_minimal():
    """Minimal data produces clean output without crashes."""
    alert_data = {
        "ticker": "X",
        "vrp_ratio": 1.0,
        "score": 30,
        "direction": "NEUTRAL",
    }
    output = format_alert(alert_data)
    assert "X" in output
    assert "1.0x" in output


def test_format_council():
    """Format council result for Telegram."""
    result = CouncilResult(
        ticker="NVDA",
        earnings_date="2026-02-26",
        timing="AMC",
        price=131.28,
        members=[
            CouncilMember(name="Perplexity Research", weight=0.294, score=0.60, direction="bullish", status="fresh"),
            CouncilMember(name="Finnhub Analysts", weight=0.235, score=0.55, direction="bullish", status="33 analysts"),
            CouncilMember(name="Perplexity Quick", weight=0.118, score=0.15, direction="neutral", status="cached"),
            CouncilMember(name="Finnhub News", weight=0.118, score=0.30, direction="bullish", status="10 articles"),
            CouncilMember(name="Options Skew", weight=0.118, score=0.70, direction="bullish", status="STRONG_BULLISH"),
            CouncilMember(name="Historical Pattern", weight=0.118, score=0.34, direction="bullish", status="12Q"),
        ],
        consensus_score=0.42,
        consensus_direction="bullish",
        agreement="HIGH",
        agreement_count=5,
        active_count=6,
        modifier=0.05,
        base_score=62,
        final_score=67,
        direction="STRONG_BULLISH",
        skew_bias="STRONG_BULLISH",
        rule_applied="Rule 3: Skew confirms sentiment",
        tail_risk={"ratio": 3.78, "level": "HIGH", "max_move": 15.2},
        risk_flags=["TRR 3.78x HIGH \u2014 max 50 contracts"],
        status="success",
    )

    output = format_council(result)

    assert "COUNCIL: NVDA" in output
    assert "Feb 26" in output or "2026-02-26" in output
    assert "AMC" in output
    assert "$131.28" in output
    assert "Perplexity Research" in output
    assert "Finnhub Analysts" in output
    assert "HIGH" in output
    assert "5/6" in output
    assert len(output) <= 4096  # Telegram limit


def test_format_council_minimal():
    """Council with only 3 members still formats."""
    result = CouncilResult(
        ticker="AAPL",
        earnings_date="2026-03-01",
        timing="BMO",
        price=200.0,
        members=[
            CouncilMember(name="Perplexity Quick", weight=0.3, score=0.2, direction="neutral", status="cached"),
            CouncilMember(name="Historical Pattern", weight=0.3, score=0.1, direction="neutral", status="8Q"),
            CouncilMember(name="Options Skew", weight=0.3, score=-0.1, direction="neutral", status="NEUTRAL"),
        ],
        consensus_score=0.07,
        consensus_direction="neutral",
        agreement="HIGH",
        agreement_count=3,
        active_count=3,
        modifier=0,
        base_score=50,
        final_score=50,
        direction="NEUTRAL",
        skew_bias="NEUTRAL",
        rule_applied="Rule 3: Default",
        tail_risk={},
        risk_flags=[],
        status="success",
    )

    output = format_council(result)
    assert "AAPL" in output
    assert "3/3" in output


def test_format_council_error():
    """Council error status formats gracefully."""
    result = CouncilResult(
        ticker="ZZZZZ",
        earnings_date="",
        timing="",
        price=0,
        members=[],
        consensus_score=0,
        consensus_direction="neutral",
        agreement="LOW",
        agreement_count=0,
        active_count=0,
        modifier=0,
        base_score=0,
        final_score=0,
        direction="NEUTRAL",
        skew_bias="",
        rule_applied="",
        tail_risk={},
        risk_flags=[],
        status="no_earnings",
    )

    output = format_council(result)
    assert "ZZZZZ" in output
    assert "no_earnings" in output


def test_format_council_cli():
    """Format council result for CLI."""
    result = CouncilResult(
        ticker="NVDA",
        earnings_date="2026-02-26",
        timing="AMC",
        price=131.28,
        members=[
            CouncilMember(name="Perplexity Research", weight=0.294, score=0.60, direction="bullish", status="fresh"),
            CouncilMember(name="Finnhub Analysts", weight=0.235, score=0.55, direction="bullish", status="33 analysts"),
        ],
        consensus_score=0.42,
        consensus_direction="bullish",
        agreement="HIGH",
        agreement_count=2,
        active_count=2,
        modifier=0.05,
        base_score=62,
        final_score=67,
        direction="BULLISH",
        skew_bias="",
        rule_applied="Rule 1: Sentiment breaks tie",
        tail_risk={},
        risk_flags=[],
        status="success",
    )

    output = format_council_cli(result)

    assert "COUNCIL: NVDA" in output
    assert "BULL" in output
    assert "<b>" not in output  # No HTML
    assert "=" in output  # ASCII borders
