# cloud/tests/test_metrics.py
"""Tests for Grafana Cloud metrics module."""
import pytest
import time
import asyncio
from unittest.mock import patch, MagicMock


# Helper to create mock config
def mock_grafana_config(url="", user="", key=""):
    """Create a mock grafana config dict."""
    return {"url": url, "user": user, "key": key}


class TestIsEnabled:
    """Tests for _is_enabled() configuration check."""

    def test_enabled_when_all_configured(self):
        """Returns True when all Grafana vars are set."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="12345",
            key="glc_xxx"
        )):
            assert metrics._is_enabled() is True

    def test_disabled_when_url_missing(self):
        """Returns False when URL is missing."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="",
            user="12345",
            key="glc_xxx"
        )):
            assert metrics._is_enabled() is False

    def test_disabled_when_user_missing(self):
        """Returns False when user is missing."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="",
            key="glc_xxx"
        )):
            assert metrics._is_enabled() is False

    def test_disabled_when_key_missing(self):
        """Returns False when API key is missing."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="12345",
            key=""
        )):
            assert metrics._is_enabled() is False


class TestFormatTags:
    """Tests for _format_tags() tag formatting."""

    def test_empty_tags(self):
        """Returns empty list for None or empty dict."""
        from src.core.metrics import _format_tags
        assert _format_tags(None) == []
        assert _format_tags({}) == []

    def test_single_tag(self):
        """Formats single tag as key=value."""
        from src.core.metrics import _format_tags
        result = _format_tags({"endpoint": "analyze"})
        assert result == ["endpoint=analyze"]

    def test_multiple_tags(self):
        """Formats multiple tags."""
        from src.core.metrics import _format_tags
        result = _format_tags({"endpoint": "analyze", "status": "success"})
        assert "endpoint=analyze" in result
        assert "status=success" in result
        assert len(result) == 2

    def test_none_value_filtered(self):
        """Filters out tags with None values."""
        from src.core.metrics import _format_tags
        result = _format_tags({"endpoint": "analyze", "ticker": None})
        assert result == ["endpoint=analyze"]


class TestRecord:
    """Tests for record() metric recording."""

    def test_noop_when_disabled(self):
        """Does nothing when Grafana not configured."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config()):
            with patch.object(metrics._executor, "submit") as mock_submit:
                metrics.record("test.metric", 100.0)
                mock_submit.assert_not_called()

    def test_submits_to_executor_when_enabled(self):
        """Submits metric to thread pool when configured."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="12345",
            key="glc_xxx"
        )):
            with patch.object(metrics._executor, "submit") as mock_submit:
                metrics.record("test.metric", 42.0, {"tag": "value"})
                mock_submit.assert_called_once()
                # Verify metric structure
                call_args = mock_submit.call_args[0]
                assert call_args[0] == metrics._push_metric
                metric_list = call_args[1]
                assert len(metric_list) == 1
                metric = metric_list[0]
                assert metric["name"] == "test.metric"
                assert metric["value"] == 42.0
                assert "tags" in metric
                assert "tag=value" in metric["tags"]


