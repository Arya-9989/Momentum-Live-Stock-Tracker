# Alpha Vantage fixtures

`IBM/` holds real responses recorded on 2026-10-23 with Alpha Vantage's public `demo` key
(`scripts/record_alphavantage.py`), byte for byte, except
`TIME_SERIES_DAILY_outputsize-full.json`, which is trimmed to its 300 most recent bars to keep the
repository small. `../yahoo/IBM_daily.json` is Yahoo's data for the same symbol on the same day.

`replies/`:

| File | Source |
| --- | --- |
| `information_demo_key.json` | Recorded: the demo key used for anything but its documented calls |
| `information_rate_limit.json` | Recorded: the demo key's 2-requests-a-second throttle |
| `error_message.json` | Constructed from the documented format (the demo key cannot produce one) |
| `information_daily_quota.json` | Constructed from the documented free-tier quota reply (key redacted) |
| `information_premium.json` | Constructed from the documented premium-endpoint reply |
| `note_per_minute.json` | Constructed from the older per-minute throttle reply |
| `empty_overview.json` | Constructed: `OVERVIEW` answers `{}` for a symbol it does not know |
