<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

## Free Options Data APIs with Historical IV and IV Rank

Based on your IV crush earnings strategy, here are the best sources for free options data via API with historical implied volatility and IV rank:

### **Completely Free Options**

**1. Theta Data - Free EOD Historical Data**

Theta Data provides completely free End-of-Day (EOD) historical options data through their Python API. This includes:[^1]

- Historical options prices (bid/ask)
- All options contracts going back 10+ years[^2]
- Free access with just an account signup

However, Theta Data does **not** currently provide pre-calculated IV or Greeks in their free tier. You would need to calculate these yourself using a pricing model like Black-Scholes.[^3][^4]

**2. DoltHub Options Database**

DoltHub hosts a free SQL-accessible options database:[^5][^6]

- Historical option chain data back to 2019
- Includes bids, asks, volumes, and Greeks (delta, gamma, theta, vega)
- Also has a separate volatility_history table with IV data and yearly high/low values for IV rank calculations[^6]
- Access via SQL queries at: `https://www.dolthub.com/repositories/post-no-preference/options`

This appears to be your best **completely free** option with IV data already calculated.

**3. Syncretism/Ops API**

An open-source options search engine:[^7][^5]

- Scrapes Yahoo Finance data and calculates Greeks from it
- Free to use, self-hosted
- API documentation: `https://github.com/Tyruiop/syncretism`
- Calculates IV and Greeks from Yahoo Finance price data


### **Free Tier / Limited Free Access**

**4. Tradier Brokerage API**

Tradier offers free access to options data if you open a brokerage account:[^8][^9][^6]

- Real-time and historical options chains
- IV and all Greeks included (courtesy of ORATS)[^10][^11]
- Both REST API and live streaming available
- Greek values include: `bid_iv`, `mid_iv`, `ask_iv`, and `smv_vol` (ORATS final IV)[^10]

**5. Polygon.io**

Polygon has a free tier for basic options data:[^12][^13][^14]

- Free tier includes: All US stock tickers, 5 API calls/minute, 2 years historical data
- **However**: They do **not** provide pre-calculated IV or Greeks. You would need to calculate these yourself.[^3]

**6. Market Data App**

Offers a free tier for options data:[^15][^16][^17]

- Includes pricing, Greeks, and implied volatility
- Historical end-of-day quotes available
- Free Google Sheets add-on available[^18]

**7. QuantConnect**

Free access to historical options data:[^19][^20][^5]

- Options data dating back to 1998 for some securities
- Minute-level resolution available
- Free with account signup
- You can calculate IV rank yourself using rolling windows[^19]
- Data is accessible but requires using their platform/API


### **Paid but Affordable**

**8. ORATS**

The most comprehensive for your IV crush strategy:[^21][^22]

- **Delayed Data API**: \$99/month (20,000 requests)
- Includes IV rank and percentiles for last month and year[^21]
- Historical stock intraday volatility data
- Proprietary ORATS forecasts of implied volatility
- Specifically designed for options traders doing IV analysis

**9. IVolatility**

Professional-grade data:[^23][^24][^25]

- **Backtest API Essential**: Pricing varies
- Historical IV data back to 2000
- Over 600 filters for data quality
- Greeks and IV surfaces included


### **My Recommendation for Your Use Case**

Given your IV crush earnings strategy, I recommend this approach:

1. **Start with DoltHub** (completely free) - It has historical IV data and the components needed to calculate IV rank yourself. You can query it via SQL and it includes volatility history tables with yearly high/low values.[^6]
2. **Consider Tradier** if you're willing to open a brokerage account - You get free real-time access to IV and Greeks calculated by ORATS, which is exactly what professional options traders use.[^8][^10]
3. **Upgrade to ORATS** (\$99/month) if you need production-quality data - They specifically include IV rank and percentile calculations, which saves you from having to calculate these yourself. This is the most tailored solution for IV crush strategies.[^21]

For calculating IV rank yourself from raw IV data, you need: current IV, 52-week high IV, and 52-week low IV. The formula is: `IV Rank = (Current IV - 52 Week Low) / (52 Week High - 52 Week Low) * 100`.[^26][^27]
<span style="display:none">[^28][^29][^30][^31][^32][^33][^34][^35][^36][^37][^38][^39][^40][^41][^42][^43][^44][^45][^46][^47][^48][^49][^50][^51][^52][^53][^54][^55][^56][^57][^58][^59][^60][^61][^62][^63][^64][^65][^66][^67][^68][^69][^70][^71][^72][^73]</span>

<div align="center">⁂</div>

[^1]: https://www.thetadata.net/post/free-historical-options-data-new-api-features

[^2]: https://www.thetadata.net/options-data

[^3]: https://databento.com/options

[^4]: https://www.reddit.com/r/algotrading/comments/1ilxrr9/where_can_i_get_historical_options_data/

[^5]: https://www.reddit.com/r/algotrading/comments/pui841/historical_options_data_api_ideally_free/

