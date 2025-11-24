"""
Metrics exporters for different formats.

Supports exporting metrics to:
- JSON (for logging/debugging)
- Prometheus text format (for Prometheus scraping)
"""

import json
import logging
from pathlib import Path
from typing import List
from datetime import datetime

from src.infrastructure.monitoring.metrics import Metric, MetricType

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
