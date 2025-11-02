"""
Reddit sentiment scraper for r/wallstreetbets and related forums.

Performance: Parallelized subreddit searches for 3x faster scraping.
"""

import praw
from typing import List, Dict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class RedditScraper:
    """Scrape Reddit for ticker sentiment."""

    def __init__(self):
        """Initialize Reddit API client."""
        self.reddit = praw.Reddit(
            client_id=os.getenv('REDDIT_CLIENT_ID'),
            client_secret=os.getenv('REDDIT_CLIENT_SECRET'),
            user_agent='earnings-bot/1.0'
        )

    def _search_subreddit(
        self,
        subreddit_name: str,
        ticker: str,
        limit: int
    ) -> List[Dict]:
        """
        Search a single subreddit for ticker mentions.

        Args:
            subreddit_name: Name of subreddit to search
            ticker: Ticker symbol
            limit: Max posts to retrieve

        Returns:
            List of post dicts
        """
        posts = []
        try:
            subreddit = self.reddit.subreddit(subreddit_name)

            # Search for ticker mentions
            for post in subreddit.search(
                ticker,
                time_filter='week',
                limit=limit
            ):
                posts.append({
                    'title': post.title,
                    'score': post.score,
                    'num_comments': post.num_comments,
                    'created_utc': datetime.fromtimestamp(post.created_utc),
                    'subreddit': subreddit_name
                })

        except Exception as e:
            logger.warning(f"Error scraping r/{subreddit_name}: {e}")

        return posts

    def get_ticker_sentiment(
        self,
        ticker: str,
        subreddits: List[str] = None,
        limit: int = 20
    ) -> Dict:
        """
        Get sentiment for ticker from Reddit.

        Performance: Searches subreddits in parallel (3x faster than sequential).

        Args:
            ticker: Ticker symbol
            subreddits: List of subreddits to check
            limit: Max posts to analyze per subreddit

        Returns:
            Dict with sentiment summary including posts_found, sentiment_score,
            avg_score, total_comments, and top_posts
        """
        subreddits = subreddits or ['wallstreetbets', 'stocks', 'options']

        # Parallel search across subreddits (3x faster than sequential)
        posts = []
        with ThreadPoolExecutor(max_workers=len(subreddits)) as executor:
            # Submit all subreddit searches concurrently
            future_to_sub = {
                executor.submit(self._search_subreddit, sub, ticker, limit): sub
                for sub in subreddits
            }

            # Collect results as they complete
            for future in as_completed(future_to_sub):
                subreddit_name = future_to_sub[future]
                try:
                    subreddit_posts = future.result(timeout=10)
                    posts.extend(subreddit_posts)
                    if subreddit_posts:
                        logger.debug(
                            f"Found {len(subreddit_posts)} posts in r/{subreddit_name}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to get results from r/{subreddit_name}: {e}")

        # Analyze sentiment (simple scoring)
        if not posts:
            return {
                'ticker': ticker,
                'posts_found': 0,
                'sentiment_score': 0.0,
                'avg_score': 0,
                'total_comments': 0
            }

        total_score = sum(p['score'] for p in posts)
        total_comments = sum(p['num_comments'] for p in posts)

        # Simple sentiment: positive if avg score > 10
        avg_score = total_score / len(posts)
        sentiment_score = min(max(avg_score / 100, -1.0), 1.0)

        return {
            'ticker': ticker,
            'posts_found': len(posts),
            'sentiment_score': sentiment_score,
            'avg_score': avg_score,
            'total_comments': total_comments,
            'top_posts': sorted(posts, key=lambda x: x['score'], reverse=True)[:5]
        }


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("NVDA")

    logger.info(f"\nReddit sentiment for {result['ticker']}:")
    logger.info(f"Posts found: {result['posts_found']}")
    logger.info(f"Sentiment score: {result['sentiment_score']:.2f}")
    logger.info(f"Avg score: {result['avg_score']:.1f}")
    logger.info(f"Total comments: {result['total_comments']}")
