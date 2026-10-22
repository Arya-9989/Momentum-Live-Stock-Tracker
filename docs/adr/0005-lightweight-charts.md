# 0005: TradingView Lightweight Charts instead of Plotly

- Status: accepted
- Date: 2026-10-23

## Context

Plotly drew both charts. On Lighthouse's mobile profile, Overview and Compare scored 41 to 65,
against 67 for a bare Streamlit page served the same way (`serve.py`) and 55 for plain
`streamlit run`. Almost all of the gap was Plotly:

- a 4.6 MB chunk (1.1 MB gzipped), loaded on every visit to those views;
- one 200 to 260 ms long task to parse it and draw the figure.

The motion pass then added a sweep-in of the series on every new figure, which has to survive the
change.

## Options considered

1. **Keep Plotly, trim it** (a partial bundle). Streamlit's `st.plotly_chart` loads Streamlit's own
   Plotly build, so the app cannot ship a smaller one through that element. Rejected.
2. **Vega-Lite (`st.altair_chart`).** Lighter than Plotly, but there is no candlestick mark, no
   linked panes with one crosshair, and the readout and zoom would be rebuilt by hand anyway.
   Rejected.
3. **TradingView Lightweight Charts** (Apache-2.0), as a Streamlit v2 custom component. Canvas
   rendering built for financial series: candles, areas, histograms, panes on one time scale with
   one crosshair, wheel and pinch zoom. It is about 60 KB gzipped, including this app's code.

## Decision

Option 3, as a **properly built, packaged component**:

- **Build.** The source is TypeScript in `frontend/chart` (pinned versions and a lockfile),
  bundled by esbuild into `src/momentum/ui/chart/momentum-chart.js` plus `momentum-chart.css`.
  The bundle is committed, and the CI `chart` job typechecks, rebuilds and fails if the result
  differs from the committed bundle.
- **Manifest.** `src/momentum/pyproject.toml` declares the component with
  `asset_dir = "ui/chart"`, so Streamlit serves the bundle as a cacheable, gzipped file from
  `/_stcore/bidi-components/momentum.price_chart/`. Without the manifest, the bundle would be
  inlined into every script run. Where the package is not installed (hosts that only use
  `requirements.txt`), the same files are registered inline as a fallback.
- **Bi-directional.** Python sends a JSON payload built by pure functions in `momentum.charts`,
  and the chart is updated in place while its structure is unchanged. The chart sends one event
  back: after a click on the price pane it offers "Set an alert at $X", and choosing it opens the
  Alerts tab with the form filled in (`views.alert_from_chart`).
- **Feature parity with the Plotly chart:**
  - area line coloured by direction (against the previous close on 1D), or candles;
  - SMA and EMA overlays;
  - Bollinger Bands with a filled band (a small series primitive, since Lightweight Charts has no
    "fill between"; the same primitive draws the RSI 30–70 zone);
  - volume, RSI and MACD panes on one time scale with one crosshair;
  - a readout listing every series at the hovered bar, value first;
  - the previous-close reference line;
  - exchange-local times, with no gaps for weekends, holidays or nights;
  - comparison lines labelled with their symbol and return on the price scale, plus a 0%
    baseline.

  Zoom is now wheel and pinch on the time scale, and a double-click fits the data again. Plotly's
  drag-to-zoom box has no equivalent in Lightweight Charts. That is the one behavioural
  difference.
- **Motion carried over.** Plotly's version was a CSS clip-path sweep of the trace layer on every
  new figure, which a canvas cannot be clipped into.
  - The component covers the plot area (never the axes) with a curtain in the surface colour and
    scales it away with a transform, so the series appear left to right while the axes are there
    at once.
  - It runs on mount and whenever the payload's `key` (symbol, range, interval, style) changes:
    the range-change transition.
  - Under `prefers-reduced-motion` the curtain is never shown. The component's stylesheet has its
    own reduced-motion rule, because it lives in a shadow root.
- **Attribution.** The licence asks for its NOTICE text and a link to tradingview.com on the
  page.
  - The chart keeps Lightweight Charts' attribution logo, which links to tradingview.com.
  - The Overview footnote carries the notice ("Charts: TradingView Lightweight Charts™, © 2026
    TradingView, Inc.").
  - The licence banner stays in the bundle, and `ui/chart/THIRD_PARTY_NOTICES.md` holds the
    licence texts.

Plotly is removed from the dependencies.

## Consequences

- See the README's Performance section for measured Lighthouse scores before and after,
  taken in alternating pairs.
- Charts are canvas, like Plotly's. Their text alternative is the figure's label and the "Price
  data" table, as before.
- The browser tests mount the built bundle in Chromium (`tests/test_chart_browser.py`). They cover
  the readout, the sweep and its replay, reduced motion, the alert offer and its event, the
  attribution link and a rebuild on style change. `tests/e2e/test_chart_e2e.py` runs the same flow
  in the live app.