class TestTimer:
    """Tests for timer() context manager."""

    def test_records_duration(self):
        """Records elapsed time in milliseconds."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            with metrics.timer("test.duration", {"op": "test"}):
                time.sleep(0.01)  # 10ms

            mock_record.assert_called_once()
            call_args = mock_record.call_args
            assert call_args[0][0] == "test.duration"
            duration = call_args[0][1]
            assert duration >= 10  # At least 10ms
            assert call_args[0][2] == {"op": "test"}


class TestTimedDecorator:
    """Tests for @timed decorator."""

    def test_sync_function(self):
        """Times synchronous function."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            @metrics.timed("sync.duration")
            def sync_func():
                time.sleep(0.01)
                return "result"

            result = sync_func()
            assert result == "result"
            mock_record.assert_called_once()
            assert mock_record.call_args[0][0] == "sync.duration"

    def test_async_function(self):
        """Times asynchronous function."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            @metrics.timed("async.duration")
            async def async_func():
                await asyncio.sleep(0.01)
                return "async_result"

            result = asyncio.run(async_func())
            assert result == "async_result"
            mock_record.assert_called_once()
            assert mock_record.call_args[0][0] == "async.duration"


class TestConvenienceHelpers:
    """Tests for count(), gauge(), and pre-defined helpers."""

    def test_count_defaults_to_one(self):
        """count() uses value=1 by default."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            metrics.count("test.count", {"key": "val"})
            mock_record.assert_called_once_with("test.count", 1, {"key": "val"})

    def test_count_with_custom_value(self):
        """count() accepts custom value."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            metrics.count("test.count", {"key": "val"}, value=5)
            mock_record.assert_called_once_with("test.count", 5, {"key": "val"})

    def test_gauge_calls_record(self):
        """gauge() passes through to record()."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            metrics.gauge("test.gauge", 99.5, {"tier": "GOOD"})
            mock_record.assert_called_once_with("test.gauge", 99.5, {"tier": "GOOD"})

    def test_request_success_records_both_metrics(self):
        """request_success() records duration and status count."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            with patch.object(metrics, "count") as mock_count:
                metrics.request_success("analyze", 150.0)
                mock_record.assert_called_once_with(
                    "ivcrush.request.duration", 150.0, {"endpoint": "analyze"}
                )
                mock_count.assert_called_once_with(
                    "ivcrush.request.status", {"endpoint": "analyze", "status": "success"}
                )

    def test_request_error_records_both_metrics(self):
        """request_error() records duration and error status."""
        from src.core import metrics

        with patch.object(metrics, "record") as mock_record:
            with patch.object(metrics, "count") as mock_count:
                metrics.request_error("whisper", 50.0, "timeout")
                mock_record.assert_called_once_with(
                    "ivcrush.request.duration", 50.0, {"endpoint": "whisper"}
                )
                mock_count.assert_called_once_with(
                    "ivcrush.request.status", {"endpoint": "whisper", "status": "timeout"}
                )

    def test_vrp_analyzed(self):
        """vrp_analyzed() records ratio gauge and tier count."""
        from src.core import metrics

        with patch.object(metrics, "gauge") as mock_gauge:
            with patch.object(metrics, "count") as mock_count:
                metrics.vrp_analyzed("AAPL", 5.5, "GOOD")
                mock_gauge.assert_called_once_with(
                    "ivcrush.vrp.ratio", 5.5, {"ticker": "AAPL"}
                )
                mock_count.assert_called_once_with(
                    "ivcrush.vrp.tier", {"tier": "GOOD"}
                )

    def test_api_call(self):
        """api_call() records call count and latency."""
        from src.core import metrics

        with patch.object(metrics, "count") as mock_count:
            with patch.object(metrics, "record") as mock_record:
                metrics.api_call("tradier", 75.0, success=True)
                mock_count.assert_called_once_with(
                    "ivcrush.api.calls", {"provider": "tradier", "status": "success"}
                )
                mock_record.assert_called_once_with(
                    "ivcrush.api.latency", 75.0, {"provider": "tradier"}
                )

    def test_budget_update(self):
        """budget_update() records calls and dollars remaining."""
        from src.core import metrics

        with patch.object(metrics, "gauge") as mock_gauge:
            metrics.budget_update(35, 4.25)
            assert mock_gauge.call_count == 2
            mock_gauge.assert_any_call("ivcrush.budget.calls_remaining", 35)
            mock_gauge.assert_any_call("ivcrush.budget.dollars_remaining", 4.25)


class TestPushMetric:
    """Tests for _push_metric() HTTP client."""

    def test_noop_when_disabled(self):
        """Does nothing when Grafana not configured."""
        from src.core import metrics
        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config()):
            with patch("httpx.Client") as mock_client:
                metrics._push_metric([{"name": "test", "value": 1}])
                mock_client.assert_not_called()

    def test_posts_json_with_basic_auth(self):
        """Posts JSON payload with Basic auth."""
        from src.core import metrics

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="12345",
            key="glc_xxx"
        )):
            with patch("httpx.Client") as mock_client_class:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.post.return_value = mock_response
                mock_client_class.return_value = mock_client

                test_metric = [{"name": "test.metric", "value": 42}]
                metrics._push_metric(test_metric)

                mock_client.post.assert_called_once()
                call_kwargs = mock_client.post.call_args[1]
                assert call_kwargs["auth"] == ("12345", "glc_xxx")
                assert call_kwargs["headers"]["Content-Type"] == "application/json"

    def test_logs_warning_on_http_error(self):
        """Logs warning when HTTP request fails."""
        from src.core import metrics
        from src.core.logging import log

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch.object(metrics, "_get_grafana_config", return_value=mock_grafana_config(
            url="https://graphite.example.com/metrics",
            user="12345",
            key="glc_xxx"
        )):
            with patch("httpx.Client") as mock_client_class:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.post.return_value = mock_response
                mock_client_class.return_value = mock_client

                with patch("src.core.metrics.log") as mock_log:
                    metrics._push_metric([{"name": "test", "value": 1}])
                    mock_log.assert_called()
                    # Check that warning was logged
                    log_calls = [c for c in mock_log.call_args_list if c[0][0] == "warn"]
                    assert len(log_calls) > 0
