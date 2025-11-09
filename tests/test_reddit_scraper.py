"""
Tests for Reddit scraper with parallel search optimization.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from src.data.reddit_scraper import RedditScraper


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


class TestRedditScraperCaching:
    """Test caching functionality for Reddit scraper."""

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_initialized_on_init(self, mock_reddit_class):
        """Test that cache is initialized when scraper is created."""
        scraper = RedditScraper()

        assert hasattr(scraper, '_cache')
        assert scraper._cache is not None
        assert scraper._cache.max_size == 100
        assert scraper._cache.ttl is not None

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_hit_returns_cached_result(self, mock_reddit_class):
        """Test that cached results are returned without calling Reddit API."""
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

        # Mock Reddit API to return posts
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("First call post", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # First call - should hit Reddit API
        result1 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Verify API was called
        assert mock_subreddit.search.called
        assert result1['posts_found'] == 1
        call_count_1 = mock_subreddit.search.call_count

        # Second call - should use cache
        result2 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Verify API was NOT called again (cache hit)
        assert mock_subreddit.search.call_count == call_count_1, "API should not be called on cache hit"
        assert result2 == result1, "Cached result should match original"

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_miss_fetches_new_data(self, mock_reddit_class):
        """Test that different parameters cause cache miss."""
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

        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("Test post", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # First call with NVDA
        result1 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])
        call_count_1 = mock_subreddit.search.call_count

        # Second call with different ticker - should cause cache miss
        result2 = scraper.get_ticker_sentiment('TSLA', subreddits=['wallstreetbets'])

        # Verify API was called again
        assert mock_subreddit.search.call_count > call_count_1, "Different ticker should cause cache miss"

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_key_includes_parameters(self, mock_reddit_class):
        """Test that cache key varies with different parameters."""
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

        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("Test post", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # Same ticker but different subreddits should miss cache
        result1 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])
        call_count_1 = mock_subreddit.search.call_count

        result2 = scraper.get_ticker_sentiment('NVDA', subreddits=['stocks'])

        # Should have called API again
        assert mock_subreddit.search.call_count > call_count_1

    @patch('src.reddit_scraper.praw.Reddit')
    def test_empty_results_cached(self, mock_reddit_class):
        """Test that empty results are also cached."""
        scraper = RedditScraper()
        mock_reddit = mock_reddit_class.return_value
        scraper.reddit = mock_reddit

        # Mock empty results
        mock_subreddit = Mock()
        mock_subreddit.search.return_value = []
        mock_reddit.subreddit.return_value = mock_subreddit

        # First call - should hit API
        result1 = scraper.get_ticker_sentiment('RARE', subreddits=['wallstreetbets'])
        assert result1['posts_found'] == 0
        call_count_1 = mock_subreddit.search.call_count

        # Second call - should use cache
        result2 = scraper.get_ticker_sentiment('RARE', subreddits=['wallstreetbets'])

        # Verify API was NOT called again
        assert mock_subreddit.search.call_count == call_count_1, "Empty results should be cached"
        assert result2['posts_found'] == 0

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_ttl_respected(self, mock_reddit_class):
        """Test that cache respects TTL (time-to-live)."""
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

        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("Test post", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # First call
        result1 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])
        call_count_1 = mock_subreddit.search.call_count

        # Mock cache to expire entry
        cache_key = list(scraper._cache.cache.keys())[0]
        old_value, old_timestamp = scraper._cache.cache[cache_key]

        # Set timestamp to 61 minutes ago (beyond 60-min TTL)
        from datetime import timedelta
        expired_timestamp = old_timestamp - timedelta(minutes=61)
        scraper._cache.cache[cache_key] = (old_value, expired_timestamp)

        # Second call - should hit API again because cache expired
        result2 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Verify API was called again
        assert mock_subreddit.search.call_count > call_count_1, "Expired cache should cause new API call"

    @patch('src.reddit_scraper.praw.Reddit')
    def test_cache_stats_tracking(self, mock_reddit_class):
        """Test that cache tracks hits and misses."""
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

        mock_subreddit = Mock()
        mock_subreddit.search.return_value = [
            create_mock_post("Test post", 100),
        ]
        mock_reddit.subreddit.return_value = mock_subreddit

        # Get initial stats
        initial_stats = scraper._cache.stats()

        # First call - cache miss
        result1 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Second call - cache hit
        result2 = scraper.get_ticker_sentiment('NVDA', subreddits=['wallstreetbets'])

        # Check stats
        final_stats = scraper._cache.stats()

        assert final_stats['hits'] > initial_stats['hits'], "Cache hits should increase"
        assert final_stats['size'] > 0, "Cache should have entries"
