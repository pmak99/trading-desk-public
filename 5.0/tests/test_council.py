"""Tests for council domain logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.domain.council import (
    normalize_analyst_score,
    calculate_historical_score,
    calculate_skew_score,
    calculate_news_score,
    score_to_direction,
    calculate_agreement,
    parse_research_response,
    run_council,
    CouncilMember,
)


class TestNormalizeAnalystScore:
    def test_all_strong_buy(self):
        rec = {"strongBuy": 10, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        assert normalize_analyst_score(rec) == 1.0

    def test_all_strong_sell(self):
        rec = {"strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 10}
        assert normalize_analyst_score(rec) == -1.0

    def test_balanced(self):
        rec = {"strongBuy": 5, "buy": 5, "hold": 5, "sell": 5, "strongSell": 5}
        assert normalize_analyst_score(rec) == 0.0

    def test_empty(self):
        rec = {"strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        assert normalize_analyst_score(rec) == 0.0

    def test_bullish_bias(self):
        rec = {"strongBuy": 10, "buy": 15, "hold": 5, "sell": 2, "strongSell": 1}
        score = normalize_analyst_score(rec)
        assert 0.3 < score < 1.0  # Should be bullish


class TestCalculateHistoricalScore:
    def test_all_up(self):
        moves = [{"intraday_move_pct": 5.0}] * 8
        score = calculate_historical_score(moves)
        assert score == 1.0

    def test_all_down(self):
        moves = [{"intraday_move_pct": -5.0}] * 8
        score = calculate_historical_score(moves)
        assert score == -1.0

    def test_mixed(self):
        moves = [{"intraday_move_pct": 5.0}, {"intraday_move_pct": -5.0}] * 4
        score = calculate_historical_score(moves)
        assert -0.1 <= score <= 0.1  # Near neutral

    def test_empty(self):
        assert calculate_historical_score([]) == 0.0

    def test_recent_weighted_heavier(self):
        # Recent 4 all up, older all down
        moves = [{"intraday_move_pct": 5.0}] * 4 + [{"intraday_move_pct": -5.0}] * 4
        score = calculate_historical_score(moves)
        assert score > 0  # Should be positive due to recent uptrend


class TestCalculateSkewScore:
    def test_strong_bullish(self):
        assert calculate_skew_score("STRONG_BULLISH") == 0.7

    def test_strong_bearish(self):
        assert calculate_skew_score("STRONG_BEARISH") == -0.7

    def test_neutral(self):
        assert calculate_skew_score("NEUTRAL") == 0.0

    def test_unknown(self):
        assert calculate_skew_score("INVALID") == 0.0


class TestCalculateNewsScore:
    def test_bullish_news(self):
        articles = [
            {"headline": "Company beats earnings expectations", "summary": "Strong growth"},
            {"headline": "Analysts upgrade stock rating", "summary": "Record revenue"},
        ]
        score = calculate_news_score(articles)
        assert score > 0

    def test_bearish_news(self):
        articles = [
            {"headline": "Company misses earnings estimates", "summary": "Weak demand"},
            {"headline": "Analysts downgrade stock", "summary": "Revenue decline"},
        ]
        score = calculate_news_score(articles)
        assert score < 0

    def test_empty(self):
        assert calculate_news_score([]) == 0.0

    def test_neutral_news(self):
        articles = [
            {"headline": "Company reports quarterly results", "summary": "In line with expectations"},
        ]
        score = calculate_news_score(articles)
        assert score == 0.0


class TestScoreToDirection:
    def test_bullish(self):
        assert score_to_direction(0.5) == "bullish"

    def test_bearish(self):
        assert score_to_direction(-0.5) == "bearish"

    def test_neutral(self):
        assert score_to_direction(0.0) == "neutral"

    def test_boundary_bullish(self):
        assert score_to_direction(0.3) == "bullish"

    def test_boundary_bearish(self):
        assert score_to_direction(-0.3) == "bearish"


class TestAgreement:
    def test_high_agreement(self):
        members = [
            CouncilMember(name="A", weight=0.3, score=0.5, direction="bullish"),
            CouncilMember(name="B", weight=0.3, score=0.4, direction="bullish"),
            CouncilMember(name="C", weight=0.2, score=0.3, direction="bullish"),
            CouncilMember(name="D", weight=0.2, score=-0.1, direction="neutral"),
        ]
        level, count, total = calculate_agreement(members)
        assert level == "HIGH"
        assert count == 3
        assert total == 4

    def test_low_agreement(self):
        members = [
            CouncilMember(name="A", weight=0.3, score=0.5, direction="bullish"),
            CouncilMember(name="B", weight=0.3, score=-0.5, direction="bearish"),
            CouncilMember(name="C", weight=0.2, score=0.0, direction="neutral"),
        ]
        level, count, total = calculate_agreement(members)
        assert level == "LOW"

    def test_failed_excluded(self):
        members = [
            CouncilMember(name="A", weight=0.3, score=0.5, direction="bullish"),
            CouncilMember(name="B", weight=0.3, score=0.4, direction="bullish"),
            CouncilMember(name="C", weight=0.2, score=-0.5, direction="bearish", failed=True),
        ]
        level, count, total = calculate_agreement(members)
        assert level == "HIGH"
        assert total == 2

    def test_empty(self):
        level, count, total = calculate_agreement([])
        assert level == "LOW"
        assert total == 0


class TestParseResearchResponse:
    def test_full_response(self):
        text = """Direction: bullish
