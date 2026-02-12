#!/usr/bin/env python3
"""
Health check script for IV Crush core system.

Verifies that all critical services are operational:
- Tradier API (market data)
- Database (SQLite)
- Cache (in-memory)

Usage:
    python scripts/health_check.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import Config
from src.container import Container
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


async def main():
    """Run health checks on all system components."""
    setup_logging("INFO")

    logger.info("Starting IV Crush core health check...")

    try:
        # Load configuration
        config = Config.from_env()

        # Create container
        container = Container(config)

        # Get health check service
        health_service = container.health_check_service

        # Run all health checks
        results = await health_service.check_all()

        # Display results
        print("\n📊 IV Crush core - System Health")
        print("=" * 60)

        all_healthy = all(h.healthy for h in results.values())

        for service in results.values():
            print(service)

        print("=" * 60)
        print(f"Status: {'✅ HEALTHY' if all_healthy else '❌ UNHEALTHY'}")
        print()

        return 0 if all_healthy else 1

    except KeyboardInterrupt:
        logger.info("\nHealth check interrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
