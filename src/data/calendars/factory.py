"""
Factory for creating earnings calendar instances.
Supports multiple data sources with easy switching via config.
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Type for earnings calendar sources
CalendarSource = Literal['nasdaq', 'alphavantage', 'alpha_vantage']


class EarningsCalendarFactory:
    """Factory for creating earnings calendar instances based on source."""

    @staticmethod
    def create(source: CalendarSource = 'alphavantage'):
        """
        Create earnings calendar instance based on source.

        Args:
            source: Data source ('nasdaq', 'alphavantage', or 'alpha_vantage')

        Returns:
            EarningsCalendar or AlphaVantageCalendar instance

        Raises:
            ValueError: If source is invalid or API key missing
        """
        # Normalize source name
        source = source.lower()
        if source == 'alpha_vantage':
            source = 'alphavantage'

        if source == 'nasdaq':
            from src.data.calendars.base import EarningsCalendar
            logger.info("Using Nasdaq earnings calendar (free, unlimited)")
            return EarningsCalendar()

        elif source == 'alphavantage':
            from src.data.calendars.alpha_vantage import AlphaVantageCalendar
            logger.info("Using Alpha Vantage earnings calendar (official NASDAQ vendor, 25 calls/day)")
            return AlphaVantageCalendar()

        else:
            raise ValueError(
                f"Invalid earnings calendar source: {source}. "
                f"Must be 'nasdaq' or 'alphavantage'"
            )

    @staticmethod
    def get_available_sources() -> list[str]:
        """Get list of available earnings calendar sources."""
        return ['nasdaq', 'alphavantage']

    @staticmethod
    def get_source_info(source: str) -> dict:
        """
        Get information about a specific earnings calendar source.

        Args:
            source: Calendar source name

        Returns:
            Dict with source information
        """
        source = source.lower()
        if source == 'alpha_vantage':
            source = 'alphavantage'

        info = {
            'nasdaq': {
                'name': 'Nasdaq Earnings Calendar',
                'provider': 'Nasdaq',
                'cost': 'Free',
                'rate_limit': 'Unlimited',
                'api_key_required': False,
                'data_quality': 'Good',
                'confirmed_dates': False,
                'earnings_time': True,  # Has pre/post market timing
                'market_cap': True,
                'pros': [
                    'Free and unlimited',
                    'Pre/post market timing included',
                    'Market cap data included',
                    'No API key required'
                ],
                'cons': [
                    'Dates may be estimated (not confirmed)',
                    'Less reliable than official vendors'
                ]
            },
            'alphavantage': {
                'name': 'Alpha Vantage Earnings Calendar',
                'provider': 'Alpha Vantage (Official NASDAQ Vendor)',
                'cost': 'Free tier available',
                'rate_limit': '25 calls/day (free), 500 calls/min (premium)',
                'api_key_required': True,
                'data_quality': 'Excellent (Official NASDAQ vendor)',
                'confirmed_dates': True,
                'earnings_time': False,  # No pre/post market timing
                'market_cap': False,
                'pros': [
                    'Official NASDAQ data vendor',
                    'Confirmed earnings dates',
                    'High accuracy and reliability',
                    'Free tier available (25 calls/day)',
                    'EPS estimates included',
                    'Cached to reduce API usage'
                ],
                'cons': [
                    'Requires API key (free)',
                    'Rate limited on free tier',
                    'No pre/post market timing',
                    'No market cap data'
                ]
            }
        }

        return info.get(source, {})


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    logger.info("")
    logger.info('='*70)
    logger.info('EARNINGS CALENDAR FACTORY - SOURCE COMPARISON')
    logger.info('='*70)
    logger.info("")

    sources = EarningsCalendarFactory.get_available_sources()
    logger.info(f"Available sources: {', '.join(sources)}\n")

    for source in sources:
        info = EarningsCalendarFactory.get_source_info(source)

        logger.info(f"\n{info['name']}")
        logger.info('-' * 70)
        logger.info(f"Provider: {info['provider']}")
        logger.info(f"Cost: {info['cost']}")
        logger.info(f"Rate Limit: {info['rate_limit']}")
        logger.info(f"API Key Required: {info['api_key_required']}")
        logger.info(f"Data Quality: {info['data_quality']}")
        logger.info(f"Confirmed Dates: {info['confirmed_dates']}")

        logger.info("\nPros:")
        for pro in info['pros']:
            logger.info(f"  ✓ {pro}")

        logger.info("\nCons:")
        for con in info['cons']:
            logger.info(f"  ✗ {con}")

        logger.info("")

    logger.info('='*70)
    logger.info("\nTesting calendar creation...")
    logger.info('='*70)

    for source in sources:
        try:
            logger.info(f"\nCreating {source} calendar...")
            calendar = EarningsCalendarFactory.create(source)
            logger.info(f"✓ Successfully created {source} calendar: {type(calendar).__name__}")
        except Exception as e:
            logger.error(f"✗ Failed to create {source} calendar: {e}")
