-- Alerts, their events (which double as in-app notifications), the delivery
-- outbox and each owner's delivery channels. See docs/adr/0002-alerts.md.

CREATE TABLE alerts (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 40),
    owner_id TEXT NOT NULL REFERENCES owners (id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK (length(symbol) BETWEEN 1 AND 21),
    condition TEXT NOT NULL CHECK (length(condition) BETWEEN 1 AND 32),
    metric TEXT NOT NULL CHECK (length(metric) BETWEEN 1 AND 32),
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    threshold REAL NOT NULL,
    hysteresis REAL NOT NULL CHECK (hysteresis >= 0),
    cooldown_s INTEGER NOT NULL CHECK (cooldown_s >= 0),
    demo INTEGER NOT NULL CHECK (demo IN (0, 1)),
    channels TEXT NOT NULL CHECK (length(channels) <= 32),
    currency TEXT CHECK (currency IS NULL OR length(currency) <= 8),
    quote_type TEXT CHECK (quote_type IS NULL OR length(quote_type) <= 32),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'armed', 'fired')),
    fire_count INTEGER NOT NULL DEFAULT 0,
    last_value REAL,
    last_observed_at INTEGER,
    last_fired_at INTEGER,
    last_checked_at INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX alerts_owner ON alerts (owner_id, created_at);

CREATE INDEX alerts_symbol ON alerts (symbol, demo);

CREATE TABLE alert_events (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners (id) ON DELETE CASCADE,
    alert_id TEXT REFERENCES alerts (id) ON DELETE SET NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('triggered', 'test')),
    symbol TEXT,
    condition TEXT,
    threshold REAL,
    value REAL,
    message TEXT NOT NULL CHECK (length(message) <= 500),
    observed_at INTEGER,
    created_at INTEGER NOT NULL,
    read_at INTEGER,
    UNIQUE (alert_id, seq)
);

CREATE INDEX alert_events_owner ON alert_events (owner_id, created_at);

CREATE TABLE alert_deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES alert_events (id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES owners (id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'webhook')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    lease_until INTEGER,
    last_error TEXT,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    UNIQUE (event_id, channel)
);

CREATE INDEX alert_deliveries_due ON alert_deliveries (status, next_attempt_at);

CREATE TABLE alert_channels (
    owner_id TEXT PRIMARY KEY REFERENCES owners (id) ON DELETE CASCADE,
    email TEXT CHECK (email IS NULL OR length(email) <= 254),
    webhook_url TEXT CHECK (webhook_url IS NULL OR length(webhook_url) <= 2048),
    webhook_secret TEXT,
    updated_at INTEGER NOT NULL
);
