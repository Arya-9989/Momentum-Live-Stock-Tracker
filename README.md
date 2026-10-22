# Momentum

[![CI](https://github.com/Arya-9989/Momentum-Live-Stock-Tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Arya-9989/Momentum-Live-Stock-Tracker/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/hero-dark.svg">
  <img src="docs/hero-light.svg" alt="Momentum: every market, one calm view. An illustrative area-line chart beside a segmented range control." width="100%">
</picture>

A fast, mobile-friendly Streamlit dashboard for global stocks, indices, ETFs, FX and crypto.
Type any Yahoo Finance symbol (`AAPL`, `LT.NS`, `VOD.L`, `^GSPC`, `BTC-USD`) to get an
area-line or candlestick chart with technical indicators, key statistics, a company profile,
multi-symbol comparison and a watchlist. All of it is backed by a typed, tested data layer that
degrades gracefully when Yahoo misbehaves.

![Overview, light theme: price header, area chart with SMA overlays, and the watchlist rail](docs/screenshots/overview-light.png)

<table>
  <tr>
    <td width="66%"><img src="docs/screenshots/overview-dark.png" alt="Overview, dark theme"></td>
    <td width="34%"><img src="docs/screenshots/mobile.png" alt="Overview on a phone"></td>
  </tr>
</table>

| Compare | Watchlist |
| --- | --- |
| ![Relative performance, each line labelled at its end](docs/screenshots/compare.png) | ![Watchlist](docs/screenshots/watchlist.png) |

![One crosshair through the price, volume, RSI and MACD panels, with a readout listing every series](docs/screenshots/crosshair.png)

<sub>Screenshots show live Yahoo Finance data (the 22 Oct 2026 US session), captured from the
Docker image with <code>scripts/capture_screenshots.py</code> on 23 Oct 2026. The hero image above
is an illustration and shows no market data.</sub>

## Features

- **Any Yahoo symbol, correctly labelled.** Prices carry their own currency (`$`, `₹`, `€`, `£`,
  `CN¥` and others). London, Johannesburg and Tel Aviv listings are shown in the minor unit the
  exchange quotes (`120.50p`, `67,899c`, `12,410 ag`), with market cap in the major currency.
  Index levels are points and FX rates are plain ratios to four places (`EUR/USD 1.1222`), not
  dollars. A move that rounds to zero reads `0.00`, never `-0.00`. The header shows exchange,
  instrument type and market state (open, pre-market, after hours or closed).
- **Chart first.** An area-gradient line by default, or candlesticks, with volume, for ten
  ranges (1D to Max) and the bar intervals Yahoo actually supports for each range (for example,
  1-minute bars only for 1D). The line is green or red by the move over the range; on 1D it is
  measured against the previous close, which is drawn as a dotted reference line. One crosshair
  runs through the price, volume, RSI and MACD panels, and one readout lists every series at the
  hovered bar, value first. Weekends, exchange holidays and overnight gaps take no space on the
  axis, and times are shown in the exchange's local time. Wheel or pinch zooms the time axis, and a
  double-click fits it again. Click the prices and the chart offers **"Set an alert at $X"**,
  which opens the Alerts tab with that level filled in. The charts are
  [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) (Apache-2.0),
  built into a Streamlit component ([ADR 0005](docs/adr/0005-lightweight-charts.md)).
- **Technical indicators:** SMA 20/50/200, EMA 12/26, Bollinger Bands (20, 2), RSI (14, Wilder)
  and MACD (12, 26, 9), computed as StockCharts defines them (EMAs seeded with an SMA). Daily,
  weekly and monthly charts fetch at least 250 bars before the visible range, so even SMA 200 is
  defined at the left edge. Intraday ranges and Max have no earlier history to fetch, so their
  indicators start once enough bars exist.
- **KPI cards** for the range return, market cap, P/E (TTM), volume vs. 3-month average and
  annualised volatility. Indices, FX, crypto, ETFs and futures get day high/low and max drawdown
  instead.
- **Key statistics** with day and 52-week range bars, plus a company profile (sector, industry,
  employees, website, business summary).
- **Fundamentals** for the symbol in view: revenue, gross and operating income, net income and
  diluted EPS, plus total assets, liabilities, equity, cash and debt, for the last four years or
  five quarters. Gross and net margins are derived, along with the dividend history, trailing
  twelve-month dividend and yield. Figures are shown in the currency the company *reports* in,
  which can differ from the one it trades in (TSMC reports in TWD, Vodafone in EUR), and the page
  says which. Lines a company doesn't report read "—", and indices, FX and crypto get one sentence
  instead of an empty table. Statements are cached for a day.
- **Compare** up to six symbols as cumulative % return, each rebased to 0% at its first bar in
  the range, with up to four lines labelled at their end, and return, volatility and
  max-drawdown columns.
- **Watchlist rail** beside the chart: each symbol with its name, a 1-month sparkline, price
  and a day-change badge. Click a row (or tab to it and press Enter; the arrow keys move between
  rows) to open it. The
  **Watchlist** tab holds up to twelve symbols in a full table with 52-week position and range,
  market cap and P/E; select a row there to open that symbol.
- **A saved watchlist.** Edits are saved as you make them and come back on your next visit,
  with no account needed: `serve.py` gives each browser a signed, HttpOnly device cookie, and the
  list is stored in SQLite (or Postgres). With OIDC sign-in configured, the list follows the
  account instead, across devices. Symbols are validated before they are saved, a symbol the data
  source does not know is refused with a reason, a list holds at most twelve, and two tabs editing
  at once never undo each other. Under plain `streamlit run` the list is kept for the session, and
  the Watchlist tab says so.
- **Price and indicator alerts.** Price crosses above or below a level, day change beyond ±x%,
  RSI (14) crossing a level, and SMA 20/50 or 50/200 crossovers. A background loop checks them
  every minute, even with the page closed. Each alert fires once per cross, and re-arms only after
  the value moves back past a hysteresis band, with a cooldown on top, so a price wobbling around
  the level does not cause an alert storm. Notifications appear in the app (a toast on any open
  page, plus a list in the Alerts tab). Optionally they also go by email, to an identity provider's
  verified address only, or to a webhook signed with HMAC-SHA256. See
  [ADR 0002](docs/adr/0002-alerts.md) and [Alerts](#alerts).
- **Portfolio.** Record buys, sells and splits (fractional shares too) and see each holding's
  shares, average cost, value, day change, and unrealized and realized P&L, with the portfolio's
  value as the headline. Cost basis is **first in, first out** (FIFO), with fees counted in. A
  back-dated buy re-matches later sales, and a sale or delete that would overdraw a holding is
  refused. Every figure stays in the instrument's own currency (London prices are entered in pence
  and valued in pounds), and totals are per currency. They can be converted at today's rate on
  request, labelled as such. Arithmetic is exact `Decimal`. FIFO is what US brokers default to and
  what Indian rules apply; UK pooling and Canadian ACB use average cost, so realized figures there
  will differ ([ADR 0003](docs/adr/0003-portfolio-cost-basis.md)).
- **CSV export** of the visible prices plus indicators, the comparison, the watchlist, and the
  portfolio's holdings and transactions, at full precision.
- **A second data provider (optional).** With your own Alpha Vantage key, Momentum falls back to
  Alpha Vantage when Yahoo Finance is down or rate-limiting. It covers history, quotes and
  fundamentals for US and other covered listings, behind the same `MarketDataProvider`
  interface, so every view works unchanged. A client-side limiter and a daily budget keep within
  the free tier, and a provider that rate-limits is skipped for a cool-down. It is off by
  default; see [Second data provider](#second-data-provider-optional) and
  [ADR 0004](docs/adr/0004-second-data-provider.md).
- **Designed failure states.** An unknown symbol, a network outage or a Yahoo rate limit each
  get a short headline and one next step in place of the quote ("Can't reach Yahoo Finance."),
  and Compare, the Watchlist and the rail say it once instead of on every row. Network and
  rate-limit failures are never cached, so the next interaction retries.
- **Shareable URLs.** Symbol, range, tab and demo mode live in the query string, e.g.
  `?ticker=LT.NS&range=1Y&view=Compare`.
- **Light and dark themes**, each designed for its surface. They follow the OS setting and can be
  switched (System, Light, Dark) from the app menu; charts are redrawn for the active theme. The
  layout reflows for tablets and phones.
- **Demo mode** with deterministic synthetic data, for offline use and tests. It is clearly
  labelled in the UI whenever it is on.

## Design

The interface follows Apple Stocks: neutral greys with the numbers as the hero, and market
green/red reserved for price state (the price line, candles, change badges and day change).
Indicator and comparison lines use a six-colour categorical palette, validated for colour-blind
separation on both surfaces, that leaves out the market green and red.

- **Type:** Inter with its optical-size axis. Prices, statistics and tables use tabular figures,
  so digits line up and do not jitter when they update.
- **Colour:** tokens live in `src/momentum/theme.py` and `src/momentum/ui/styles.css`. Every text
  colour meets WCAG AA on the surfaces it is used on; `tests/test_theme.py` checks each ratio.
- **Stable layout:** Streamlit loads each element's code lazily, so everything above the fold,
  the top bar included, sits in a fixed-height slot with a skeleton of the same size, and nothing
  moves when data arrives (or when an element fails to load). Measured CLS is at most 0.003 on
  desktop and 0.01 on phones through `serve.py`.
- **Motion:** three quiet transitions, each marking a change of state. On a new symbol or range,
  only the characters of the price, day change and KPI figures that changed roll (up when the
  value rose, down when it fell), and each ends on exactly the text a static render shows; there
  is no count through made-up numbers. The watchlist's selection slides to the row you pick as
  you click it. When the chart shows something new (a symbol, range, interval or style), its axes
  stay still while the series sweep in along the time axis: a curtain over the plot area scales
  away, so only a transform moves. Only transform, opacity and clip-path animate. With
  `prefers-reduced-motion` the figures and the selection change at once, and the chart appears
  without the sweep.
- **Accessibility:** one 2px focus ring everywhere. Every target is at least 24 px (WCAG 2.5.8);
  on touch screens the Overview controls are 44 px or more (the range control scrolls sideways
  rather than shrink its segments), while Streamlit's multiselect chips, the data grid's toolbar
  and the company's website link stay between 24 and 28 px. Rail rows are keyboard-reachable,
  keep focus across reruns and have spoken labels ("AAPL, Apple Inc., $342.87, down 0.33%
  today"). Each chart has a text alternative and a table: the price data behind the price chart,
  and per-symbol returns under Compare.

## Architecture

```mermaid
flowchart LR
    user([Browser]) <--> serve[serve.py<br/>st.App + momentum.server<br/>gzip, robots.txt, head tags]
    serve --> entry[streamlit_app.py]
    entry --> app[momentum.app<br/>top bar, rail + page layout]
    app --> views[momentum.ui.views<br/>overview / compare / watchlist]
    views --> kpis[ui.kpis<br/>pure KPI + stats models]
    views --> messages[ui.messages<br/>error and empty-state copy]
    views --> components[ui.components<br/>HTML-escaped widgets, rail component]
    views --> rail[ui.rail<br/>pure watchlist-rail model]
    charts --> theme[momentum.theme<br/>light + dark tokens]
    views --> charts[momentum.charts<br/>chart payloads, pure]
    views --> chartc[ui.chart_component<br/>Lightweight Charts component<br/>built from frontend/chart]
    views --> indicators[momentum.indicators<br/>SMA, EMA, RSI, MACD, BB]
    views --> cache
    subgraph data [momentum.data]
        cache[cached_* wrappers<br/>st.cache_data TTLs] --> loaders[load_* + parsing<br/>Quote, Profile, clean_history]
        loaders --> provider{MarketDataProvider}
    end
    provider --> yahoo[YahooProvider<br/>yfinance]
    provider --> demo[DemoProvider<br/>synthetic, offline]
    yahoo --> yf[(Yahoo Finance)]
```

- `data`: providers translate every upstream failure (unknown symbol, rate limit, network error)
  into a small `DataError` hierarchy whose messages are safe to show to users. Parsing is pure.
  Batch loaders fetch in parallel and isolate per-symbol failures.
- `indicators`, `charts`, `theme`, `formatting`, `ui.kpis`, `ui.rail` and `ui.messages` are pure
  functions of DataFrames and dataclasses, with no Streamlit and no I/O, so they are unit-tested
  directly.
- `ui` is the only layer that calls Streamlit widgets; `server` is plain ASGI.

## Quickstart

### Docker

```bash
docker build -t momentum .
docker run --rm -p 8501:8501 momentum
# offline / synthetic data:
docker run --rm -p 8501:8501 -e MOMENTUM_DEMO=1 momentum
```

To keep saved watchlists across container replacements, mount a volume on `/data`:
`docker run --rm -p 8501:8501 -v momentum-data:/data momentum`.

Open <http://localhost:8501>. The image is multi-stage, runs as a non-root user, has a
`HEALTHCHECK` on Streamlit's `/_stcore/health` endpoint, and serves the app through `serve.py`
(see [Performance](#performance)).

### Local (uv)

```bash
uv sync                      # Python 3.12 + locked dependencies + dev tools
uv run streamlit run streamlit_app.py   # development
uv run streamlit run serve.py           # as in production: gzip, robots.txt, head tags
```

### Streamlit Community Cloud

1. Fork this repository and click **Create app** on [share.streamlit.io](https://share.streamlit.io).
2. Set **Main file path** to `streamlit_app.py`, and choose Python 3.12 under *Advanced settings*.
3. Deploy. Community Cloud installs from the first dependency file it finds, and `uv.lock` comes
   before `requirements.txt`, so it installs the locked versions from `uv.lock`
   (`requirements.txt` is exported from the same lock, checked in CI, for pip-based hosts). The
   theme comes from `.streamlit/config.toml`. The entrypoint adds `src/` to the import path when
   the package itself is not installed, so no extra setup is needed. `serve.py` is used by the
   Docker image and has not been tested on Community Cloud, so point Cloud at `streamlit_app.py`.

## Performance

Lighthouse 13.5 scores for the Docker image on a local machine (median of five runs, default
simulated throttling, desktop and mobile presets), next to a bare `st.write("hi")` app in the
same image:

| Page | Performance (desktop / mobile) | Accessibility | Best practices | SEO |
| --- | --- | --- | --- | --- |
| Bare Streamlit, plain `streamlit run` | 73 / 55 | 100 | 100 | 82 |
| Bare Streamlit through `serve.py` | 95 / 67 | 100 | 100 | 100 |
| Overview | 88 / 42 | 100 | 100 | 100 |
| Compare | 87 / 42 | 100 | 100 | 100 |
| Watchlist | 94 / 65 | 100 | 100 | 100 |

Before `serve.py` the three views scored 60 / 32, 60 / 30 and 71 / 53, with SEO 82.

The motion described under Design costs no score: with the image before and after it measured
side by side (runs interleaved, median of five, 23 Oct 2026), every view stays within one point
on desktop and mobile, the Overview's largest contentful paint is still the hero price, and the
median CLS is at most 0.005.

`serve.py` runs the app through `st.App` with `momentum.server` in front of it:

- **Compression.** Streamlit serves its frontend bundle uncompressed. The image build writes a
  brotli copy of every asset (20.9 MB to 5.1 MB), served to browsers that accept it. Other
  browsers get gzip, compressed once per process and cached. Plotly's chunk goes from 4.6 MB to
  1.1 MB.
- **Document.** It sets a real title and a meta description, drops a preload of a font the theme
  never uses (166 KB), and adds one critical CSS rule. That rule removes the shift as
  Streamlit's initial narrow shell turns wide (desktop CLS 0.02–0.047 down to at most 0.003).
- **robots.txt.** Streamlit answers `/robots.txt` with its HTML page; `serve.py` returns a real
  one.

What remains belongs to the framework, as the bare-app rows show:

- Streamlit renders entirely in the browser, so nothing paints until its entry bundle has run and
  the websocket session has started. On Lighthouse's mobile profile (slow 4G, 4x CPU slowdown)
  that takes about 5.3 s even for the bare app, which caps it at 67.
- On Overview and Compare almost all of the remaining blocking time is one 200–260 ms task in
  Plotly's chunk, which loads and draws the chart. The Watchlist has no Plotly chart and scores
  within two points of the bare app.

## Configuration

| Setting | Where | Default | Effect |
| --- | --- | --- | --- |
| `MOMENTUM_DEMO` | env var | `0` | `1`/`true` starts with synthetic demo data |
| `?demo=true` | URL | off | Same, per session (also the Demo data toggle in the rail) |
| `?ticker=`, `?range=`, `?view=` | URL | `AAPL`, `6M`, `Overview` | Deep links |
| Theme, server options | `.streamlit/config.toml` | | Light/dark palettes, Inter, headless server |
| Cache TTLs, defaults | `src/momentum/config.py` | quotes 60 s, intraday 60 s, daily 15 min, profile 24 h | |
| `MOMENTUM_DATA_DIR` | env var | `./.momentum` (`/data` in the image) | SQLite database and the generated signing key |
| `MOMENTUM_DATABASE_URL` | env var | SQLite in the data directory | `postgresql://user:pass@host/db` to use Postgres (`uv sync --extra postgres`; the image includes it) |
| `MOMENTUM_SECRET_KEY` | env var | generated into `secret.key` | 32+ characters; signs device cookies. Set it when running several replicas |
| `MOMENTUM_SECRET_KEY_PREVIOUS` | env var | unset | The old key during a rotation; its cookies are accepted and re-signed |
| `MOMENTUM_COOKIE_SECURE` | env var | `auto` (Secure on HTTPS) | `1` behind a TLS-terminating proxy |
| `MOMENTUM_DEVICE_RETENTION_DAYS` | env var | `400` | Anonymous watchlists not seen for this long are deleted |
| `MOMENTUM_OIDC_*` | env var | unset (sign-in off) | OIDC sign-in, see [Sign-in](#sign-in-optional) |
| `MOMENTUM_ALERT_WORKER` | env var | `auto` | `auto` runs the alert loop in the app process; `external` leaves it to `python -m momentum.alerts run`; `off` disables it |
| `MOMENTUM_ALERT_INTERVAL_S` | env var | `60` | Seconds between alert checks (minimum 10) |
| `MOMENTUM_SMTP_HOST`, `_PORT`, `_FROM`, `_STARTTLS`, `_SSL`, `_USERNAME`, `_PASSWORD` | env vars | unset (email off) | SMTP for email alerts |
| `MOMENTUM_WEBHOOK_ALLOW_PRIVATE` | env var | `0` | `1` allows `http://` and private addresses for webhooks (local development only) |
| `MOMENTUM_PROVIDERS` | env var | `yahoo` | Data providers in fallback order, e.g. `yahoo,alphavantage` |
| `ALPHAVANTAGE_API_KEY` | env var | unset | Your Alpha Vantage key; Alpha Vantage is used only when it is listed and this is set |
| `ALPHAVANTAGE_DAILY_BUDGET` | env var | `25` | Requests per UTC day before Momentum stops calling Alpha Vantage (the free tier's limit) |

Any `STREAMLIT_*` environment variable (for example `STREAMLIT_SERVER_PORT`) works as usual.
No API keys or secrets are required.

### Sign-in (optional)

Without sign-in, a watchlist belongs to a browser. To let people keep it across devices, configure
any OpenID Connect provider for `st.login`, either in `.streamlit/secrets.toml` (an `[auth]`
table, as in the [Streamlit docs](https://docs.streamlit.io/develop/api-reference/user/st.login))
or, for containers, with environment variables that `serve.py` turns into the same settings:

```bash
MOMENTUM_OIDC_SERVER_METADATA_URL=https://idp.example.com/realms/momentum/.well-known/openid-configuration
MOMENTUM_OIDC_CLIENT_ID=momentum
MOMENTUM_OIDC_CLIENT_SECRET=...
MOMENTUM_OIDC_REDIRECT_URI=https://momentum.example.com/oauth2callback
MOMENTUM_SECRET_KEY=...            # 32+ characters; the session-cookie secret is derived from it
```

A signed-in list is keyed by the identity provider's issuer and subject, not the email address.
The design and its trade-offs are in [ADR 0001](docs/adr/0001-watchlist-identity-and-storage.md).

## Second data provider (optional)

Yahoo Finance is the default and needs no key. To fall back to Alpha Vantage when Yahoo fails,
claim a free key at <https://www.alphavantage.co/support/#api-key> and put this in `.env`
(or the container's environment):

```bash
MOMENTUM_PROVIDERS=yahoo,alphavantage
ALPHAVANTAGE_API_KEY=your-key-here
# ALPHAVANTAGE_DAILY_BUDGET=25        # raise it if you have a paid plan
```

- **Order and failover.** Providers are tried in the order listed. A network failure, a rate
  limit, or a symbol the provider doesn't cover moves on to the next one. "No such symbol" from a
  provider that covers it stops the chain, so typos don't spend the second provider's quota.
- **Coverage.** Alpha Vantage covers US listings and the exchanges it documents (London `.L`,
  Toronto `.TO`/`.V`, XETRA `.DE`, BSE `.BO`, Shanghai `.SS`, Shenzhen `.SZ`). Indices, FX, crypto
  and NSE (`.NS`) listings keep coming from Yahoo.
- **Adjustment.** Its free daily prices are not dividend-adjusted, unlike Yahoo's. Bars after the
  last dividend match to the cent (the tests check this on recorded data); older bars sit above
  Yahoo's by the dividends paid since.
- **Rate limits.**
  - The free tier allows 25 requests a day. Momentum spaces requests by 1.1 s, stops calling once
    the day's budget is spent, and caches each company overview for a day, so a quote costs one
    request.
  - That is enough to keep a handful of symbols alive through a Yahoo outage, not to replace
    Yahoo.
- **Tests and the key.** The tests replay responses recorded with Alpha Vantage's public `demo`
  key (`scripts/record_alphavantage.py`), so CI never calls the live API, and the key never
  appears in logs or error messages.

## Alerts

Alerts live in the **Alerts** tab and need a saved identity (a device cookie or a sign-in, as for
the watchlist). The loop runs inside the app by default. To run it as its own process instead:

```bash
MOMENTUM_ALERT_WORKER=external uv run streamlit run serve.py   # the app, without the loop
uv run python -m momentum.alerts run                           # the loop (stops cleanly on SIGTERM)
uv run python -m momentum.alerts once                          # one cycle, e.g. from cron
```

Several loops at once are safe: each state change is a compare-and-set in the database, so an
alert notifies once whichever loop sees the cross first.

**Email** goes only to an address the identity provider has verified, so it needs
[sign-in](#sign-in-optional) plus an SMTP server. Locally, Mailpit catches everything:

```bash
docker compose -p roadmap-momentum up -d mailpit      # SMTP on 5422, inbox at http://127.0.0.1:5423
MOMENTUM_SMTP_HOST=127.0.0.1 MOMENTUM_SMTP_PORT=5422 MOMENTUM_SMTP_STARTTLS=0 MOMENTUM_SMTP_FROM="Momentum <alerts@momentum.test>" uv run streamlit run serve.py
```

**Webhooks** receive a JSON `POST` for each alert that has Webhook ticked. Add the URL under
*Webhook delivery* in the Alerts tab; the signing secret is shown once.

- Only `https` URLs that resolve to public addresses are accepted, and the request goes to the
  address that was checked.
- Redirects are not followed and requests time out after 5 s.
- Failed deliveries are retried with backoff, up to six attempts.
- Every request carries `Momentum-Event-Id` (for de-duplication) and
  `Momentum-Signature: t=<unix time>,v1=<hex>`.

To verify a request:

```python
import hashlib, hmac, time


def verify(secret: str, header: str, body: bytes, tolerance_s: int = 300) -> bool:
    fields = dict(part.split("=", 1) for part in header.split(","))
    timestamp, signature = int(fields["t"]), fields["v1"]
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    fresh = abs(time.time() - timestamp) <= tolerance_s
    return fresh and hmac.compare_digest(expected.hexdigest(), signature)
```

To see an alert fire without waiting for the market, create one in the Alerts tab (for example
AAPL, price crosses above 250, in demo mode) and replay prices into it:

```bash
uv run python -m momentum.alerts replay AAPL 240 251    # 240 arms it, 251 crosses
```

## Testing and quality

```bash
uv run pytest --cov          # unit, AppTest and browser tests, no network access
uv run playwright install chromium   # once, for the browser tests (skipped without it)
uv run ruff check . && uv run ruff format --check .
uv run mypy                  # strict mode
```

- **Indicators** are checked against published reference values: RSI reproduces the StockCharts
  ChartSchool worksheet to two decimal places and the EMA reproduces its 10-day EMA worksheet to
  within 0.01; MACD and Bollinger Bands are checked against hand-computed values, and every
  daily-or-longer range and interval the UI offers is checked for at least 250 bars of warm-up.
- **Data layer** tests replace `yfinance.Ticker` with fakes covering empty frames, `Infinity`
  values, rate limits, connection errors, timezone handling, partial batch failures and the
  rule that transient failures are never cached (batch results drop their cache entry).
- **Formatting** tests cover currencies, minor-unit listings (pence, cents, agorot), indices, FX
  rates, values that round to zero, compact numbers, missing values and lossless CSV export.
- **App smoke tests** run the real script with `streamlit.testing.v1.AppTest` (demo data, network
  stubbed to fail), covering each tab, URL deep links, invalid and unknown symbols, and the
  network-failure and rate-limit states on every view.
- **Server** tests drive `momentum.server` as plain ASGI: gzip and its cache, pass-through for
  HEAD, Range and media requests, the rewritten document head, robots.txt, and that
  `streamlit run serve.py` detects the app.
- **Chart** tests cover the payloads (panes, overlays, bands, line colour by direction, the
  previous-close reference, exchange-local times, plain JSON with no NaN), and, in Chromium, the
  built bundle itself: the readout, the sweep and its replay on a new key, reduced motion, the
  "Set an alert at" offer and the event it sends, the TradingView attribution link, and a rebuild
  on a style change. A browser end-to-end test runs the chart-to-alert flow in the live app.
- **UI** tests cover stable series colours, the rail model and sparkline geometry, header markup and
  escaping, the number roll's markup, the KPI payload, and the WCAG contrast of every text colour.
- **Motion** tests check that the stylesheets animate only transform, opacity and clip-path and
  that the chart sweep exists only without a reduced-motion preference. In Chromium (Playwright)
  they render the quote header and mount the KPI tiles and rail the way Streamlit does: a roll
  always ends on the exact formatted string, only changed digits move, nothing beside a rolling
  figure moves, reduced motion shows the final value at once, and the rail's indicator lands on
  the selected row.

CI (`.github/workflows/ci.yml`) runs:

- lint, format check, mypy and tests with coverage, with a Postgres and a Mailpit service for the
  storage and email tests;
- a check that `requirements.txt` matches `uv.lock`;
- a chart job that typechecks the component, rebuilds it and fails if the committed bundle
  differs;
- a Docker build whose container must report healthy, run as non-root, render the app (AppTest
  inside the image, with the chart served from the package's asset directory), migrate its SQLite
  database, set the signed device cookie, and serve robots.txt, the real title and a gzipped
  bundle.

To refresh the README screenshots, start the app and run
`uv run --group screenshots python scripts/capture_screenshots.py` (add `--demo` for synthetic data).
The hero images come from `uv run python scripts/make_hero.py`.

## Project structure

```text
.
├── streamlit_app.py           # thin entrypoint (local, Streamlit Cloud)
├── serve.py                   # production entrypoint: st.App + momentum.server (Docker)
├── src/momentum/
│   ├── app.py                 # page composition and sidebar
│   ├── config.py              # ranges, valid intervals, warm-up, TTLs
│   ├── data.py                # providers, parsing, loaders, cached API
│   ├── demo.py                # deterministic synthetic provider
│   ├── indicators.py          # SMA, EMA, RSI, MACD, Bollinger, returns, drawdown
│   ├── charts.py              # chart payloads (price + indicator panes, comparison)
│   ├── fundamentals.py        # statements and dividends: model, Yahoo parsing, cache
│   ├── portfolio.py           # FIFO lots, P&L and per-currency totals (pure)
│   ├── alphavantage.py        # second provider; providers.py is the fallback chain
│   ├── identity.py            # accounts, signed device cookies, the cookie middleware
│   ├── settings.py            # environment configuration
│   ├── store/                 # SQLite/Postgres, migrations, watchlist/alert/portfolio repositories
│   ├── alerts/                # rules, evaluation, delivery, the worker loop and its CLI
│   ├── pyproject.toml         # Streamlit component manifest (the chart's asset_dir)
│   ├── theme.py               # light and dark design tokens
│   ├── formatting.py          # currency-aware number formatting
│   ├── server.py              # gzip, robots.txt and head tags around Streamlit (ASGI)
│   └── ui/                    # views, components, KPI/rail/message models, styles.css, rail.js/css,
│                              # chart/ (the built chart bundle and its third-party notices)
├── frontend/chart/            # the chart component's TypeScript source and esbuild script
├── tests/                     # pytest suite (no network)
├── scripts/                   # capture_screenshots.py, make_hero.py
├── docs/                      # adr/, hero-light/dark.svg, screenshots/
├── deploy/keycloak/           # local OIDC realm for sign-in (compose profile "oidc")
├── compose.yaml               # Postgres, Mailpit and Keycloak for development and tests
├── .streamlit/config.toml     # light + dark theme
├── Dockerfile, .dockerignore
├── pyproject.toml, uv.lock, requirements.txt
└── .github/workflows/ci.yml
```

## Design decisions

- **A provider seam instead of calling yfinance everywhere.** The UI depends on
  `MarketDataProvider`, so demo data, tests and a future paid API are drop-in replacements.
- **Errors are values at the boundary.** yfinance raises a mix of its own, `curl_cffi` and
  built-in exceptions. They are translated once into `InvalidSymbolError`, `NoDataError` (with
  `EmptyRangeError` for a window with no bars) or `DataSourceError` (with `RateLimitError`), and
  `ui.messages` turns each into a short headline and next step; raw exception text never reaches
  the page.
- **Cache successes only.** `st.cache_data` does not memoise exceptions, so a transient outage is
  retried on the next interaction instead of being pinned for the TTL. Batch loaders return
  per-symbol failures as values, so their wrappers drop the cache entry when any failure is
  transient. TTLs are shorter for intraday bars than for daily bars, and company profiles keep
  for a day.
- **Indicator warm-up.** Showing SMA 200 on a 6-month chart needs 200 bars before the first
  visible one. Daily-and-longer ranges fetch the shortest Yahoo period that covers the range plus
  250 bars at the chosen interval, compute indicators on it and trim the result. Intraday ranges
  don't, because Yahoo caps intraday look-back.
- **Exchange-local time.** Bars are converted to naive exchange-local timestamps, so a 09:15 NSE
  open reads 09:15 for every viewer, and overnight gaps can be removed from the axis.
- **Wilder RSI, SMA-seeded EMAs and population-σ Bollinger Bands**, matching what charting
  platforms display rather than a naive rolling mean, a first-value seed or a sample standard
  deviation.
- **A thin ASGI layer instead of a reverse proxy.** Streamlit's own `st.App` takes routes and
  middleware, so compression, robots.txt and the document head live in tested Python in the same
  process, with no second server to configure or supervise.
- **Escaping.** All Yahoo-provided text (names, summaries, URLs) is HTML-escaped before it goes
  into custom markup.

## Data source and disclaimer

Market data comes from Yahoo Finance through [yfinance](https://github.com/ranaroussi/yfinance), an
**unofficial**, community-maintained library that is not affiliated with or endorsed by Yahoo.
If you configure it, [Alpha Vantage](https://www.alphavantage.co/) serves as a fallback under your
own key and its terms.
Data may be delayed, incomplete or briefly unavailable, and Yahoo can rate-limit requests. Use is
subject to Yahoo's terms. This project is for education and personal use. **It is not financial
advice.**

## License

[MIT](LICENSE) © Arya Mahindrakar
