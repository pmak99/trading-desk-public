# Earnings Options Automation System

A semi-automated earnings options trading system built with Python, featuring AI-powered sentiment analysis and strategy generation.

## Project Status

**Current Phase:** Phase 1 - Data Collection Layer
**Version:** 0.1.0-dev

## Overview

This system automates the analysis of upcoming earnings announcements and generates options trading recommendations using:
- Earnings calendar scanning (yfinance)
- Reddit sentiment analysis (PRAW)
- AI-powered sentiment analysis (Sonar Deep Research)
- Strategy generation (GPT-5 Thinking)
- Position sizing based on confidence scores

## Phase 1 Features

- **Earnings Scanner**: Identifies upcoming earnings in the next 14 days
- **Reddit Scraper**: Analyzes sentiment from r/wallstreetbets, r/stocks, r/options
- **Unit Tests**: Comprehensive test coverage with mocked API calls

## Installation

1. Clone the repository:
```bash
git clone git@github.com:pmak99/trading-desk.git
cd trading-desk
```

2. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure API keys:
```bash
cp .env.example .env
# Edit .env and add your actual API keys
```

## API Keys Required

- **Reddit API**: Get credentials from https://www.reddit.com/prefs/apps
- **Perplexity API** (Phase 2): Get from https://www.perplexity.ai/api
- **Alpha Vantage** (Phase 2): Get from https://www.alphavantage.co/support/#api-key

## Usage

### Run Earnings Scanner
```bash
python -m src.earnings_scanner
```

### Run Reddit Scraper
```bash
python -m src.reddit_scraper
```

### Run Tests
```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_earnings_scanner.py
```

## Project Structure

```
trading-desk/
├── src/                    # Source code
│   ├── earnings_scanner.py # Earnings calendar scanner
│   └── reddit_scraper.py   # Reddit sentiment scraper
├── tests/                  # Unit tests
│   ├── conftest.py         # Pytest fixtures
│   ├── test_earnings_scanner.py
│   └── test_reddit_scraper.py
├── config/                 # Configuration files
├── scripts/                # Utility scripts
├── data/                   # Output data (not tracked)
├── .env.example            # API key template
├── .gitignore              # Git ignore patterns
├── requirements.txt        # Python dependencies
├── pytest.ini              # Test configuration
└── README.md              # This file
```

## Development Roadmap

- [x] **Phase 1**: Data collection (earnings + Reddit)
- [ ] **Phase 2**: AI integration (Sonar + GPT-5 + Alpha Vantage)
- [ ] **Phase 3**: Reports & execution (position sizing + CSV reports)
- [ ] **Phase 4**: Deployment & automation (daily runner + docs)

## Testing

All tests use mocks to avoid real API calls during testing. Tests are designed for:
- Fast execution
- No external dependencies
- High code coverage (target: 95%+)

## Security

- API keys are stored in `.env` file (not tracked by git)
- `.env.example` provides template for required keys
- Never commit secrets to git

## License

Private project - All rights reserved

## Contributing

This is a private project for personal trading automation.
