# Trading Desk

> Production quantitative options trading system for IV Crush strategies, built iteratively with Claude Code as an AI pair-programming partner.

## What This Is

A real trading system used daily for options trading around earnings announcements. It exploits the **Implied Volatility Crush** — selling options when IV is elevated before earnings, profiting from the volatility collapse after the announcement.

- **200+ strategies** tracked with full P&L accounting
- **~55-60% win rate** across all strategy types
- **6,800+ historical earnings moves** for backtesting and pattern analysis
- **1,300+ tests** across four subsystems
- **570+ commits** of iterative development (Oct 2025 – Feb 2026)

## Architecture

```
agents  Agent Orchestration ──→ Parallel Claude Code agents for analysis
cloud   Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram bot
sentiment AI Sentiment      ──→ Perplexity-powered sentiment layer
core    Core Math Engine    ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
SQLite (ivcrush.db)         ──→ 15 tables, 6,861 historical moves
```

All subsystems import core as a shared library via `sys.path` injection. Sentiment, cloud, and agents never duplicate core's math.

| Subsystem | Purpose | Tests | Key Tech |
|-----------|---------|------:|----------|
| [core](core/) | VRP math, scoring, strategy generation | 690 | SQLite, DDD, configurable scoring |
| [sentiment](sentiment/) | AI sentiment with budget tracking | 221 | Perplexity API, 3hr TTL cache |
| [cloud](cloud/) | 24/7 autopilot with Telegram alerts | 311 | FastAPI, Cloud Run, GCS sync |
| [agents](agents/) | Parallel agent orchestration | 82 | Claude Code agents, YAML config |

## Claude Code Integration

This project was built almost entirely through AI pair-programming with Claude Code. The integration goes beyond simple code generation:

### 19 Custom Slash Commands

Every daily workflow is a Claude Code slash command in [`.claude/commands/`](.claude/commands/):

| Command | What It Does |
|---------|-------------|
| `/whisper` | Find most anticipated earnings with VRP analysis |
| `/analyze TICKER` | Deep dive single ticker for trade decision |
| `/scan DATE` | Scan all earnings on a specific date |
| `/prime` | Pre-cache sentiment for upcoming earnings week |
| `/alert` | Today's high-VRP trading alerts |
| `/health` | System status (APIs, DB, budget) |
| `/maintenance MODE` | System maintenance (sync, backup, backfill, cleanup, validate) |
| `/journal FILE` | Parse Fidelity CSV/PDF trade statements |
| `/backtest [TICKER]` | Performance analysis from strategies DB |
| `/history TICKER` | Historical earnings moves with pattern analysis |
| `/backfill ARGS` | Record post-earnings outcomes |
| `/collect TICKER` | Collect and store pre-earnings sentiment |
| `/export-report [MODE]` | Export to CSV/JSON |
| `/positions [TICKER]` | Open positions and 30-day exposure dashboard |
| `/risk [DAYS]` | Portfolio risk assessment (TRR, concentration, drawdown) |
| `/calendar [DATE]` | Weekly earnings calendar with history and TRR flags |
| `/pnl [PERIOD]` | P&L summary (week/month/ytd/year/quarter/N days) |
| `/postmortem TICKER` | Post-earnings: predicted vs actual move analysis |
| `/deploy [--quick]` | Deploy cloud to Cloud Run |

### Custom MCP Server

A [budget-aware Perplexity MCP server](mcp-servers/perplexity-tracked/) that:
- Tracks API spending against a monthly budget ($5 hard cap)
- Persists call history in SQLite for cost analysis
- Provides `perplexity_ask` and `perplexity_search` tools to Claude Code
- Cascades between `sonar` and `sonar-pro` models based on query complexity

### CLAUDE.md as Living Documentation

The [`CLAUDE.md`](CLAUDE.md) file serves as a domain knowledge base for Claude Code — VRP thresholds, scoring weights, strategy performance data, liquidity tiers, and critical trading rules. This means every Claude Code session starts with full domain context.

### Development Methodology

All 570+ commits show AI-assisted development patterns:
- Test-driven development with Claude Code running tests iteratively
- Architectural decisions documented in [`docs/plans/`](docs/plans/)
- Progressive system evolution from local CLI to cloud autopilot to agent orchestration

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| **`sys.path` injection** over packages | Four subsystems share core's math without pip install complexity |
| **SQLite** over Postgres | Single-user system, portable, cloud-syncable via GCS |
| **Budget-aware AI** | Hard $5/month cap on Perplexity API prevents runaway costs |
| **DDD architecture** | Domain/application/infrastructure separation in core enables testing |
| **YAML agent config** | Agent behavior (models, tools, prompts) configured without code changes |
| **Tail Risk Ratio (TRR)** | Position sizing based on historical tail risk, not just VRP |

## Performance Highlights

| Metric | Value |
|--------|-------|
| Overall win rate | ~55-60% |
| Strategies tracked | 200+ |
| Best strategy type | SINGLE options (~60-65% win rate) |
| Best TRR tier | LOW TRR (~70% win rate, strong profit) |
| Historical moves DB | 6,861 records across 400+ tickers |

## Getting Started

### Prerequisites

- Python 3.11+
- API keys: Tradier, Alpha Vantage, Twelve Data, Perplexity

### Setup

```bash
# Clone and set up core
cd core/
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add your API keys

# Run tests
python -m pytest tests/ -v

# Analyze a ticker
./trade.sh NVDA 2026-02-10

# Scan all earnings on a date
./trade.sh scan 2026-02-10
```

### Agent System

```bash
cd agents/
./agent.sh whisper              # Find opportunities (parallel)
./agent.sh analyze NVDA         # Deep dive with TRR + sentiment
```

### Cloud Deployment

```bash
cd cloud/
./deploy.sh                     # Deploy to Cloud Run
```

## Directory Structure

```
trading-desk/
├── core/              Core math engine (VRP, liquidity, strategies)
├── sentiment/         AI sentiment layer (Perplexity integration)
├── cloud/             Cloud autopilot (FastAPI + Telegram)
├── agents/            Agent orchestration (parallel Claude Code)
├── common/            Shared constants and enums
├── scripts/           Data pipelines and utilities
├── docs/              Architecture docs and implementation plans
├── mcp-servers/       Custom MCP server (Perplexity budget tracker)
├── .claude/           Claude Code commands (19 slash commands)
├── CLAUDE.md          Domain knowledge base for Claude Code
└── .github/           CI workflows and Dependabot config
```

## Testing

```bash
cd core && ./venv/bin/python -m pytest tests/ -v          # 690 tests
cd sentiment && ../core/venv/bin/python -m pytest tests/  # 221 tests
cd cloud && ../core/venv/bin/python -m pytest tests/      # 311 tests
cd agents && ../core/venv/bin/python -m pytest tests/     # 82 tests
```

Total: **1,304 tests** across all subsystems.

---

**Disclaimer:** For research and educational purposes only. Not financial advice. Options trading involves substantial risk of loss.

**License:** [MIT](LICENSE)
