"""Record Alpha Vantage and Yahoo responses for the provider tests (tests/fixtures/).

    uv run python scripts/record_alphavantage.py            # IBM with the public demo key
    ALPHAVANTAGE_API_KEY=... uv run python scripts/record_alphavantage.py --symbol MSFT

Alpha Vantage documents a ``demo`` key that works for IBM, so the default needs no
account. Responses are stored byte for byte, with the key never written. The same
day's Yahoo data for the symbol is stored beside them, so the tests can compare the
two providers' normalised output.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
URL = "https://www.alphavantage.co/query"
CALLS = (
    {"function": "GLOBAL_QUOTE"},
    {"function": "OVERVIEW"},
    {"function": "TIME_SERIES_DAILY"},
    {"function": "TIME_SERIES_DAILY", "outputsize": "full"},
    {"function": "TIME_SERIES_WEEKLY"},
    {"function": "TIME_SERIES_MONTHLY"},
    {"function": "TIME_SERIES_INTRADAY", "interval": "5min"},
    {"function": "INCOME_STATEMENT"},
    {"function": "BALANCE_SHEET"},
    {"function": "DIVIDENDS"},
)


def fixture_name(params: dict[str, str]) -> str:
    parts = [
        params["function"],
        *(f"{k}-{v}" for k, v in sorted(params.items()) if k != "function"),
    ]
    return "_".join(parts) + ".json"


def record_alphavantage(symbol: str, key: str) -> None:
    folder = ROOT / "alphavantage" / symbol
    folder.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as client:
        for call in CALLS:
            params = {**call, "symbol": symbol}
            response = client.get(URL, params={**params, "apikey": key})
            response.raise_for_status()
            (folder / fixture_name(call)).write_bytes(response.content)
            print(f"recorded {call['function']} ({len(response.content):,} bytes)")
            time.sleep(1.2)  # the demo key allows 2 requests a second


def record_yahoo(symbol: str) -> None:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    frame = ticker.history(period="6mo", interval="1d", auto_adjust=True)
    info = ticker.info
    out = {
        "history": {
            "index": [ts.isoformat() for ts in frame.index],
            "columns": list(frame.columns),
            "data": frame.to_numpy().tolist(),
        },
        "info": {k: v for k, v in info.items() if isinstance(v, str | int | float | bool)},
    }
    path = ROOT / "yahoo" / f"{symbol}_daily.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"recorded Yahoo {symbol} ({len(frame)} bars)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="IBM")
    args = parser.parse_args()
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip() or "demo"
    record_alphavantage(args.symbol, key)
    record_yahoo(args.symbol)


if __name__ == "__main__":
    main()
