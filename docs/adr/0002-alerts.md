# 0002: Price and indicator alerts

- Status: accepted
- Date: 2026-10-23

## Context

Viewers want to hear when a price or an indicator crosses a level. Getting that right in a
Streamlit app is harder than it looks:

- A Streamlit script runs only while someone has the page open, and it reruns on every
  interaction. An alert must keep being checked when nobody is looking, must not fire again
  because a script reran, and must survive a restart.
- A price that hovers at a level crosses it many times an hour. Without care, every wobble is
  another notification.
- Delivery leaves the process (email, webhooks), so it can fail, be slow, or be abused: a
  webhook URL is a request the server makes on someone else's behalf.

Alerts belong to the owners from ADR 0001 (an account or a device), because they must be
evaluated when the owner's page is closed.

## Options considered

1. **Evaluate in the script run** (check alerts whenever the page reruns). This is simple, but
   nothing is checked while the page is closed, and two open tabs evaluate twice. Rejected.
2. **A scheduler service** (Celery beat, cron, a queue). Robust, but it adds a broker and a second
   deployable to a single-container app. Rejected as the default.
3. **A worker loop with all of its state in the database**, run as a thread in the app process (the
   default) or as its own process (`python -m momentum.alerts run`). Correctness comes from the
   database rather than from there being exactly one worker.

## Decision

Option 3.

**Conditions.** Each alert compares one *metric* of a symbol with a threshold, in one direction:

| Condition | Metric | Default hysteresis |
| --- | --- | --- |
| Price crosses above / below | last price | 0.5% of the threshold |
| Day change rises above / falls below ±x% | % change vs previous close | 0.5 points |
| RSI (14) crosses above / below | Wilder RSI on daily closes (`indicators.rsi`) | 5 points |
| SMA 20 / 50 or SMA 50 / 200 crosses up / down | (SMA fast / SMA slow − 1) × 100, against 0 | 0.25 points |

The SMA crossovers ("golden" and "death" crosses) are threshold crossings of the gap between the
two averages, so one state machine serves every condition.

**State machine** (`alerts.rules.step`, a pure function):

- `pending`: not observed yet. The first observation decides: already past the threshold
  means `fired` *without* notifying, because no cross was seen; otherwise `armed`.
- `armed` → `fired` when the metric reaches the threshold. This notifies, unless the previous
  notification is younger than the cooldown (default 1 hour), in which case the alert moves to
  `fired` silently.
- `fired` → `armed` only after the metric retreats past the threshold by the hysteresis.
  Until then, wobbling around the level does nothing. That is what prevents alert storms.
- A missing value (no data, too little history for RSI or SMA 200) changes nothing.

**Idempotent and safe under concurrency.**

- Each alert stores the timestamp of the observation it last evaluated. Evaluating the same
  observation again (a rerun, a second worker, a restart in the middle of a cycle) is a no-op.
- A transition is a compare-and-set:
  `UPDATE alerts SET state = 'fired', fire_count = fire_count + 1 ... WHERE id = ? AND state = 'armed' AND fire_count = ?`.
  Only the worker whose update matched a row goes on to record the event, so two workers
  (replicas, or the in-app thread plus the CLI) cannot both notify.
- In the same transaction, the event, the in-app notification and one outbox row per external
  channel are written, all with unique keys. A crash leaves either all of them or none.

**Delivery** is an outbox (`alert_deliveries`) drained by the same loop:

- Claiming a row is another compare-and-set with a 60 s lease, so a worker that dies mid-send
  gives the row back.
- Failures retry with exponential backoff (30 s up to 2 h, 6 attempts), then the row is marked
  failed. Delivery is *at least once*, and every message carries the event id for de-duplication
  (`Momentum-Event-Id` for webhooks, a deterministic `Message-ID` for email).
- **In-app:** a notification row. Each open page polls for new rows with
  `st.fragment(run_every=30s)`, shows a toast, and lists recent notifications in the Alerts tab.
- **Email:** SMTP (Mailpit locally). Only to an address the identity provider has marked
  verified (ADR 0001). Anonymous devices cannot enter an address, so the server can never be
  used to mail strangers.
- **Webhook:** one HTTPS endpoint per owner. Each request carries
  `Momentum-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>`, Stripe-style, so the
  receiver can check authenticity and freshness. The secret is random, shown once, and can be
  rotated. SSRF defences:
  - The URL must be `https` with no credentials.
  - Every address the host resolves to must be public (no loopback, private, link-local,
    multicast, reserved or cloud-metadata addresses).
  - The connection goes to the address that was checked (pinned, so a DNS rebind cannot swap
    it), redirects are not followed, timeouts are 5 s, and at most 64 KB of the response is
    read.
  - `MOMENTUM_WEBHOOK_ALLOW_PRIVATE=1` lifts the address rules for local development only.

**Bounds.** At most 20 alerts and one webhook per owner. Notifications are kept for 90 days
(and at most 200 per owner), and finished deliveries for 30 days. The same daily maintenance
step prunes devices past their retention (ADR 0001).

**Where the loop runs.** `serve.py` starts it in its `st.App` lifespan and stops it gracefully
on shutdown. A plain `streamlit run` starts it once per process from the script. Set
`MOMENTUM_ALERT_WORKER=external` to run it only as a separate process.

## Consequences

- Alerts need a persistent owner (a device cookie or an account). In a session-only page, the
  Alerts tab explains this instead of offering a form that could not be kept.
- Market data is polled (every 60 s by default, one request per symbol per cycle), so an
  alert fires within about a minute of the cross being visible in the data. Intraday spikes
  shorter than that can be missed; that is a property of polling a delayed, unofficial feed.
- Prices are compared as floats. Thresholds are levels, not money, so float rounding a millionth
  away from the threshold does not matter.
- Every rule is a pure function, so the tests drive it with deterministic price series: a slow
  rise, a wobble around the level, a gap through it, a restart in the middle of a cycle, and two
  concurrent workers.
