# Trading Alerts (Notification Skill)

Configure and manage alerts for high-VRP opportunities and position updates.

## Quick Alert Check

Run the alert checker to scan for opportunities:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk && python scripts/check_alerts.py
```

This will:
- Run today's scan
- Filter for VRP >= 4.0x opportunities
- Highlight EXCELLENT tier setups
- Show alert summary

## Alert Types

### 1. High-VRP Opportunity Alerts
Trigger when scan finds opportunities meeting criteria:
- VRP >= 7.0x (EXCELLENT tier)
- Liquidity = EXCELLENT
- POP >= 60%

### 2. Position Status Alerts
Monitor Alpaca positions for:
- P&L threshold breaches (profit target or stop loss)
- Position opened/closed
- Order fills

### 3. Earnings Calendar Alerts
Upcoming earnings for watchlist tickers:
- 1 day before earnings
- Morning of earnings day

## Check Current Alpaca Positions

```bash
# Via MCP - use alpaca_list_positions tool
```

Use the Alpaca MCP server to check positions:
- `mcp__alpaca__alpaca_list_positions` - List all open positions
- `mcp__alpaca__alpaca_account_overview` - Account summary with positions
- `mcp__alpaca__alpaca_get_position` - Get specific position details

## Scan for Alert Conditions

Run scan and filter for alert-worthy opportunities:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $(date +%Y-%m-%d) 2>&1 | grep -E "EXCELLENT.*VRP.*[7-9]\.[0-9]|VRP.*[1-9][0-9]\."
```

## Alert Message Format

When an alert condition is met, format the message:

```
🚨 IV CRUSH ALERT

Ticker: NVDA
Earnings: 2025-12-15
VRP: 8.2x (EXCELLENT)
Implied Move: 12.5%
Historical Mean: 1.52%
Liquidity: EXCELLENT

Recommended: Iron Condor
- Strikes: 130/135/145/150
- POP: 72%
- Max Profit: $450
- Max Risk: $550

Action Required: Review and enter before close
```

## Position Alert Format

```
📊 POSITION UPDATE

NVDA Iron Condor
Status: PROFIT TARGET HIT

Entry: $2.50 credit
Current: $0.25 (90% profit)
P&L: +$225

Action: Consider closing to lock in gains
```

## Integration Options

### Manual Check
Run `/alert` command to check for:
- New high-VRP opportunities from today's scan
- Current position P&L status
- Upcoming earnings for watched tickers

### Watchlist Management
Maintain a watchlist of preferred tickers:
- NVDA, TSLA, META, AMZN, GOOGL, AAPL, MSFT
- AMD, CRM, NFLX, SNOW, PLTR

Check earnings dates for watchlist:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh list NVDA,TSLA,META,AMZN $(date +%Y-%m-%d)
```

## Usage Examples

"Check for any high-VRP alerts today"
"Show my current Alpaca positions"
"Any earnings coming up for my watchlist?"
"Alert me to NVDA opportunities this week"

## External Integration (Future)

To add Slack/Discord webhooks:
1. Create webhook URL in Slack/Discord
2. Add to environment: `SLACK_WEBHOOK_URL=https://...`
3. Use curl to post alert messages

Example webhook post:
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"🚨 High VRP Alert: NVDA 8.2x"}' \
  $SLACK_WEBHOOK_URL
```
