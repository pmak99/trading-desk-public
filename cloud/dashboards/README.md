# Grafana Dashboards

Pre-built dashboards for monitoring the Trading Desk API and trading metrics.

## Dashboards

| Dashboard | File | Description |
|-----------|------|-------------|
| **Operations** | `operations.json` | Request rate, error rate, latency by endpoint |
| **Trading** | `trading.json` | VRP ratios, qualified tickers, sentiment, liquidity tiers |
| **API & Budget** | `api.json` | External API calls by provider, budget tracking |
| **Whisper & Scans** | `whisper.json` | Scan results, VRP/liquidity tier distributions, trends |

## Import Instructions

### Prerequisites

1. Grafana Cloud account with Graphite datasource configured
2. Note your datasource UID (found in Grafana > Connections > Data sources > Graphite)

### Import Steps

1. **Open Grafana Cloud** and navigate to Dashboards

2. **Click "New" > "Import"**

3. **Upload JSON file** or paste contents

4. **Configure datasource variable**:
   - When prompted, select your Graphite datasource for `DS_GRAFANACLOUD-TRADINGDESK-GRAPHITE`
   - If your datasource has a different name, the import will ask you to map it

5. **Click "Import"**

6. **Repeat** for each dashboard file

### Post-Import

- Dashboards auto-refresh (Operations: 30s, Trading/API: 1m)
- Default time range: 6h (Operations) or 24h (Trading/API)
- Timezone: America/New_York

## Metric Names Reference

### Operations Metrics
- `ivcrush.request.duration` - Request latency in ms (tags: endpoint)
- `ivcrush.request.status` - Request count (tags: endpoint, status)

### Trading Metrics
- `ivcrush.vrp.ratio` - VRP ratio per ticker (tags: ticker)
- `ivcrush.vrp.tier` - VRP tier count (tags: tier)
- `ivcrush.liquidity.tier` - Liquidity tier count (tags: tier)
- `ivcrush.sentiment.score` - Sentiment score per ticker (tags: ticker)
- `ivcrush.tickers.qualified` - Count of qualified tickers

### API & Budget Metrics
- `ivcrush.api.calls` - External API call count (tags: provider, status)
- `ivcrush.api.latency` - External API latency in ms (tags: provider)
- `ivcrush.budget.calls_remaining` - Perplexity calls remaining
- `ivcrush.budget.dollars_remaining` - Monthly budget remaining

## Customization

Dashboards use Graphite's `seriesByTag()` function for querying. Example queries:

```graphite
# VRP ratio for all tickers
aliasByTags(seriesByTag('name=ivcrush.vrp.ratio'), 'ticker')

# API calls filtered by success status
seriesByTag('name=ivcrush.api.calls', 'status=success')

# Average latency across all endpoints
averageSeries(seriesByTag('name=ivcrush.request.duration'))
```

## Troubleshooting

**No data showing?**
- Verify metrics are being pushed: check Cloud Run logs for "Metrics pushed"
- Confirm datasource connection in Grafana
- Check time range - metrics may not exist for selected period

**Wrong datasource?**
- Edit dashboard settings > Variables
- Or re-import with correct datasource selected
