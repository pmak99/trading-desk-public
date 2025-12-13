# Grafana Dashboards Design

**Date:** 2025-12-12
**Status:** Approved

## Overview

Add Grafana Cloud integration for metrics and dashboards to Trading Desk 5.0.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Endpoints   │────▶│  metrics.py  │────▶│ Grafana Cloud│
│              │     │  record()    │     │ Graphite API │
└──────────────┘     │  push()      │     └──────────────┘
                     └──────────────┘
```

- Single new file: `src/core/metrics.py`
- Uses Grafana Cloud's Graphite endpoint (simple HTTP POST)
- Synchronous fire-and-forget (low volume, no async batching needed)

## Metrics

| Metric | Type | Description | Tags |
|--------|------|-------------|------|
| `ivcrush.request.duration` | timing | Request latency in ms | endpoint |
| `ivcrush.request.status` | count | Success/error counts | endpoint, status |
| `ivcrush.vrp.ratio` | gauge | VRP ratio per analysis | ticker |
| `ivcrush.vrp.tier` | count | Count by tier | tier |
| `ivcrush.liquidity.tier` | count | Count by tier | tier |
| `ivcrush.sentiment.score` | gauge | Sentiment score | ticker |
| `ivcrush.api.calls` | count | External API calls | provider |
| `ivcrush.api.latency` | timing | External API latency | provider |
| `ivcrush.budget.remaining` | gauge | Perplexity budget left | - |
| `ivcrush.tickers.qualified` | count | Tickers passing VRP threshold | - |

## Dashboards

1. **Operations** - Request rate, error rate, P95 latency, latency heatmap
2. **Trading** - VRP distribution, qualified tickers/day, tier breakdown
3. **API** - Calls by provider, latency by provider, budget gauge
4. **Whisper** - Daily scan results, top VRP tickers, sentiment distribution

## Implementation Steps

1. Grafana Cloud setup (manual)
2. Create `src/core/metrics.py`
3. Instrument endpoints in `main.py`
4. Create dashboards in Grafana UI
5. Add `/dashboard` bot command
6. Update docs

## Environment Variables

```
GRAFANA_GRAPHITE_URL=https://graphite-prod-xx.grafana.net/graphite/metrics
GRAFANA_USER=<instance-id>
GRAFANA_API_KEY=<api-key>
```