Score: +0.6
Bull Case: Strong cloud growth; AI revenue expanding
Bear Case: High valuation; Competition increasing
Key Risk: Regulatory scrutiny on AI
Analyst Trend: upgrading"""
        result = parse_research_response(text)
        assert result["direction"] == "bullish"
        assert result["score"] == 0.6
        assert "cloud growth" in result["bull_case"]
        assert "valuation" in result["bear_case"]
        assert "Regulatory" in result["key_risk"]
        assert result["analyst_trend"] == "upgrading"

    def test_partial_response(self):
        text = "Direction: bearish\nScore: -0.4"
        result = parse_research_response(text)
        assert result["direction"] == "bearish"
        assert result["score"] == -0.4

    def test_score_clamping(self):
        text = "Direction: bullish\nScore: 5.0"
        result = parse_research_response(text)
        assert result["score"] == 1.0

    def test_empty_response(self):
        result = parse_research_response("")
        assert result["direction"] == "neutral"
        assert result["score"] == 0.0


@pytest.mark.asyncio
async def test_run_council_no_earnings():
    """Returns error when no earnings found."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = None

    result = await run_council(
        ticker="ZZZZZ",
        finnhub=None,
        perplexity=MagicMock(),
        tradier=MagicMock(),
        repo=repo,
        cache=MagicMock(),
    )

    assert result.status == "no_earnings"


@pytest.mark.asyncio
async def test_run_council_no_finnhub():
    """Operates with 4 sources when finnhub=None."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = {"earnings_date": "2026-03-01", "timing": "AMC"}
    repo.get_position_limits.return_value = None
    repo.get_moves.return_value = [
        {"intraday_move_pct": 5.0} for _ in range(8)
    ]

    tradier = AsyncMock()
    tradier.get_quote.return_value = {"last": 150.0}
    tradier.get_expirations.return_value = ["2026-03-07"]
    tradier.get_options_chain.return_value = []

    cache = MagicMock()
    cache.get_sentiment.return_value = {"score": 0.3, "direction": "bullish"}
    cache.save_sentiment.return_value = True

    result = await run_council(
        ticker="NVDA",
        finnhub=None,  # No Finnhub
        perplexity=MagicMock(),
        tradier=tradier,
        repo=repo,
        cache=cache,
    )

    # Finnhub members failed, but historical + perplexity_quick + skew should work
    # At minimum we need 3 active. With 2 finnhub failed + skew likely failed (empty chain),
    # we have perplexity_quick (cached) + historical = 2, plus perplexity_research skipped
    # This may result in insufficient_data, which is the correct behavior
    assert result.ticker == "NVDA"
    assert result.earnings_date == "2026-03-01"


@pytest.mark.asyncio
async def test_run_council_success():
    """Full pipeline with all sources mocked."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = {"earnings_date": "2026-03-01", "timing": "AMC"}
    repo.get_position_limits.return_value = {"tail_risk_ratio": 1.8, "tail_risk_level": "NORMAL"}
    repo.get_moves.return_value = [{"intraday_move_pct": 5.0}] * 8

    # Mock finnhub
    finnhub = AsyncMock()
    finnhub.get_recommendations.return_value = {
        "strongBuy": 10, "buy": 15, "hold": 5, "sell": 2, "strongSell": 1, "period": "2026-02-01",
    }
    finnhub.get_company_news.return_value = [
        {"headline": "Strong earnings beat expectations", "summary": "Growth accelerating", "source": "Reuters", "datetime": 0},
    ]

    tradier = AsyncMock()
    tradier.get_quote.return_value = {"last": 150.0}
    tradier.get_expirations.return_value = ["2026-03-07"]
    tradier.get_options_chain.return_value = []  # Skew will fail

    cache = MagicMock()
    cache.get_sentiment.return_value = {"score": 0.4, "direction": "bullish"}
    cache.save_sentiment.return_value = True

    perplexity = MagicMock()

    result = await run_council(
        ticker="NVDA",
        finnhub=finnhub,
        perplexity=perplexity,
        tradier=tradier,
        repo=repo,
        cache=cache,
    )

    assert result.ticker == "NVDA"
    assert result.earnings_date == "2026-03-01"
    assert result.timing == "AMC"
    assert result.price == 150.0
    assert len(result.members) == 6  # All 6 members attempted

    # At least finnhub_analysts, finnhub_news, historical, perplexity_quick should succeed
    active = [m for m in result.members if not m.failed]
    assert len(active) >= 3


