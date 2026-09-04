#!/usr/bin/env python
"""Tests for DataQualityAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.data_quality import DataQualityAgent


class TestDataQualityAgent:
    """Tests for DataQualityAgent."""

    def test_report_mode_returns_issues(self):
        """Report mode should return issues without fixing."""
        agent = DataQualityAgent()
        result = agent.run(mode="report")

        assert 'fixable_issues' in result
        assert 'flagged_issues' in result
        assert 'summary' in result

    def test_dry_run_shows_what_would_fix(self):
        """Dry run should show fixes without applying."""
        agent = DataQualityAgent()
        result = agent.run(mode="dry-run")

        assert 'would_fix' in result or 'fixable_issues' in result
        assert result.get('changes_applied') is False

    def test_mode_validation(self):
        """Invalid mode should raise error."""
        agent = DataQualityAgent()

        with pytest.raises(ValueError, match="Invalid mode"):
            agent.run(mode="invalid")
