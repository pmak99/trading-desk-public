"""Unit tests for Reddit scraper."""

import pytest
from unittest.mock import patch, Mock, MagicMock
from datetime import datetime
from src.reddit_scraper import RedditScraper


@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables for Reddit API."""
    monkeypatch.setenv('REDDIT_CLIENT_ID', 'test_client_id')
    monkeypatch.setenv('REDDIT_CLIENT_SECRET', 'test_client_secret')


@pytest.fixture
def mock_reddit_post():
    """Create a mock Reddit post."""
    post = Mock()
    post.title = "NVDA to the moon!"
    post.score = 150
    post.num_comments = 42
    post.created_utc = datetime.now().timestamp()
    return post


@pytest.fixture
def scraper(mock_env):
    """Create scraper instance with mocked Reddit API."""
    with patch('praw.Reddit') as mock_reddit:
        mock_reddit.return_value = MagicMock()
        return RedditScraper()


def test_scraper_initialization(mock_env):
    """Test scraper initializes with Reddit client."""
    with patch('praw.Reddit') as mock_reddit:
        scraper = RedditScraper()
        mock_reddit.assert_called_once()


@patch('praw.Reddit')
def test_get_ticker_sentiment_with_posts(mock_reddit_class, mock_env, mock_reddit_post):
    """Test sentiment analysis with posts found."""
    # Setup mock
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()

    # Create multiple mock posts
    mock_posts = [mock_reddit_post for _ in range(5)]
    mock_subreddit.search.return_value = mock_posts
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("NVDA")

    assert result['ticker'] == "NVDA"
    assert result['posts_found'] > 0
    assert isinstance(result['sentiment_score'], float)
    assert -1.0 <= result['sentiment_score'] <= 1.0
    assert 'avg_score' in result
    assert 'total_comments' in result


@patch('praw.Reddit')
def test_get_ticker_sentiment_no_posts(mock_reddit_class, mock_env):
    """Test sentiment analysis when no posts found."""
    # Setup mock
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()
    mock_subreddit.search.return_value = []
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("INVALID")

    assert result['ticker'] == "INVALID"
    assert result['posts_found'] == 0
    assert result['sentiment_score'] == 0.0
    assert result['avg_score'] == 0
    assert result['total_comments'] == 0


@patch('praw.Reddit')
def test_get_ticker_sentiment_custom_subreddits(mock_reddit_class, mock_env, mock_reddit_post):
    """Test with custom subreddit list."""
    # Setup mock
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()
    mock_subreddit.search.return_value = [mock_reddit_post]
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment(
        "NVDA",
        subreddits=['wallstreetbets', 'investing']
    )

    assert result['ticker'] == "NVDA"
    assert result['posts_found'] > 0


@patch('praw.Reddit')
def test_get_ticker_sentiment_error_handling(mock_reddit_class, mock_env):
    """Test error handling when subreddit access fails."""
    # Setup mock to raise exception
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()
    mock_subreddit.search.side_effect = Exception("API Error")
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test - should not crash, should return empty result
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("NVDA")

    assert result['ticker'] == "NVDA"
    assert result['posts_found'] == 0


@patch('praw.Reddit')
def test_sentiment_score_calculation(mock_reddit_class, mock_env):
    """Test sentiment score calculation logic."""
    # Setup mock with specific scores
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()

    # Create posts with known scores
    mock_posts = []
    for score in [100, 200, 300]:
        post = Mock()
        post.title = "Test post"
        post.score = score
        post.num_comments = 10
        post.created_utc = datetime.now().timestamp()
        mock_posts.append(post)

    mock_subreddit.search.return_value = mock_posts
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test - specify single subreddit to get exactly 3 posts
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("TEST", subreddits=['wallstreetbets'])

    expected_avg = (100 + 200 + 300) / 3  # 200
    assert result['avg_score'] == expected_avg
    assert result['posts_found'] == 3
    assert result['total_comments'] == 30  # 10 * 3


@patch('praw.Reddit')
def test_top_posts_included(mock_reddit_class, mock_env):
    """Test that top posts are included in results."""
    # Setup mock
    mock_reddit = MagicMock()
    mock_subreddit = MagicMock()

    # Create posts with different scores
    mock_posts = []
    for i, score in enumerate([500, 100, 300, 200, 400]):
        post = Mock()
        post.title = f"Post {i}"
        post.score = score
        post.num_comments = 10
        post.created_utc = datetime.now().timestamp()
        mock_posts.append(post)

    mock_subreddit.search.return_value = mock_posts
    mock_reddit.subreddit.return_value = mock_subreddit
    mock_reddit_class.return_value = mock_reddit

    # Test
    scraper = RedditScraper()
    result = scraper.get_ticker_sentiment("TEST")

    assert 'top_posts' in result
    assert len(result['top_posts']) <= 5

    # Verify top posts are sorted by score
    if len(result['top_posts']) > 1:
        scores = [p['score'] for p in result['top_posts']]
        assert scores == sorted(scores, reverse=True)
