"""End to end: download everything saved for this browser, then erase it.

Needs a live ``serve.py`` in demo mode (see test_watchlist_persistence.py).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.e2e.helpers import add_watchlist_symbol, open_app, settle

pytestmark = pytest.mark.e2e


def test_export_then_erase(e2e_url: str, browser: Any) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 1000}, accept_downloads=True)
    page = context.new_page()
    open_app(page, f"{e2e_url}/?view=Watchlist")
    add_watchlist_symbol(page, "TSLA")

    page.get_by_role("button", name="Your data").click()
    settle(page)
    with page.expect_download() as info:
        page.get_by_role("button", name="Download my data (JSON)").click()
    document = json.loads(info.value.path().read_text(encoding="utf-8"))
    assert document["format"] == "momentum-export"
    assert "TSLA" in document["watchlist"]
    device = next(c["value"] for c in context.cookies() if c["name"] == "momentum_device")
    assert device.split(".")[1] not in json.dumps(document)  # no identifier in the file

    delete = page.get_by_role("button", name="Delete my data")
    assert delete.is_disabled()
    page.get_by_text("I understand this permanently deletes").click()
    settle(page)
    page.get_by_role("button", name="Delete my data").click()
    settle(page)
    assert "Deleted your watchlist" in page.inner_text("body")

    # A fresh page for the same browser shows the defaults again.
    open_app(page, f"{e2e_url}/?view=Watchlist")
    chips = page.locator(".st-key-watchlist_symbols").inner_text()
    assert "TSLA" not in chips
    context.close()
