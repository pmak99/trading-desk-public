"""
Tests for Reddit scraper with parallel search optimization.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from src.reddit_scraper import RedditScraper


class TestRedditScraperParallelSearch:
    """Test parallel subreddit search functionality."""

    @patch('src.reddit_scraper.praw.Reddit')
    def test_parallel_search_combines_results_from_all_subreddits(self, mock_reddit_class):
        """Test that parallel search aggregates posts from all subreddits."""
        # Setup mock
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        # Mock subreddit searches
        def create_mock_post(title, score, subreddit):
            post = Mock()
            post.title = title
            post.score = score
            post.num_comments = 10
            post.created_utc = datetime.now().timestamp()
            return post

        # Mock each subreddit to return different posts
        mock_wsb = Mock()
        mock_wsb.search.return_value = [
            create_mock_post("WSB Post 1", 100, "wallstreetbets"),
            create_mock_post("WSB Post 2", 200, "wallstreetbets"),
        ]

        mock_stocks = Mock()
        mock_stocks.search.return_value = [
            create_mock_post("Stocks Post 1", 50, "stocks"),
        ]

        mock_options = Mock()
        mock_options.search.return_value = [
            create_mock_post("Options Post 1", 75, "options"),
        ]

        def get_subreddit(name):
            if name == 'wallstreetbets':
                return mock_wsb
            elif name == 'stocks':
                return mock_stocks
            elif name == 'options':
                return mock_options

        mock_reddit.subreddit.side_effect = get_subreddit

        # Execute
        result = scraper.get_ticker_sentiment('NVDA')

        # Verify
        assert result['posts_found'] == 4  # 2 + 1 + 1
        assert result['ticker'] == 'NVDA'
        assert result['sentiment_score'] > 0  # Has positive sentiment
        assert result['total_comments'] == 40  # 4 posts * 10 comments

    @patch('src.reddit_scraper.praw.Reddit')
    def test_parallel_search_handles_subreddit_failures_gracefully(self, mock_reddit_class):
        """Test that failure in one subreddit doesn't break entire search."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        def create_mock_post(title, score):
            post = Mock()
            post.title = title
            post.score = score
            post.num_comments = 5
            post.created_utc = datetime.now().timestamp()
            return post

        # Mock one successful subreddit
        mock_wsb = Mock()
        mock_wsb.search.return_value = [
            create_mock_post("WSB Post", 100),
        ]

        # Mock one failing subreddit
        mock_stocks = Mock()
        mock_stocks.search.side_effect = Exception("API error")

        # Mock another successful subreddit
        mock_options = Mock()
        mock_options.search.return_value = [
            create_mock_post("Options Post", 50),
        ]

        def get_subreddit(name):
            if name == 'wallstreetbets':
                return mock_wsb
            elif name == 'stocks':
                return mock_stocks
            elif name == 'options':
                return mock_options

        mock_reddit.subreddit.side_effect = get_subreddit

        # Execute - should not raise exception despite one failure
        result = scraper.get_ticker_sentiment('AAPL')

        # Verify - got posts from 2 successful subreddits
        assert result['posts_found'] == 2
        assert result['ticker'] == 'AAPL'

    @patch('src.reddit_scraper.praw.Reddit')
    def test_parallel_search_returns_empty_when_no_posts_found(self, mock_reddit_class):
        """Test graceful handling when no posts found in any subreddit."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        # Mock all subreddits returning empty results
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = []
        mock_reddit.subreddit.return_value = mock_subreddit

        # Execute
        result = scraper.get_ticker_sentiment('RARE')

        # Verify
        assert result['posts_found'] == 0
        assert result['sentiment_score'] == 0.0
        assert result['avg_score'] == 0
        assert result['total_comments'] == 0
        assert result['ticker'] == 'RARE'

    @patch('src.reddit_scraper.praw.Reddit')
    def test_sentiment_calculation_accuracy(self, mock_reddit_class):
        """Test that sentiment score is calculated correctly from posts."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        def create_mock_post(score):
            post = Mock()
            post.title = "Test post"
            post.score = score
            post.num_comments = 10
            post.created_utc = datetime.now().timestamp()
            return post

        # Mock posts with known scores - only for one subreddit to control count
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post(1000),  # Very positive
            create_mock_post(500),
            create_mock_post(200),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # Execute with single subreddit
        result = scraper.get_ticker_sentiment('TSLA', subreddits=['wallstreetbets'])

        # Verify sentiment calculation
        # avg_score = (1000 + 500 + 200) / 3 = 566.67
        # sentiment_score = min(max(566.67 / 100, -1.0), 1.0) = 1.0 (capped)
        assert result['posts_found'] == 3
        assert result['avg_score'] == pytest.approx(566.67, rel=0.01)
        assert result['sentiment_score'] == 1.0  # Capped at 1.0

    @patch('src.reddit_scraper.praw.Reddit')
    def test_top_posts_sorted_by_score(self, mock_reddit_class):
        """Test that top_posts are sorted by score descending."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        def create_mock_post(title, score):
            post = Mock()
            post.title = title
            post.score = score
            post.num_comments = 10
            post.created_utc = datetime.now().timestamp()
            return post

        # Mock posts with different scores
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("Low score", 10),
            create_mock_post("Highest score", 1000),
            create_mock_post("Medium score", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # Execute with single subreddit to control count
        result = scraper.get_ticker_sentiment('META', subreddits=['wallstreetbets'])

        # Verify sorting
        top_posts = result['top_posts']
        assert len(top_posts) == 3
        assert top_posts[0]['title'] == "Highest score"
        assert top_posts[0]['score'] == 1000
        assert top_posts[1]['title'] == "Medium score"
        assert top_posts[1]['score'] == 100
        assert top_posts[2]['title'] == "Low score"
        assert top_posts[2]['score'] == 10

    def test_search_subreddit_private_method(self):
        """Test the _search_subreddit helper method directly."""
        with patch('src.reddit_scraper.praw.Reddit'):
            scraper = RedditScraper()
            mock_reddit = Mock()
            scraper.reddit = mock_reddit

            # Mock subreddit
            def create_mock_post(title, score):
                post = Mock()
                post.title = title
                post.score = score
                post.num_comments = 5
                post.created_utc = datetime.now().timestamp()
                return post

            mock_subreddit = Mock()
            mock_subreddit.search.return_value = [
                create_mock_post("Test 1", 100),
                create_mock_post("Test 2", 200),
            ]
            mock_reddit.subreddit.return_value = mock_subreddit

            # Execute
            posts = scraper._search_subreddit('wallstreetbets', 'AAPL', 20)

            # Verify
            assert len(posts) == 2
            assert posts[0]['title'] == "Test 1"
            assert posts[0]['score'] == 100
            assert posts[0]['subreddit'] == 'wallstreetbets'
            assert posts[1]['title'] == "Test 2"
            assert posts[1]['score'] == 200

    def test_search_subreddit_error_handling(self):
        """Test that _search_subreddit handles exceptions gracefully."""
        with patch('src.reddit_scraper.praw.Reddit'):
            scraper = RedditScraper()
            mock_reddit = Mock()
            scraper.reddit = mock_reddit

            # Mock subreddit that raises exception
            mock_subreddit = Mock()
            mock_subreddit.search.side_effect = Exception("API Error")
            mock_reddit.subreddit.return_value = mock_subreddit

            # Execute - should not raise
            posts = scraper._search_subreddit('wallstreetbets', 'AAPL', 20)

            # Verify - returns empty list on error
            assert posts == []


class TestRedditScraperIntegration:
    """Integration-style tests (still mocked but more realistic)."""

    @patch('src.reddit_scraper.praw.Reddit')
    def test_complete_workflow_with_mixed_sentiment(self, mock_reddit_class):
        """Test complete workflow with realistic mixed sentiment posts."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        def create_mock_post(title, score, comments):
            post = Mock()
            post.title = title
            post.score = score
            post.num_comments = comments
            post.created_utc = datetime.now().timestamp()
            return post

        # Realistic mix of posts
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("NVDA to the moon! 🚀", 500, 150),  # Very positive
            create_mock_post("NVDA earnings analysis", 100, 50),  # Neutral
            create_mock_post("NVDA concerns about valuations", 30, 20),  # Skeptical
            create_mock_post("Why NVDA will beat expectations", 200, 80),  # Positive
            create_mock_post("NVDA options play", 75, 30),  # Neutral
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # Execute
        result = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Verify realistic output
        assert result['posts_found'] == 5
        assert result['ticker'] == 'NVDA'
        assert result['total_comments'] == 330
        assert result['avg_score'] == 181.0  # (500 + 100 + 30 + 200 + 75) / 5
        assert result['sentiment_score'] == 1.0  # Capped positive sentiment
        assert len(result['top_posts']) == 5
        assert result['top_posts'][0]['score'] == 500  # Highest score first
