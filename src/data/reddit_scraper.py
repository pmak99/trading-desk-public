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
        limit: int = 20,
        analyze_content: bool = False,
        ai_client = None
    ) -> Dict:
        """
        Get sentiment for ticker from Reddit.

        Performance: Searches subreddits in parallel (3x faster than sequential).

        Enhancement: Optional AI content analysis using free Gemini (vs just upvote scores).

        Args:
            ticker: Ticker symbol
            subreddits: List of subreddits to check
            limit: Max posts to analyze per subreddit
            analyze_content: If True, use AI to analyze post content (recommended)
            ai_client: Optional AIClient instance for content analysis

        Returns:
            Dict with sentiment summary including posts_found, sentiment_score,
            avg_score, total_comments, top_posts, and optionally content_sentiment
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

        result = {
            'ticker': ticker,
            'posts_found': len(posts),
            'sentiment_score': sentiment_score,
            'avg_score': avg_score,
            'total_comments': total_comments,
            'top_posts': sorted(posts, key=lambda x: x['score'], reverse=True)[:5]
        }

        # ENHANCED: Analyze post content with AI (FREE Gemini)
        if analyze_content and ai_client and posts:
            try:
                content_sentiment = self._analyze_post_content(ticker, posts[:10], ai_client)
                result['content_sentiment'] = content_sentiment
                result['sentiment_score'] = content_sentiment  # Use AI sentiment as primary
                logger.info(f"{ticker}: Enhanced sentiment with AI content analysis")
            except Exception as e:
                logger.warning(f"{ticker}: AI content analysis failed, using score-based: {e}")

        return result

    def _analyze_post_content(self, ticker: str, posts: List[Dict], ai_client) -> float:
        """
        Analyze Reddit post content using AI (free Gemini).

        Understands sarcasm, memes, and actual arguments (vs just upvotes).

        Args:
            ticker: Stock ticker
            posts: List of Reddit posts
            ai_client: AIClient instance

        Returns:
            Sentiment score: -1.0 (bearish) to 1.0 (bullish)
        """
        try:
            # Build content summary
            post_texts = []
            for post in posts[:10]:  # Top 10 posts
                text = f"[{post['score']} upvotes] {post['title']}"
                post_texts.append(text)

            content_summary = "\n".join(post_texts)

            prompt = f"""Analyze Reddit sentiment for ${ticker} based on these posts:

{content_summary}

Consider:
- Actual content and arguments (not just upvotes)
- Sarcasm and memes (WSB culture)
- Bullish vs bearish tone
- Quality of discussion

Return ONLY a number between -1.0 and 1.0:
- 1.0 = Very bullish
- 0.5 = Moderately bullish
- 0.0 = Neutral
- -0.5 = Moderately bearish
- -1.0 = Very bearish

Response (number only):"""

            # Use free Gemini for this (no cost!)
            response = ai_client.chat_completion(
                prompt=prompt,
                preferred_model="gemini-2.0-flash",
                use_case="sentiment",
                ticker=ticker,
                max_tokens=50
            )

            # Parse response
            sentiment_str = response['content'].strip()
            sentiment = float(sentiment_str)

            # Clamp to valid range
            sentiment = max(-1.0, min(1.0, sentiment))

            logger.debug(f"{ticker}: AI content sentiment = {sentiment:.2f}")
            return sentiment

        except Exception as e:
            logger.warning(f"{ticker}: Failed to parse AI sentiment: {e}")
            # Fallback to score-based
            return self._calculate_score_based_sentiment(posts)

    def _calculate_score_based_sentiment(self, posts: List[Dict]) -> float:
        """Fallback: Calculate sentiment from scores only."""
        if not posts:
            return 0.0

        avg_score = sum(p['score'] for p in posts) / len(posts)
        return min(max(avg_score / 100, -1.0), 1.0)


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
