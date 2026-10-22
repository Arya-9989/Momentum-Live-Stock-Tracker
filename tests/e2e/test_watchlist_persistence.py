"""End to end: the watchlist survives a reload, and two browsers never share one.

Runs against a live ``serve.py`` (the production entry point, which sets the
device cookie) in demo mode, so no network is needed::

    MOMENTUM_DEMO=1 uv run streamlit run serve.py --server.port 5425
    MOMENTUM_E2E_URL=http://127.0.0.1:5425 uv run --group screenshots pytest tests/e2e

Skipped unless ``MOMENTUM_E2E_URL`` is set.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import add_watchlist_symbol, open_app, rail_symbols

pytestmark = pytest.mark.e2e


def test_watchlist_persists_per_browser(e2e_url: str, browser: object) -> None:
    from playwright.sync_api import Browser

    assert isinstance(browser, Browser)
    first = browser.new_context()
    page = first.new_page()
    open_app(page, f"{e2e_url}/?view=Watchlist")
    cookie = next(c for c in first.cookies() if c["name"] == "momentum_device")
    assert cookie["httpOnly"]
    assert cookie["sameSite"] == "Lax"
    assert page.evaluate("document.cookie").find("momentum_device") == -1  # not readable by JS
    assert "Saved in this browser." in page.inner_text("body")

    add_watchlist_symbol(page, "TSLA")
    page.goto(f"{e2e_url}/?view=Overview")
    open_app(page)
    assert "TSLA" in rail_symbols(page)

    # A fresh browser profile gets its own identity and the default list.
    second = browser.new_context()
    other = second.new_page()
    open_app(other, f"{e2e_url}/")
    assert "TSLA" not in rail_symbols(other)
    other_cookie = next(c for c in second.cookies() if c["name"] == "momentum_device")
    assert other_cookie["value"] != cookie["value"]

    # A forged cookie is replaced, not trusted.
    third = browser.new_context()
    third.add_cookies(
        [
            {
                "name": "momentum_device",
                "value": cookie["value"][:-2] + "AA",
                "url": e2e_url,
            }
        ]
    )
    forged = third.new_page()
    open_app(forged, f"{e2e_url}/")
    assert "TSLA" not in rail_symbols(forged)
    for context in (first, second, third):
        context.close()
