# Earnings vendor fixtures (#20)

Hand-written responses in the documented shape of each vendor's API. They are **not real
earnings dates** and were not downloaded: no test uses a network connection or an API key.
The symbols are real tickers so the parsers see realistic input, but every date is made up.

| File | Shape |
|---|---|
| `finnhub_nvda.json` | `GET /api/v1/calendar/earnings?symbol=NVDA` with one upcoming report |
| `finnhub_amd.json` | same for AMD |
| `finnhub_empty.json` | no report inside the window |
| `alphavantage_calendar.csv` | `function=EARNINGS_CALENDAR&horizon=3month` bulk CSV |
| `alphavantage_rate_limit.json` | the JSON notice returned instead of CSV when the free quota is used up |
