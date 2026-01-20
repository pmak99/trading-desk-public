# Perplexity Tracked MCP Server

A custom MCP server that replaces the standard Perplexity MCP and logs all API usage with accurate token counts to the budget tracker database.

## Features

- **Accurate Token Tracking**: Extracts actual token counts from Perplexity API responses
- **Invoice-Verified Pricing**: Uses rates from actual Perplexity invoice
- **Budget Integration**: Logs to the same database as 4.0 budget tracker
- **Drop-in Replacement**: Exposes the same tools as the standard perplexity MCP

## Installation

```bash
cd mcp-servers/perplexity-tracked
pip install -r requirements.txt
```

Or use the project's virtual environment:

```bash
$PROJECT_ROOT/2.0/venv/bin/pip install mcp httpx
```

## Configuration

Update `~/.claude.json` to use this server instead of the standard Perplexity MCP:

```json
{
  "mcpServers": {
    "perplexity": {
      "type": "stdio",
      "command": "$PROJECT_ROOT/2.0/venv/bin/python",
      "args": [
        "$PROJECT_ROOT/mcp-servers/perplexity-tracked/server.py"
      ],
      "env": {
        "PERPLEXITY_API_KEY": "pplx-xxx...your-key-here",
        "BUDGET_DB_PATH": "$PROJECT_ROOT/4.0/data/sentiment_cache.db"
      }
    }
  }
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PERPLEXITY_API_KEY` | Yes | Your Perplexity API key |
| `BUDGET_DB_PATH` | No | Path to budget tracker database (defaults to 4.0 db) |

## Available Tools

| Tool | Description | Model |
|------|-------------|-------|
| `perplexity_ask` | Basic chat completion | sonar |
| `perplexity_search` | Web search with citations | sonar |
| `perplexity_research` | Deep research with citations | sonar-pro |
| `perplexity_reason` | Reasoning tasks | sonar-reasoning-pro |

## Token Pricing

Rates from January 2025 Perplexity invoice:

| Category | Rate | Example |
|----------|------|---------|
| sonar output | $0.000001/token | 1M tokens = $1 |
| sonar-pro output | $0.000015/token | 1M tokens = $15 |
| reasoning-pro | $0.000003/token | 1M tokens = $3 |
| Search API | $0.005/request | 1K requests = $5 |

## Database Schema

Logs to `api_budget` table with token breakdown:

```sql
CREATE TABLE api_budget (
    date TEXT PRIMARY KEY,
    calls INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    last_updated TEXT,
    output_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    search_requests INTEGER DEFAULT 0
);
```

## Testing

```bash
# Test server startup
python server.py

# Should see MCP initialization messages
# Ctrl+C to exit
```

## Verification

After making Perplexity calls, verify token tracking:

```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT * FROM api_budget ORDER BY date DESC LIMIT 5"
```
