#!/usr/bin/env python
"""Tests for SectorFetchAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import Mock, patch
from src.agents.sector_fetch import SectorFetchAgent


class TestSectorFetchAgent:
    """Tests for SectorFetchAgent."""

    def test_fetch_from_cache(self):
        """Should return cached data if available."""
        agent = SectorFetchAgent()

        # Mock the repository to return cached data
        with patch.object(agent.metadata_repo, 'get_metadata') as mock_get:
            mock_get.return_value = {
                'ticker': 'NVDA',
                'sector': 'Technology',
                'industry': 'Semiconductors'
            }

            result = agent.fetch("NVDA")

            assert result['sector'] == 'Technology'
            assert result.get('cached') is True

    def test_returns_none_when_not_found(self):
        """Should return None when ticker not found anywhere."""
        agent = SectorFetchAgent()

        with patch.object(agent.metadata_repo, 'get_metadata', return_value=None):
            with patch.object(agent, '_fetch_from_finnhub', return_value=None):
                result = agent.fetch("XXXXX")
                assert result is None
