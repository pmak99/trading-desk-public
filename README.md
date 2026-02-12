# Trading Desk

> **Portfolio showcase** — proprietary trading algorithms have been removed from this public version. This repository demonstrates Claude Code integration patterns: custom slash commands, MCP servers, agent orchestration, and AI-assisted development methodology.

## What This Is

A real trading system used daily for options trading around earnings announcements. It exploits the **Implied Volatility Crush** — selling options when IV is elevated before earnings, profiting from the volatility collapse after the announcement.

**570+ commits** of iterative AI pair-programming development (Oct 2025 – Feb 2026).

## Architecture

```
agents  Agent Orchestration ──→ Parallel Claude Code agents for analysis
cloud   Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram bot
sentiment AI Sentiment      ──→ Perplexity-powered sentiment layer
core    Core Math Engine    ──→ VRP/strategy calculations (shared library)
```

All subsystems import core as a shared library via `sys.path` injection. Sentiment, cloud, and agents never duplicate core's math.

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

The [`CLAUDE.md`](CLAUDE.md) file serves as a domain knowledge base for Claude Code — VRP thresholds, trading rules, and system architecture. This means every Claude Code session starts with full domain context.

### Development Methodology

All 570+ commits show AI-assisted development patterns:
- Test-driven development with Claude Code running tests iteratively
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
├── cloud/             Cloud autopilot (FastAPI + Telegram)
├── agents/            Agent orchestration (parallel Claude Code)
├── mcp-servers/       Custom MCP server (Perplexity budget tracker)
├── docs/              Architecture documentation
├── .claude/           Claude Code commands (19 slash commands)
├── CLAUDE.md          Domain knowledge base for Claude Code
└── .github/           CI workflows and Dependabot config
```

---

**Disclaimer:** For research and educational purposes only. Not financial advice. Options trading involves substantial risk of loss.

**License:** [MIT](LICENSE)
