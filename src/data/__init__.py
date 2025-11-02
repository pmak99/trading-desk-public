"""
Data Module - External data sources.

Contains data fetchers for earnings calendars, Reddit sentiment, etc.
"""

from .reddit_scraper import RedditScraper

__all__ = [
    'RedditScraper',
]
