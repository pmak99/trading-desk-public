"""
Data Module - External data sources.

Contains data fetchers for earnings calendars, Reddit sentiment, etc.
"""

# Optional imports - only available if dependencies are installed
try:
    from .reddit_scraper import RedditScraper
    __all__ = ['RedditScraper']
except ImportError:
    # praw not installed - RedditScraper not available
    __all__ = []
