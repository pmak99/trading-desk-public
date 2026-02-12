# Trading Desk - IV Crush System

## Project Overview

Production quantitative options trading system focused on **IV Crush** - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

> **Note:** Scoring weights, thresholds, strategy performance data, and proprietary trading algorithms have been removed from this public version. This repository showcases Claude Code integration patterns.

## Architecture

```
agents  Agent Orchestration ──→ Parallel Claude Code agents
cloud   Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram
sentiment AI Sentiment        ──→ Perplexity sentiment layer
core    Core Math Engine    ──→ VRP/strategy calculations (shared library)
```

All subsystems import core via `sys.path` injection. Sentiment, cloud, and agents never duplicate core's math.

## Slash Commands

| Command | Purpose |
|---------|---------|
| `/whisper` | Most anticipated earnings with VRP |
| `/analyze TICKER` | Deep dive for trade decision |
| `/scan DATE` | Scan all earnings on date |
| `/prime` | Pre-cache sentiment for the week |
| `/alert` | Today's high-VRP alerts |
| `/health` | System status check |
| `/maintenance MODE` | System maintenance (sync, backup, backfill, cleanup, validate) |
| `/journal FILE` | Parse Fidelity CSV/PDF |
| `/backtest [TICKER]` | Performance analysis |
| `/history TICKER` | Historical earnings moves |
| `/backfill ARGS` | Record post-earnings outcomes |
| `/collect TICKER` | Collect pre-earnings sentiment |
| `/export-report [MODE]` | Export to CSV/JSON |
| `/positions [TICKER]` | Open positions and 30-day exposure dashboard |
| `/risk [DAYS]` | Portfolio risk assessment (TRR, concentration, drawdown) |
| `/calendar [DATE]` | Weekly earnings calendar with history and TRR flags |
| `/pnl [PERIOD]` | P&L summary (week/month/ytd/year/quarter/N days) |
| `/postmortem TICKER` | Post-earnings: predicted vs actual move analysis |
| `/deploy [--quick\|--status\|--logs\|--rollback]` | Deploy cloud to Cloud Run |

## CLI Commands

```bash
# Core
cd core/ && ./trade.sh TICKER DATE    # Single analysis
cd core/ && ./trade.sh scan DATE      # Scan all earnings
cd core/ && ./trade.sh whisper        # Most anticipated
cd core/ && ./trade.sh sync-cloud     # Sync DB + backup
cd core/ && ./trade.sh health         # Health check

# Agents
cd agents/ && ./agent.sh whisper        # Parallel scan
cd agents/ && ./agent.sh analyze TICKER # Deep dive
```

## Cloud API

**Base:** `https://your-cloud-run-url.run.app`

| Endpoint | Description |
|----------|-------------|
| `/api/analyze?ticker=XXX` | Deep analysis |
| `/api/whisper` | High-VRP opportunities |
| `/api/scan?date=YYYY-MM-DD` | Scan date |
| `/api/health` | System health |

Rate limit: 60 req/min per IP.

## Environment Variables

```bash
TRADIER_API_KEY=xxx          # Options data (primary)
ALPHA_VANTAGE_KEY=xxx        # Earnings calendar
TWELVE_DATA_KEY=xxx          # Historical prices
PERPLEXITY_API_KEY=xxx       # AI sentiment
DB_PATH=data/ivcrush.db
```

## Testing

> Tests have been removed from this public version. The full system has 1,300+ tests across all subsystems.

## Working Style Preferences

1. **Confirm scope before executing** — For data operations (backfill, sync, cleanup), confirm the exact scope (which tickers, which date range, which database) before executing. For batch operations, always scope to the user's actual data (historically traded tickers from `strategies`/`trade_journal`, existing watchlists) rather than arbitrary/random selections.
2. **Respect rewrite requests** — When asked for a full rewrite or ground-up rebuild, do NOT attempt incremental fixes first. Go straight to the clean-slate approach.
3. **Audit all related configs** — When fixing any issue, find ALL files that reference the same service, URL, endpoint, or config value and fix them together. A fix in one file often needs to be mirrored in others.
4. **Source env before API calls** — Always check for required environment variables (`TRADIER_API_KEY`, `TWELVE_DATA_KEY`, `PERPLEXITY_API_KEY`, etc.) before executing scripts that need them. Source `.env` if needed.
5. **Verify data, don't infer** — When checking external sources (job postings, ticker lookups, API responses), verify actual data fields rather than inferring from URLs or titles. Validate ticker symbols against exchange lookup (e.g., "PFIZER" -> "PFE").
6. **Complete output, no truncation** — Slash commands (`/prime`, `/whisper`, `/analyze`, `/backfill`, `/scan`, etc.) must run to full completion with complete output displayed. Never truncate results.