[^6]: https://www.reddit.com/r/options/comments/y1lopg/where_to_get_options_data_from_for_free/

[^7]: https://github.com/Tyruiop/syncretism

[^8]: https://tradier.com/company/press/leading-options-trading-and-analysis-software-livevol-integrates-with-tradier

[^9]: https://docs.tradier.com/docs/market-data

[^10]: https://docs.tradier.com/docs/quotes

[^11]: https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains

[^12]: https://polygon.io/options

[^13]: https://polygon.io

[^14]: https://polygon.io/pricing

[^15]: https://www.marketdata.app/data/options/

[^16]: https://www.marketdata.app

[^17]: https://www.marketdata.app/api/

[^18]: https://workspace.google.com/marketplace/app/market_data/453586334945

[^19]: https://www.quantconnect.com/forum/discussion/5549/is-there-an-api-for-iv-rank/

[^20]: https://www.quantconnect.com/docs/v2/research-environment/datasets/equity-options/individual-contracts

[^21]: https://orats.com/data-api

[^22]: https://www.insightbig.com/post/best-options-data-apis-to-be-aware-of

[^23]: https://www.ivolatility.com/data-cloud-api/

[^24]: https://www.ivolatility.com/news/2961

[^25]: https://www.ivolatility.com/historical-options-data/

[^26]: https://www.barchart.com/options/iv-rank-percentile

[^27]: https://unusualwhales.com/information/iv-rank

[^28]: https://www.optionstrategist.com/calculators/free-volatility-data

[^29]: https://www.alphaquery.com/stock/API/volatility-option-statistics/30-day/iv-mean

[^30]: https://optioncharts.io/options/API/volatility-skew

[^31]: https://www.ivolatility.com/ivollive-options/

[^32]: https://marketchameleon.com/Overview/APG/IV/

[^33]: https://marketchameleon.com/Overview/API/IV/

[^34]: https://www.ivolatility.com

[^35]: https://www.quantvps.com/blog/best-apis-for-historical-options-market-data-volatility

[^36]: https://data.nasdaq.com/databases/VOL

[^37]: https://polygon.io/docs/rest/options/overview

[^38]: https://github.com/schepal/yahoo_vol

[^39]: https://www.youtube.com/watch?v=0xOHf3UfZdY

[^40]: https://www.optionistics.com/quotes/option-prices

[^41]: https://finance.yahoo.com/news/implied-volatility-works-options-trading-200000624.html

[^42]: https://www.youtube.com/watch?v=Hrs9CWb92_g

[^43]: https://www.codearmo.com/python-tutorial/options-trading-getting-options-data-yahoo-finance

[^44]: https://www.reddit.com/r/algotrading/comments/1c35864/how_to_get_options_data_from_polygon/

[^45]: https://www.thetadata.net

[^46]: https://www.reddit.com/r/options/comments/18xsy68/implied_vol_yahoo_finance/

[^47]: https://finance.yahoo.com/news/options-market-predicting-spike-api-193700027.html

[^48]: https://polygon.io/docs

[^49]: https://finance.yahoo.com/markets/options/highest-implied-volatility/

[^50]: https://www.quantconnect.com/docs/v2/lean-cli/datasets/theta-data

[^51]: https://docs.dolthub.com/products/dolthub

[^52]: https://www.reddit.com/r/algotrading/comments/ly4ifj/where_to_get_cheap_preferably_free_options_data/

[^53]: https://docs.dolthub.com/products/dolthub/api

[^54]: https://www.quantconnect.com/docs/v1/research/historical-data

[^55]: https://www.dolthub.com/blog/2024-04-11-doltlab-installer/

[^56]: https://www.quantconnect.com/forum/discussion/16236/how-can-i-access-minute-level-historical-spy-option-data/

[^57]: https://www.dolthub.com/repositories/dolthub/options

[^58]: https://www.quantconnect.com/forum/discussion/5123/options-historical-data/

[^59]: https://www.dolthub.com/discover

[^60]: https://docs.tradier.com

[^61]: https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/history-requests

[^62]: https://github.com/dolthub/dolt

[^63]: https://tradier.com/platforms/hoadley

[^64]: https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data

[^65]: https://public.com/api/docs/resources/option-details/get-option-greeks

[^66]: https://dev.to/williamsmithh/top-5-free-financial-data-apis-for-building-a-powerful-stock-portfolio-tracker-4dhj

[^67]: https://www.youtube.com/watch?v=-73cAS1jCVI

[^68]: https://www.reddit.com/r/webdev/comments/151zk8y/is_there_any_free_stock_market_api_that_allows/

[^69]: https://interactivebrokers.github.io/tws-api/option_computations.html

[^70]: https://www.alphavantage.co

[^71]: https://www.ivolatility.com/api/docs

[^72]: https://www.postman.com/ivolatility/ivolatility-data-cloud-api/collection/l51togg/ivolatility-data-api

[^73]: https://polygon.io/blog/greeks-and-implied-volatility/

