-- Owners (signed-in accounts and anonymous devices) and their watchlists.
-- Times are Unix epoch milliseconds (UTC).

CREATE TABLE owners (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 64),
    kind TEXT NOT NULL CHECK (kind IN ('account', 'device')),
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE INDEX owners_kind_last_seen ON owners (kind, last_seen_at);

CREATE TABLE watchlists (
    owner_id TEXT PRIMARY KEY REFERENCES owners (id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE watchlist_items (
    owner_id TEXT NOT NULL REFERENCES watchlists (owner_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK (length(symbol) BETWEEN 1 AND 21),
    position INTEGER NOT NULL,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (owner_id, symbol)
);
