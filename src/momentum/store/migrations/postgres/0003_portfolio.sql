-- Portfolio transactions. Amounts are exact decimals: text on SQLite, NUMERIC here.
-- See docs/adr/0003-portfolio-cost-basis.md.

CREATE TABLE portfolio_transactions (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 8 AND 40),
    owner_id TEXT NOT NULL REFERENCES owners (id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    symbol TEXT NOT NULL CHECK (length(symbol) BETWEEN 1 AND 21),
    kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell', 'split')),
    trade_date DATE NOT NULL,
    quantity NUMERIC(28, 10) NOT NULL CHECK (quantity > 0),
    price NUMERIC(28, 10) NOT NULL CHECK (price >= 0),
    fees NUMERIC(28, 10) NOT NULL CHECK (fees >= 0),
    currency TEXT NOT NULL CHECK (length(currency) BETWEEN 3 AND 8),
    created_at BIGINT NOT NULL,
    UNIQUE (owner_id, seq)
);

CREATE INDEX portfolio_transactions_owner ON portfolio_transactions (owner_id, trade_date, seq);
