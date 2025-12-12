import pytest
from datetime import datetime
from src.core.config import now_et, today_et, is_half_day, Settings

def test_now_et_returns_eastern_time():
    """now_et() should return time in Eastern timezone."""
    result = now_et()
    assert result.tzinfo is not None
    assert "America/New_York" in str(result.tzinfo) or "EDT" in str(result) or "EST" in str(result)

def test_today_et_returns_date_string():
    """today_et() should return YYYY-MM-DD format."""
    result = today_et()
    assert len(result) == 10
    assert result[4] == '-' and result[7] == '-'

def test_is_half_day_christmas_eve():
    """Christmas Eve 2025 is a half day."""
    assert is_half_day("2025-12-24") is True

def test_is_half_day_normal_day():
    """Normal trading day is not a half day."""
    assert is_half_day("2025-12-12") is False

def test_settings_vrp_thresholds():
    """Settings should have correct VRP thresholds."""
    s = Settings()
    assert s.VRP_EXCELLENT == 7.0
    assert s.VRP_GOOD == 4.0
    assert s.VRP_MARGINAL == 1.5
    assert s.VRP_DISCOVERY == 3.0
