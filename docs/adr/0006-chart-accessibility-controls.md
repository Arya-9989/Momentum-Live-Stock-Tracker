# 0006: Accessible chart controls within the existing design

- Status: accepted
- Date: 2026-11-09

## Context

Chart zoom and reset require wheel, pinch or double-click gestures. The price-alert offer
requires a pointer click, has a transparent background over the plot, and is only 32px tall.
Candles distinguish rising and falling prices by colour alone. Portfolio and Fundamentals
segments are also 32px tall on touch screens.

## Decision

Reuse the chart component and existing theme tokens. Reserve 48px inside its existing height
for native zoom/reset buttons and an alert-at-latest-close button. Keep pointer controls and
the Alerts form for editing any price level. Disable the latest-close action when no valid
positive close exists. Escape dismisses a price offer and returns focus to the visible action.

Use hollow rising candles and filled falling candles, retaining the market colours. Give the
price offer a solid themed surface, a focus ring and a 44px minimum height. Increase the two
feature segmented controls to 44px only on touch screens, within their existing row slots.

## Consequences

The chart plot is 48px shorter; the outer chart dimensions and existing motion stay intact.
The price table remains the accessible alternative for historical values. No dependency or
new interaction framework is needed. Browser tests cover keyboard activation, zoom/reset,
alert payloads, missing-price disabling, narrow layout, focus rings and feature touch targets.
