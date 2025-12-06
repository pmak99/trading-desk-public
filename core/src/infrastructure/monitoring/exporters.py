"""
Metrics exporters for different formats.

Supports exporting metrics to:
- JSON (for logging/debugging)
- Prometheus text format (for Prometheus scraping)
- HTTP endpoint for Prometheus scraping

Usage:
    # Start metrics HTTP server (for Prometheus scraping)
    from src.infrastructure.monitoring.exporters import MetricsHTTPServer
    server = MetricsHTTPServer(port=9090)
    server.start()

    # Export metrics to file
    exporter = PrometheusExporter()
    exporter.export_to_file(metrics, Path("metrics.prom"))
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import List, Callable, Optional
from datetime import datetime

from src.infrastructure.monitoring.metrics import Metric, MetricType, MetricsCollector

logger = logging.getLogger(__name__)


class JSONExporter:
    """
    Export metrics as JSON.

    Useful for logging, debugging, and simple monitoring dashboards.
    """

    def export_to_file(self, metrics: List[Metric], file_path: Path):
        """
        Export metrics to JSON file.

        Args:
            metrics: List of metrics to export
            file_path: Output file path

        Format:
            {
                "timestamp": "2024-11-23T10:00:00",
                "metrics": [
                    {
                        "name": "api.requests.total",
                        "value": 1234.0,
                        "type": "counter",
                        "labels": {"endpoint": "vrp"}
                    },
                    ...
                ]
            }
        """
        data = {
            'timestamp': datetime.now().isoformat(),
            'metrics': [
                {
                    'name': m.name,
                    'value': m.value,
                    'type': m.type.value,
                    'labels': m.labels,
                    'timestamp': m.timestamp.isoformat()
                }
                for m in metrics
            ]
        }

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Exported {len(metrics)} metrics to {file_path}")

    def export_to_string(self, metrics: List[Metric]) -> str:
        """
        Export metrics as JSON string.

        Args:
            metrics: List of metrics to export

        Returns:
            JSON string
        """
        data = {
            'timestamp': datetime.now().isoformat(),
            'metrics': [
                {
                    'name': m.name,
                    'value': m.value,
                    'type': m.type.value,
                    'labels': m.labels
                }
                for m in metrics
            ]
        }
        return json.dumps(data, indent=2)


class PrometheusExporter:
    """
    Export metrics in Prometheus text format.

    Prometheus text format specification:
    https://prometheus.io/docs/instrumenting/exposition_formats/

    Example output:
        # HELP api_requests_total Total API requests
        # TYPE api_requests_total counter
        api_requests_total{endpoint="vrp"} 1234

        # HELP connections_pool_active Active pool connections
        # TYPE connections_pool_active gauge
        connections_pool_active 15
    """

    def export_to_file(self, metrics: List[Metric], file_path: Path, descriptions: dict[str, str] | None = None):
        """
        Export metrics to Prometheus text format file.

        Args:
            metrics: List of metrics to export
            file_path: Output file path
            descriptions: Optional metric descriptions (name -> help text)
        """
        content = self.export_to_string(metrics, descriptions)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            f.write(content)

        logger.info(f"Exported {len(metrics)} metrics to {file_path} (Prometheus format)")

    def export_to_string(self, metrics: List[Metric], descriptions: dict[str, str] | None = None) -> str:
        """
        Export metrics as Prometheus text format string.

        Args:
            metrics: List of metrics to export
            descriptions: Optional metric descriptions

        Returns:
            Prometheus text format string
        """
        descriptions = descriptions or {}
        lines = []

        # Group metrics by name
        metrics_by_name = {}
        for metric in metrics:
            if metric.name not in metrics_by_name:
                metrics_by_name[metric.name] = []
            metrics_by_name[metric.name].append(metric)

        # Export each metric family
        for name, metric_list in sorted(metrics_by_name.items()):
            # Sanitize name for Prometheus (replace dots with underscores)
            prom_name = name.replace('.', '_')

            # Get metric type (all metrics in family should have same type)
            metric_type = metric_list[0].type

            # HELP line (optional)
            if name in descriptions:
                lines.append(f"# HELP {prom_name} {descriptions[name]}")

            # TYPE line
            prom_type = self._get_prometheus_type(metric_type)
            lines.append(f"# TYPE {prom_name} {prom_type}")

            # Metric lines
            for metric in metric_list:
                label_str = self._format_labels(metric.labels)
                lines.append(f"{prom_name}{label_str} {metric.value}")

            lines.append("")  # Blank line between metric families

        return "\n".join(lines)

    def _get_prometheus_type(self, metric_type: MetricType) -> str:
        """Map MetricType to Prometheus type."""
        mapping = {
            MetricType.COUNTER: "counter",
            MetricType.GAUGE: "gauge",
            MetricType.HISTOGRAM: "summary",  # Export histograms as summaries
            MetricType.TIMER: "summary"
        }
        return mapping.get(metric_type, "untyped")

    def _format_labels(self, labels: dict[str, str]) -> str:
        """Format labels for Prometheus."""
        if not labels:
            return ""

        label_pairs = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(label_pairs) + "}"


class MetricsHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Prometheus metrics endpoint."""

    # Class-level references (set by MetricsHTTPServer)
    collector: Optional[MetricsCollector] = None
    exporter: Optional[PrometheusExporter] = None
    descriptions: dict[str, str] = {}

    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/health":
            self._serve_health()
        else:
            self.send_error(404, "Not Found")

    def _serve_metrics(self):
        """Serve Prometheus metrics."""
        if self.collector is None or self.exporter is None:
            self.send_error(503, "Metrics not configured")
            return

        try:
            metrics = self.collector.get_all_metrics()
            content = self.exporter.export_to_string(metrics, self.descriptions)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        except Exception as e:
            logger.error(f"Error serving metrics: {e}")
            self.send_error(500, str(e))

    def _serve_health(self):
        """Serve health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "healthy"}')

    def log_message(self, format: str, *args):
        """Suppress default logging (use our logger instead)."""
        logger.debug(f"Metrics HTTP: {args[0]}")


class MetricsHTTPServer:
    """
    HTTP server for Prometheus metrics scraping.

    Starts a lightweight HTTP server that exposes metrics in Prometheus
    text format at /metrics endpoint.

    Endpoints:
    - GET /metrics: Prometheus metrics
    - GET /health: Health check

    Usage:
        collector = MetricsCollector()
        server = MetricsHTTPServer(
            collector=collector,
            port=9090,
            descriptions={
                "ivcrush_scans_total": "Total number of ticker scans",
                "ivcrush_vrp_ratio": "Current VRP ratio by ticker",
            }
        )
        server.start()

        # Later...
        server.stop()

    For Docker/Kubernetes:
        Add to your prometheus.yml scrape configs:
        - job_name: 'ivcrush'
          static_configs:
            - targets: ['localhost:9090']
    """

    def __init__(
        self,
        collector: MetricsCollector,
        port: int = 9090,
        host: str = "0.0.0.0",
        descriptions: Optional[dict[str, str]] = None,
    ):
        """
        Initialize metrics HTTP server.

        Args:
            collector: MetricsCollector to export
            port: Port to listen on (default: 9090)
            host: Host to bind to (default: 0.0.0.0)
            descriptions: Metric descriptions for HELP lines
        """
        self.collector = collector
        self.port = port
        self.host = host
        self.descriptions = descriptions or self._default_descriptions()
        self.exporter = PrometheusExporter()

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _default_descriptions(self) -> dict[str, str]:
        """Default metric descriptions for IV Crush system."""
        return {
            # Scan metrics
            "ivcrush_scans_total": "Total number of ticker scans performed",
            "ivcrush_scan_duration_ms": "Duration of ticker scans in milliseconds",
            "ivcrush_scan_errors_total": "Total number of scan errors",

            # VRP metrics
            "ivcrush_vrp_ratio": "VRP ratio by ticker",
            "ivcrush_implied_move_pct": "Implied move percentage by ticker",
            "ivcrush_historical_move_pct": "Historical mean move percentage by ticker",

            # Strategy metrics
            "ivcrush_strategies_generated_total": "Total strategies generated",
            "ivcrush_strategies_by_type": "Strategies generated by type",

            # API metrics
            "api_requests_total": "Total API requests",
            "api_request_duration_ms": "API request duration in milliseconds",
            "api_errors_total": "Total API errors",

            # Database metrics
            "db_queries_total": "Total database queries",
            "db_query_duration_ms": "Database query duration in milliseconds",
            "db_connections_active": "Active database connections",

            # Cache metrics
            "cache_hits_total": "Total cache hits",
            "cache_misses_total": "Total cache misses",
            "cache_size": "Current cache size",
        }

    def start(self):
        """Start the metrics HTTP server in a background thread."""
        if self._server is not None:
            logger.warning("Metrics server already running")
            return

        # Configure handler with collector and exporter
        MetricsHTTPHandler.collector = self.collector
        MetricsHTTPHandler.exporter = self.exporter
        MetricsHTTPHandler.descriptions = self.descriptions

        # Create and start server
        self._server = HTTPServer((self.host, self.port), MetricsHTTPHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        logger.info(f"Metrics HTTP server started on http://{self.host}:{self.port}/metrics")

    def stop(self):
        """Stop the metrics HTTP server."""
        if self._server is None:
            return

        self._server.shutdown()
        self._server = None
        self._thread = None

        logger.info("Metrics HTTP server stopped")

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server is not None


# IV Crush specific metric helpers
def record_scan_metrics(
    collector: MetricsCollector,
    ticker: str,
    vrp_ratio: float,
    implied_move: float,
    historical_move: float,
    duration_ms: float,
    success: bool = True,
):
    """
    Record metrics for a ticker scan.

    Args:
        collector: MetricsCollector instance
        ticker: Ticker symbol
        vrp_ratio: VRP ratio
        implied_move: Implied move percentage
        historical_move: Historical mean move percentage
        duration_ms: Scan duration in milliseconds
        success: Whether scan succeeded
    """
    labels = {"ticker": ticker}

    collector.increment("ivcrush_scans_total", labels=labels)
    collector.histogram("ivcrush_scan_duration_ms", duration_ms, labels=labels)

    if success:
        collector.gauge("ivcrush_vrp_ratio", vrp_ratio, labels=labels)
        collector.gauge("ivcrush_implied_move_pct", implied_move, labels=labels)
        collector.gauge("ivcrush_historical_move_pct", historical_move, labels=labels)
    else:
        collector.increment("ivcrush_scan_errors_total", labels=labels)


def record_strategy_metrics(
    collector: MetricsCollector,
    ticker: str,
    strategy_type: str,
    pop: float,
    score: float,
):
    """
    Record metrics for generated strategy.

    Args:
        collector: MetricsCollector instance
        ticker: Ticker symbol
        strategy_type: Strategy type (e.g., "iron_condor")
        pop: Probability of profit
        score: Strategy score
    """
    labels = {"ticker": ticker, "strategy_type": strategy_type}

    collector.increment("ivcrush_strategies_generated_total", labels=labels)
    collector.gauge("ivcrush_strategy_pop", pop, labels=labels)
    collector.gauge("ivcrush_strategy_score", score, labels=labels)
