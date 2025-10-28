"""
Reddit sentiment scraper for r/wallstreetbets and related forums.
"""

import praw
from typing import List, Dict
from datetime import datetime, timedelta
import os
import logging

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

    def get_ticker_sentiment(
        self,
        ticker: str,
        subreddits: List[str] = None,
        limit: int = 20
    ) -> Dict:
        """
        Get sentiment for ticker from Reddit.

        Args:
            ticker: Ticker symbol
            subreddits: List of subreddits to check
            limit: Max posts to analyze

        Returns:
            Dict with sentiment summary
        """
        subreddits = subreddits or ['wallstreetbets', 'stocks', 'options']

        posts = []
        for subreddit_name in subreddits:
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
                continue

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
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("NVDA")

    print(f"\nReddit sentiment for {result['ticker']}:")
    print(f"Posts found: {result['posts_found']}")
    print(f"Sentiment score: {result['sentiment_score']:.2f}")
    print(f"Avg score: {result['avg_score']:.1f}")
    print(f"Total comments: {result['total_comments']}")
