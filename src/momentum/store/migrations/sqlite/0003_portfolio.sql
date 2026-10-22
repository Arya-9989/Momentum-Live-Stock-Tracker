-- Portfolio transactions. Amounts are exact decimals: text here, NUMERIC on Postgres.
-- See docs/adr/0003-portfolio-cost-basis.md.

CREATE TABLE portfolio_transactions (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 40),
    owner_id TEXT NOT NULL REFERENCES owners (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    symbol TEXT NOT NULL CHECK (length(symbol) BETWEEN 1 AND 21),
    kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell', 'split')),
    trade_date TEXT NOT NULL CHECK (length(trade_date) = 10),
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fees TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (length(currency) BETWEEN 3 AND 8),
    created_at INTEGER NOT NULL,
    UNIQUE (owner_id, seq)
);

CREATE INDEX portfolio_transactions_owner ON portfolio_transactions (owner_id, trade_date, seq);
