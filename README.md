# Earnings Options Automation System

A semi-automated earnings options trading system built with Python, featuring AI-powered sentiment analysis and strategy generation.

## Project Status

**Current Phase:** Phase 1 Complete ✅ | Phase 2 In Progress 🚧
**Version:** 0.2.0-dev

## Overview

This system automates the analysis of upcoming earnings announcements and generates options trading recommendations using:
- Earnings calendar scanning (yfinance)
- Reddit sentiment analysis (PRAW)
- AI-powered sentiment analysis (Sonar Deep Research)
- Strategy generation (GPT-5 Thinking)
- Position sizing based on confidence scores

## Features

### Phase 1 - Data Collection ✅
- **Earnings Scanner**: Identifies upcoming earnings in the next 14 days
- **Reddit Scraper**: Analyzes sentiment from r/wallstreetbets, r/stocks, r/options
- **Unit Tests**: Comprehensive test coverage with mocked API calls (15/15 passing)

### Phase 2 - AI Integration & Cost Controls 🚧
- **Budget System**: $5/month budget cap with automatic cost tracking
- **Usage Tracker**: Real-time monitoring of API costs and token usage
- **Dual Model Support**:
  - `sonar-pro` for daily analysis (fast, cheap)
  - `sonar-deep-research` for high-priority tickers (thorough, slower)
- **Cost Dashboard**: View spending and remaining budget anytime

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

### View Cost Dashboard
```bash
python -m src.usage_tracker
```
Shows:
- Monthly budget status
- Today's API usage
- Cost breakdown by model
- Remaining budget

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
│   ├── reddit_scraper.py   # Reddit sentiment scraper
│   └── usage_tracker.py    # Budget & cost tracking system
├── tests/                  # Unit tests
│   ├── conftest.py         # Pytest fixtures
│   ├── test_earnings_scanner.py
│   └── test_reddit_scraper.py
├── config/                 # Configuration files
│   └── budget.yaml         # Budget configuration ($5/month cap)
├── scripts/                # Utility scripts
├── data/                   # Output data (not tracked)
│   └── usage.json          # API usage log (auto-generated)
├── .env                    # API keys (not tracked - YOU MUST CREATE THIS)
├── .env.example            # API key template
├── .gitignore              # Git ignore patterns
├── requirements.txt        # Python dependencies
├── pytest.ini              # Test configuration
└── README.md              # This file
```

## Development Roadmap

- [x] **Phase 1**: Data collection (earnings + Reddit) ✅ Complete
  - [x] Earnings scanner with yfinance
  - [x] Reddit sentiment scraper
  - [x] Unit tests (15/15 passing)
  - [x] All APIs tested and verified

- [ ] **Phase 2**: AI integration (Sonar + GPT-5 + Alpha Vantage) 🚧 In Progress
  - [x] Budget system ($5/month cap)
  - [x] Usage tracker with cost controls
  - [x] API testing (Reddit, Perplexity, Alpha Vantage)
  - [ ] API client wrappers
  - [ ] Sentiment analyzer (Sonar)
  - [ ] Strategy generator (GPT-5)
  - [ ] Options pricer (Alpha Vantage)

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
