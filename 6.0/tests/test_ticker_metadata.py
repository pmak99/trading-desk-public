#!/usr/bin/env python
"""Tests for ticker_metadata integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.integration.ticker_metadata import TickerMetadataRepository


class TestTickerMetadataRepository:
    """Tests for TickerMetadataRepository."""

    def test_get_returns_none_for_empty_table(self):
        """Should return None when ticker not in database."""
        repo = TickerMetadataRepository()
        result = repo.get_metadata("XXXXX")
        assert result is None

    def test_save_and_get(self):
        """Should save and retrieve metadata."""
        repo = TickerMetadataRepository()

        # Save test data
        repo.save_metadata(
            ticker="TEST123",
            company_name="Test Corp",
            sector="Technology",
            industry="Software",
            market_cap=1000.0
        )

        # Retrieve
        result = repo.get_metadata("TEST123")
        assert result is not None
        assert result['sector'] == "Technology"

        # Cleanup
        repo.delete_metadata("TEST123")

    def test_get_by_sector(self):
        """Should get all tickers in a sector."""
        repo = TickerMetadataRepository()

        # This may return empty if table is empty
        results = repo.get_by_sector("Technology")
        assert isinstance(results, list)
