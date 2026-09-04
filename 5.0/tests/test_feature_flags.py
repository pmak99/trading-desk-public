"""Tests for SCHEDULED_JOBS_ENABLED feature flag branching on /dispatch."""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

TEST_API_KEY = "test-api-key-for-unit-tests"


@pytest.fixture(autouse=True)
def set_test_api_key():
    original = os.environ.get("API_KEY")
    os.environ["API_KEY"] = TEST_API_KEY
    yield
    if original is not None:
        os.environ["API_KEY"] = original
    elif "API_KEY" in os.environ:
        del os.environ["API_KEY"]


@pytest.fixture
def client():
    from src.main import app
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"X-API-Key": TEST_API_KEY}


# ---------------------------------------------------------------------------
# Settings.scheduled_jobs_enabled — env var parsing
# ---------------------------------------------------------------------------

class TestScheduledJobsEnabledSetting:
    """Settings.scheduled_jobs_enabled reads from SCHEDULED_JOBS_ENABLED env var."""

    def _get_flag(self, env_val: str | None) -> bool:
        import importlib
        import src.core.config as mod

        env_patch = {k: v for k, v in os.environ.items()}
        if env_val is None:
            env_patch.pop("SCHEDULED_JOBS_ENABLED", None)
        else:
            env_patch["SCHEDULED_JOBS_ENABLED"] = env_val

        with patch.dict(os.environ, env_patch, clear=True):
            importlib.reload(mod)
            return mod.settings.scheduled_jobs_enabled

    def test_default_is_true(self):
        assert self._get_flag(None) is True

    def test_explicit_true(self):
        assert self._get_flag("true") is True
        assert self._get_flag("True") is True
        assert self._get_flag("TRUE") is True

    def test_explicit_false(self):
        assert self._get_flag("false") is False
        assert self._get_flag("False") is False
        assert self._get_flag("FALSE") is False

    def test_invalid_value_is_false(self):
        assert self._get_flag("yes") is False
        assert self._get_flag("1") is False


# ---------------------------------------------------------------------------
# /dispatch — kill switch behaviour
# ---------------------------------------------------------------------------

class TestDispatchKillSwitch:
    """/dispatch returns disabled immediately when SCHEDULED_JOBS_ENABLED=false."""

    def test_disabled_returns_disabled_status(self, client, auth_headers):
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = False
        mock_settings.gcs_bucket = ""

        with patch("src.api.routers.jobs.settings", mock_settings):
            response = client.post("/dispatch", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "disabled"
        assert "disabled" in data["message"].lower()

    def test_disabled_does_not_call_job_manager(self, client, auth_headers):
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = False
        mock_settings.gcs_bucket = ""

        with patch("src.api.routers.jobs.settings", mock_settings):
            with patch("src.api.routers.jobs.get_job_manager") as mock_mgr:
                client.post("/dispatch", headers=auth_headers)
                mock_mgr.assert_not_called()

    def test_disabled_does_not_call_job_runner(self, client, auth_headers):
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = False
        mock_settings.gcs_bucket = ""

        with patch("src.api.routers.jobs.settings", mock_settings):
            with patch("src.api.routers.jobs.get_job_runner") as mock_runner:
                client.post("/dispatch", headers=auth_headers)
                mock_runner.assert_not_called()

    def test_enabled_proceeds_to_job_lookup(self, client, auth_headers):
        """When enabled, dispatch proceeds past the flag check and attempts job lookup."""
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = True
        mock_settings.gcs_bucket = ""

        mock_manager = MagicMock()
        mock_manager.get_current_job.return_value = None  # no job scheduled right now

        with patch("src.api.routers.jobs.settings", mock_settings):
            with patch("src.api.routers.jobs.get_job_manager", return_value=mock_manager):
                response = client.post("/dispatch", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        # Should be "no_job" (no job scheduled), NOT "disabled"
        assert data["status"] != "disabled"
        mock_manager.get_current_job.assert_called_once()

    def test_disabled_requires_auth(self, client):
        """Even when disabled, /dispatch still requires API key auth."""
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = False

        with patch("src.api.routers.jobs.settings", mock_settings):
            response = client.post("/dispatch")  # no auth header

        assert response.status_code == 401

    def test_force_param_still_blocked_when_disabled(self, client, auth_headers):
        """force= parameter cannot bypass the kill switch."""
        mock_settings = MagicMock()
        mock_settings.scheduled_jobs_enabled = False
        mock_settings.gcs_bucket = ""

        with patch("src.api.routers.jobs.settings", mock_settings):
            response = client.post("/dispatch?force=morning-digest", headers=auth_headers)

        data = response.json()
        assert data["status"] == "disabled"
