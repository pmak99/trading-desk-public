#!/usr/bin/env python3
"""
Health Check Script for 3.0 System.

Validates system components and reports status.

Usage:
    python scripts/health_check.py [--json] [--verbose]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class HealthStatus(Enum):
    """Health check status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    status: HealthStatus
    message: str
    latency_ms: Optional[float] = None
    details: Optional[Dict] = None


@dataclass
class SystemHealth:
    """Overall system health."""
    status: HealthStatus
    timestamp: str
    components: List[ComponentHealth]
    summary: str


class HealthChecker:
    """Check health of system components."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.db_path = (
            Path(__file__).parent.parent.parent / "2.0" / "data" / "ivcrush.db"
        )
        self.models_dir = Path(__file__).parent.parent / "models" / "validated"

    def check_database(self) -> ComponentHealth:
        """Check database connectivity."""
        import time

        try:
            from src.utils.db import get_db_connection

            start = time.time()
            with get_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM historical_moves")
                count = cursor.fetchone()[0]
            latency = (time.time() - start) * 1000

            return ComponentHealth(
                name="database",
                status=HealthStatus.HEALTHY,
                message=f"Connected, {count} historical moves",
                latency_ms=round(latency, 2),
                details={"path": str(self.db_path), "record_count": count}
            )

        except Exception as e:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {e}",
                details={"path": str(self.db_path), "error": str(e)}
            )

    def check_model_files(self) -> ComponentHealth:
        """Check ML model files exist."""
        required_files = [
            "rf_magnitude_validated.pkl",
            "imputer_validated.pkl",
            "feature_columns.txt"
        ]

        missing = []
        for filename in required_files:
            filepath = self.models_dir / filename
            if not filepath.exists():
                missing.append(filename)

        if not missing:
            # Check model metadata
            metadata_path = self.models_dir / "model_metadata.json"
            metadata = None
            if metadata_path.exists():
                try:
                    with open(metadata_path) as f:
                        metadata = json.load(f)
                except:
                    pass

            return ComponentHealth(
                name="ml_model",
                status=HealthStatus.HEALTHY,
                message="All model files present",
                details={
                    "path": str(self.models_dir),
                    "metadata": metadata
                }
            )
        else:
            return ComponentHealth(
                name="ml_model",
                status=HealthStatus.UNHEALTHY,
                message=f"Missing files: {', '.join(missing)}",
                details={"path": str(self.models_dir), "missing": missing}
            )

    def check_tradier_api(self) -> ComponentHealth:
        """Check Tradier API connectivity."""
        import time

        api_key = os.getenv('TRADIER_API_KEY')
        if not api_key:
            return ComponentHealth(
                name="tradier_api",
                status=HealthStatus.UNHEALTHY,
                message="TRADIER_API_KEY not set"
            )

        try:
            import requests

            start = time.time()
            response = requests.get(
                "https://api.tradier.com/v1/markets/clock",
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Accept': 'application/json'
                },
                timeout=10
            )
            latency = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                clock = data.get('clock', {})
                return ComponentHealth(
                    name="tradier_api",
                    status=HealthStatus.HEALTHY,
                    message=f"Connected, market {'open' if clock.get('state') == 'open' else 'closed'}",
                    latency_ms=round(latency, 2),
                    details={"market_state": clock.get('state')}
                )
            else:
                return ComponentHealth(
                    name="tradier_api",
                    status=HealthStatus.UNHEALTHY,
                    message=f"API returned {response.status_code}",
                    latency_ms=round(latency, 2)
                )

        except Exception as e:
            return ComponentHealth(
                name="tradier_api",
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {e}"
            )

    def check_yahoo_finance(self) -> ComponentHealth:
        """Check Yahoo Finance connectivity."""
        import time

        try:
            import yfinance as yf

            start = time.time()
            stock = yf.Ticker("AAPL")
            price = stock.info.get('regularMarketPrice') or stock.info.get('currentPrice')
            latency = (time.time() - start) * 1000

            if price:
                return ComponentHealth(
                    name="yahoo_finance",
                    status=HealthStatus.HEALTHY,
                    message=f"Connected, AAPL=${price:.2f}",
                    latency_ms=round(latency, 2)
                )
            else:
                return ComponentHealth(
                    name="yahoo_finance",
                    status=HealthStatus.DEGRADED,
                    message="Connected but no price data",
                    latency_ms=round(latency, 2)
                )

        except Exception as e:
            return ComponentHealth(
                name="yahoo_finance",
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {e}"
            )

    def check_python_dependencies(self) -> ComponentHealth:
        """Check required Python packages."""
        required_packages = [
            ('numpy', 'np'),
            ('pandas', 'pd'),
            ('sklearn', None),
            ('joblib', None),
            ('aiohttp', None),
            ('yfinance', 'yf'),
        ]

        missing = []
        versions = {}

        for package, alias in required_packages:
            try:
                mod = __import__(package)
                versions[package] = getattr(mod, '__version__', 'unknown')
            except ImportError:
                missing.append(package)

        if not missing:
            return ComponentHealth(
                name="python_deps",
                status=HealthStatus.HEALTHY,
                message=f"All {len(required_packages)} packages available",
                details={"versions": versions}
            )
        else:
            return ComponentHealth(
                name="python_deps",
                status=HealthStatus.UNHEALTHY,
                message=f"Missing: {', '.join(missing)}",
                details={"missing": missing, "versions": versions}
            )

    def check_disk_space(self) -> ComponentHealth:
        """Check available disk space."""
        import shutil

        path = Path(__file__).parent.parent
        total, used, free = shutil.disk_usage(path)

        free_gb = free / (1024**3)
        used_pct = (used / total) * 100

        if free_gb < 1:
            status = HealthStatus.UNHEALTHY
            message = f"Low disk space: {free_gb:.1f}GB free"
        elif free_gb < 5:
            status = HealthStatus.DEGRADED
            message = f"Disk space warning: {free_gb:.1f}GB free"
        else:
            status = HealthStatus.HEALTHY
            message = f"{free_gb:.1f}GB free ({100-used_pct:.0f}%)"

        return ComponentHealth(
            name="disk_space",
            status=status,
            message=message,
            details={
                "free_gb": round(free_gb, 2),
                "used_pct": round(used_pct, 1)
            }
        )

    def run_all_checks(self) -> SystemHealth:
        """Run all health checks."""
        components = [
            self.check_python_dependencies(),
            self.check_database(),
            self.check_model_files(),
            self.check_tradier_api(),
            self.check_yahoo_finance(),
            self.check_disk_space(),
        ]

        # Determine overall status
        statuses = [c.status for c in components]
        if HealthStatus.UNHEALTHY in statuses:
            overall_status = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        healthy_count = sum(1 for s in statuses if s == HealthStatus.HEALTHY)
        summary = f"{healthy_count}/{len(components)} components healthy"

        return SystemHealth(
            status=overall_status,
            timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            components=components,
            summary=summary
        )


def print_health_text(health: SystemHealth) -> None:
    """Print health status in human-readable format."""
    status_symbols = {
        HealthStatus.HEALTHY: "\u2705",    # Green check
        HealthStatus.DEGRADED: "\u26A0\uFE0F",   # Warning
        HealthStatus.UNHEALTHY: "\u274C",  # Red X
    }

    print(f"\n{'='*60}")
    print(f"3.0 System Health Check")
    print(f"{'='*60}")
    print(f"Status: {status_symbols.get(health.status, '?')} {health.status.value.upper()}")
    print(f"Time: {health.timestamp}")
    print(f"Summary: {health.summary}")
    print(f"{'='*60}")

    for component in health.components:
        symbol = status_symbols.get(component.status, '?')
        latency = f" ({component.latency_ms}ms)" if component.latency_ms else ""
        print(f"{symbol} {component.name}: {component.message}{latency}")

    print(f"{'='*60}\n")


def print_health_json(health: SystemHealth) -> None:
    """Print health status as JSON."""
    data = {
        'status': health.status.value,
        'timestamp': health.timestamp,
        'summary': health.summary,
        'components': [
            {
                'name': c.name,
                'status': c.status.value,
                'message': c.message,
                'latency_ms': c.latency_ms,
                'details': c.details
            }
            for c in health.components
        ]
    }
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(description="3.0 System Health Check")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    checker = HealthChecker(verbose=args.verbose)
    health = checker.run_all_checks()

    if args.json:
        print_health_json(health)
    else:
        print_health_text(health)

    # Exit with appropriate code
    if health.status == HealthStatus.UNHEALTHY:
        sys.exit(2)
    elif health.status == HealthStatus.DEGRADED:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
