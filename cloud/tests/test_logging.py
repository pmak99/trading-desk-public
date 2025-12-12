import json
import pytest
from io import StringIO
from unittest.mock import patch
from src.core.logging import log, set_request_id, get_request_id

def test_set_and_get_request_id():
    """Request ID should be settable and retrievable."""
    set_request_id("abc123")
    assert get_request_id() == "abc123"

def test_log_outputs_json(capsys):
    """log() should output valid JSON to stdout."""
    set_request_id("test123")
    log("info", "Test message", ticker="NVDA", vrp=5.2)

    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())

    assert data["level"] == "INFO"
    assert data["message"] == "Test message"
    assert data["ticker"] == "NVDA"
    assert data["vrp"] == 5.2
    assert data["request_id"] == "test123"

def test_log_excludes_secrets(capsys):
    """log() should filter out secret-like keys."""
    log("info", "Test", api_key="secret123", token="hidden")

    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())

    assert "api_key" not in data
    assert "token" not in data