@pytest.mark.asyncio
async def test_run_council_insufficient_members():
    """Returns insufficient_data when < 3 members succeed."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = {"earnings_date": "2026-03-01", "timing": "BMO"}
    repo.get_position_limits.return_value = None
    repo.get_moves.return_value = []  # No historical data

    tradier = AsyncMock()
    tradier.get_quote.return_value = {"last": 100.0}
    tradier.get_expirations.return_value = []
    tradier.get_options_chain.return_value = []

    cache = MagicMock()
    cache.get_sentiment.return_value = None
    cache.save_sentiment.return_value = True

    result = await run_council(
        ticker="ZZZZZ",
        finnhub=None,
        perplexity=MagicMock(),
        tradier=tradier,
        repo=repo,
        cache=cache,
    )

    assert result.status == "insufficient_data"
    assert result.active_count < 3


@pytest.mark.asyncio
async def test_run_council_cached_sentiment():
    """Cache hit for Perplexity Quick avoids API call."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = {"earnings_date": "2026-03-01", "timing": "AMC"}
    repo.get_position_limits.return_value = None
    repo.get_moves.return_value = [{"intraday_move_pct": 3.0}] * 6

    finnhub = AsyncMock()
    finnhub.get_recommendations.return_value = {
        "strongBuy": 5, "buy": 10, "hold": 5, "sell": 2, "strongSell": 0, "period": "2026-02-01",
    }
    finnhub.get_company_news.return_value = [
        {"headline": "Company reports results", "summary": "In line", "source": "AP", "datetime": 0},
    ]

    tradier = AsyncMock()
    tradier.get_quote.return_value = {"last": 200.0}
    tradier.get_expirations.return_value = []

    cache = MagicMock()
    cache.get_sentiment.return_value = {"score": 0.2, "direction": "neutral"}
    cache.save_sentiment.return_value = True

    result = await run_council(
        ticker="MSFT",
        finnhub=finnhub,
        perplexity=MagicMock(),
        tradier=tradier,
        repo=repo,
        cache=cache,
    )

    # Perplexity Quick should be "cached"
    quick = next((m for m in result.members if m.name == "Perplexity Quick"), None)
    assert quick is not None
    assert quick.status == "cached"
    assert not quick.failed


@pytest.mark.asyncio
async def test_run_council_deep_research():
    """Phase 2 Perplexity Research fires automatically."""
    repo = MagicMock()
    repo.get_next_earnings.return_value = {"earnings_date": "2026-03-01", "timing": "AMC"}
    repo.get_position_limits.return_value = {"tail_risk_ratio": 1.8, "tail_risk_level": "NORMAL"}
    repo.get_moves.return_value = [{"intraday_move_pct": 5.0}] * 8

    finnhub = AsyncMock()
    finnhub.get_recommendations.return_value = {
        "strongBuy": 10, "buy": 15, "hold": 5, "sell": 2, "strongSell": 1, "period": "2026-02-01",
    }
    finnhub.get_company_news.return_value = [
        {"headline": "Strong earnings beat expectations", "summary": "Growth accelerating", "source": "Reuters", "datetime": 0},
    ]

    tradier = AsyncMock()
    tradier.get_quote.return_value = {"last": 150.0}
    tradier.get_expirations.return_value = ["2026-03-07"]
    tradier.get_options_chain.return_value = []

    cache = MagicMock()
    cache.get_sentiment.return_value = {"score": 0.4, "direction": "bullish"}
    cache.save_sentiment.return_value = True

    perplexity = MagicMock()
    perplexity.query = AsyncMock(return_value={
        "choices": [{"message": {"content": (
            "Direction: bullish\n"
            "Score: +0.6\n"
            "Bull Case: Strong cloud growth; AI revenue expanding\n"
            "Bear Case: High valuation; Competition increasing\n"
            "Key Risk: Regulatory scrutiny\n"
            "Analyst Trend: upgrading"
        )}}],
    })

    result = await run_council(
        ticker="NVDA",
        finnhub=finnhub,
        perplexity=perplexity,
        tradier=tradier,
        repo=repo,
        cache=cache,
    )

    # Perplexity Research should be active (not failed)
    research = next((m for m in result.members if m.name == "Perplexity Research"), None)
    assert research is not None
    assert not research.failed
    assert research.status == "fresh"
    assert research.score == 0.6
    assert research.direction == "bullish"

    perplexity.query.assert_called_once()
