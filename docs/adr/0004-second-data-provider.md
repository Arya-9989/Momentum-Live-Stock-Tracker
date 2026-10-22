# 0004: A second market data provider and a fallback chain

- Status: accepted
- Date: 2026-10-23

## Context

Everything came from Yahoo Finance through yfinance, an unofficial library that breaks when Yahoo
changes its site and that Yahoo rate-limits without warning. A second provider behind the
existing `MarketDataProvider` interface gives the app somewhere to turn when Yahoo fails. It
also proves that the interface really is provider-neutral.

Constraints:

- The owner supplies the key, and it is off by default. CI never calls a live API: tests replay
  recorded HTTP responses.
- Free tiers are small. Alpha Vantage allows 25 requests a day, and its demo key 2 a second.
- The UI and the parsing layer must see identical, normalised data whichever provider answered.

## Options considered

1. **Finnhub (free).** Quotes and company profiles, but historical candles became a paid
   endpoint, so it cannot draw a chart. Rejected.
2. **Alpha Vantage (free).** Daily, weekly, monthly and intraday bars, a global quote, a company
   overview, income statement, balance sheet and dividends. It publishes a `demo` key that works
   for IBM, so real responses could be recorded for the tests without signing up.
   Rejected as the only provider (25 requests a day), accepted as the second.
3. **A paid feed (Polygon, Tiingo).** Better limits, but it needs money and an account. Out of
   scope; the same seam takes it later.

## Decision

**`AlphaVantageProvider`** implements `history`, `info` and `fundamentals`. It returns exactly what
the parsing layer already consumes:

- OHLCV frames shaped like yfinance's.
- `info` mappings with Yahoo's keys (`regularMarketPrice`, `longName`, `marketCap`...). The
  quote merges `GLOBAL_QUOTE` with the cached `OVERVIEW`.
- The `Fundamentals` model the Fundamentals tab uses (`momentum.fundamentals`).

The tests run the same assertions over both providers' normalised output, using recorded
responses for the same symbol and day.

Rules:

- **Symbols.** Yahoo suffixes map to Alpha Vantage's documented ones: `.L`→`.LON`,
  `.TO`→`.TRT`, `.V`→`.TRV`, `.DE`→`.DEX`, `.BO`→`.BSE`, `.SS`→`.SHH`, `.SZ`→`.SHZ`. Indices
  (`^GSPC`), FX (`=X`), crypto (`-USD`) and exchanges it does not document (`.NS`) raise
  `UnsupportedSymbolError`, so the chain moves on.
- **Prices are not adjusted.** The free daily series is raw; Yahoo's is split- and
  dividend-adjusted. Bars after the last dividend match. Older bars differ by the dividend
  adjustment, and the README says so.
- **Rate limits** are handled before and after the call:
  - A client-side limiter spaces requests (1.1 s by default).
  - A daily budget (25 by default, resetting at 00:00 UTC) refuses calls locally once it is
    spent.
  - Throttle replies (HTTP 200 with a `Note` or `Information` that mentions the limit) become
    `RateLimitError`.
  - A premium-only reply becomes `UnsupportedSymbolError`, and an `Error Message` becomes
    `NoDataError`.
- **Network:** 10 s timeouts and two retries with backoff on timeouts, connection errors and
  5xx; never on 4xx or throttling. The key is sent only as a query parameter, and error
  messages and logs never include the URL.
- **Caching inside the provider:** overviews for a day, so a quote costs one request, not two.

**`FallbackProvider`** tries providers in order (`MOMENTUM_PROVIDERS=yahoo,alphavantage`).

- A transient failure (`DataSourceError`, including rate limits) or an unsupported symbol
  moves to the next provider.
- A definite "no such symbol" (`NoDataError`) from a provider that covers the symbol stops the
  chain, so typos don't spend the second provider's quota.
- A provider that rate-limits is skipped for a cool-down (60 s, or until the next UTC day when
  the daily quota is spent).
- When every provider fails, the most informative error is raised: a rate limit or outage before
  "unsupported".

**Configuration:** `MOMENTUM_PROVIDERS` (default `yahoo`) and `ALPHAVANTAGE_API_KEY`.
Alpha Vantage is used only when it is listed *and* a key is set. Demo mode ignores both.

## Consequences

- With the default configuration nothing changes. With a key, a Yahoo outage or throttle no longer
  blanks US and other covered listings.
- The free tier's 25 requests a day cover a handful of symbols as a fallback, not as the primary
  source. The README gives that number rather than implying otherwise.
- The recorded fixtures date from 2026-10-23 and are refreshed with
  `scripts/record_alphavantage.py`.
