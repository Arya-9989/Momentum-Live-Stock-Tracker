# 0001: Who owns a watchlist, and where it is stored

- Status: accepted
- Date: 2026-10-23

## Context

The watchlist lived in `st.session_state`, so it vanished when the tab closed. Persisting it
needs two decisions: an **identity** to key it by, and a **store**. Later features (alerts,
portfolio) reuse both, so they have to hold up for more than a list of twelve tickers.

Constraints:

- Momentum is public and works without an account. Most viewers will never sign in, and a
  sign-in wall in front of a market dashboard would be wrong.
- Only supported Streamlit mechanisms. Streamlit 1.66 offers `st.login`/`st.user` (OIDC, via
  Authlib), read-only `st.context.cookies`, and `st.App`, an ASGI entry point that takes
  Starlette middleware. Python cannot set a cookie from a script run.
- The default deployment is a single container; some deployments run several replicas.
- An anonymous identity is a bearer credential. Whoever holds it can read and change that
  watchlist, so it must not be guessable, must not leak through URLs, and should not be
  readable by page JavaScript.

## Options considered

**Identity**

1. *Query parameter* (`?device=...`). Works everywhere, but it leaks into shared links, history
   and logs. Rejected.
2. *A token in `localStorage`, read by a custom component.* Works under plain `streamlit run`,
   but any script on the page can read it, and the watchlist is not known until the component
   has mounted and called back, which costs a second script run on every page load. Rejected.
3. *An HttpOnly cookie set by ASGI middleware in `st.App`, read through `st.context.cookies`.*
   It is invisible to JavaScript and sent automatically on the websocket handshake, so the very
   first script run knows its owner. It needs `serve.py` (the production entry point).
4. *OIDC only (`st.login`).* Strong, but it makes everyone sign in. Rejected as the only path;
   kept as the preferred one when it is configured.

**Store**

1. *SQLite through the standard library.* No server, transactional, and WAL mode lets readers run
   during a write. Right for one container with a volume.
2. *Postgres.* Needed once there are several replicas.
3. *SQLAlchemy plus Alembic* over either. It hides dialect differences, but the app has a few
   small tables, and the ORM and migration framework would outweigh the code they serve.

## Decision

**Identity**, resolved on every script run, in this order (`momentum.identity`):

1. **Account.** If `[auth]` is configured in `secrets.toml` and `st.user.is_logged_in`, the owner
   is `acct_` plus a hash of the token's `iss` and `sub`. The `sub` claim is stable per issuer;
   email addresses are not, so they are never the key.
2. **Device.** Otherwise, the signed `momentum_device` cookie. `serve.py` sets it on the HTML
   document when the request has no valid one: 128 random bits, signed with HMAC-SHA256, and
   marked `HttpOnly`, `SameSite=Lax` and `Path=/`, plus `Secure` on HTTPS, for 400 days.
   - The signature lets the server reject made-up identifiers before they reach the database.
     A rotated key is accepted through `MOMENTUM_SECRET_KEY_PREVIOUS`, and the cookie is
     re-signed with the current key.
   - `Lax` rather than `Strict`: a `Strict` cookie is withheld when someone arrives from a link
     on another site, so the middleware would issue a new identity and orphan the old list.
     Browsers do not send `Lax` cookies on cross-site websocket handshakes, so a hostile page
     still cannot act with someone else's cookie.
3. **Session.** With neither (plain `streamlit run`, or a browser that refuses cookies), the
   watchlist stays in the session, as before, and the page says so.

The key comes from `MOMENTUM_SECRET_KEY`. If it is unset, a random key is generated once and
saved as `secret.key` (mode 0600) in the data directory, so device cookies survive restarts
without any configuration.

**Store:** a repository interface (`WatchlistRepository`) with one SQL implementation that runs
on **SQLite by default** and **Postgres optionally** (`MOMENTUM_DATABASE_URL=postgresql://...`,
`psycopg` 3 with a small pool; the `postgres` extra).

- Queries are shared between the dialects; only the DDL differs, in
  `store/migrations/{sqlite,postgres}/NNNN_name.sql`.
- A small migration runner applies files in order inside one transaction. It records each file's
  SHA-256 and refuses to start if an applied migration has since been edited. Concurrent starts
  are serialised by `BEGIN IMMEDIATE` on SQLite and `pg_advisory_xact_lock` on Postgres.
- **Writes are deltas.** An edit is applied as `add` and `remove` operations rather than "save
  this list", so two tabs editing the same watchlist never undo each other's changes. Each
  operation is one transaction that takes the owner's row lock first (`BEGIN IMMEDIATE` on
  SQLite, `SELECT ... FOR UPDATE` on Postgres), so the size cap cannot be raced past. Each write
  bumps a version number, and every script run compares it with the version it last loaded, so
  a second tab picks up the change on its next run.
- **Validation:** symbols go through `data.normalize_symbol` (the same rule the search field
  uses) at the repository boundary, and the schema repeats the length rule as a `CHECK`. In the
  UI, a symbol that the data provider says does not exist is refused before it is saved. A
  network error does not count as "does not exist".
- **Bounded growth:** at most `MAX_WATCHLIST` (12) symbols per owner, enforced inside the write
  transaction. No owner row exists until the first edit, so crawlers and one-off visitors write
  nothing. Device owners not seen for `MOMENTUM_DEVICE_RETENTION_DAYS` (400) are pruned, and
  `last_seen_at` is updated at most once a day per owner.

## Consequences

- Persistence across visits needs `serve.py` (the Docker image runs it) or OIDC. Under plain
  `streamlit run` the app behaves as before, and the Watchlist tab says the list is kept for this
  session only.
- Clearing cookies starts a new anonymous watchlist; the old one is pruned after the retention
  period. Signing in is the way to keep a list across browsers.
- On Postgres, the same code runs against a pool, and every repository test runs against both
  databases (the Postgres run is enabled by `MOMENTUM_TEST_POSTGRES_URL`, and CI provides a
  Postgres service).
- Alerts and the portfolio reuse the owner model, the database and the migration runner.
