"""Page helpers shared by the browser tests."""

from __future__ import annotations

from typing import Any

RAIL_ROWS = "button.row[data-symbol]"


def open_app(page: Any, url: str | None = None) -> None:
    """Load ``url`` (or stay) and wait until the script run has finished."""
    if url:
        page.goto(url)
    page.wait_for_selector('[data-testid="stApp"]', timeout=60_000)
    # The theme watcher is the last element of every run.
    page.wait_for_selector(".st-key-theme_watch", state="attached", timeout=60_000)
    settle(page)


def settle(page: Any) -> None:
    page.wait_for_timeout(400)
    page.wait_for_function(
        "!document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60_000
    )
    page.wait_for_timeout(300)


def rail_symbols(page: Any) -> list[str]:
    page.wait_for_selector(RAIL_ROWS, timeout=60_000)
    return [str(s) for s in page.eval_on_selector_all(RAIL_ROWS, "els => els.map(e => e.dataset.symbol)")]


def add_watchlist_symbol(page: Any, symbol: str) -> None:
    field = page.locator(".st-key-watchlist_symbols input")
    field.click()
    field.fill(symbol)
    page.keyboard.press("Enter")
    page.keyboard.press("Escape")
    settle(page)
