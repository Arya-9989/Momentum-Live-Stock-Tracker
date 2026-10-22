"""Capture README screenshots of a running Momentum instance with Playwright.

Usage (app already running on :8501):

    uv run --group screenshots python scripts/capture_screenshots.py
    uv run --group screenshots python scripts/capture_screenshots.py --demo
    uv run --group screenshots python scripts/capture_screenshots.py --base-url http://localhost:5410

Requires the Chromium build for the installed Playwright (``playwright install chromium``).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from playwright.sync_api import Browser, Page, sync_playwright

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
RAIL = '[data-testid="stBidiComponentIsolated"]'
TABLE = '[data-testid="stDataFrame"] canvas'


def wait_for_app(page: Page, *, charts: int = 1, selector: str | None = None) -> None:
    page.wait_for_selector('[data-testid="stApp"]', timeout=60_000)
    if selector:
        page.wait_for_selector(selector, timeout=90_000)
    if charts:
        page.wait_for_function(
            f"document.querySelectorAll('.js-plotly-plot .main-svg').length >= {charts}",
            timeout=90_000,
        )
    # Let the script run finish (the status widget disappears) and fonts/plots settle.
    page.wait_for_function(
        "!document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=90_000
    )
    page.wait_for_function("document.fonts.status === 'loaded'", timeout=30_000)
    page.wait_for_timeout(2_000)


def shoot(page: Page, url: str, name: str, *, charts: int = 1, selector: str | None = None) -> None:
    page.goto(url)
    wait_for_app(page, charts=charts, selector=selector)
    path = OUT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"saved {path}")


Scheme = Literal["light", "dark"]


def desktop(browser: Browser, scheme: Scheme, height: int) -> Page:
    ctx = browser.new_context(
        viewport={"width": 1440, "height": height}, device_scale_factor=1, color_scheme=scheme
    )
    return ctx.new_page()


def crosshair(page: Page, url: str, name: str) -> None:
    """The chart with MACD switched on and the pointer over it: one crosshair and one
    readout through every panel."""
    page.goto(url)
    wait_for_app(page)
    page.get_by_role("button", name="Indicators").click()
    page.get_by_label("MACD (12, 26, 9)").check(force=True)
    page.keyboard.press("Escape")
    wait_for_app(page)
    chart = page.locator(".st-key-chart_slot .js-plotly-plot")
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.62, box["y"] + 140)
    page.mouse.move(box["x"] + box["width"] * 0.62 + 2, box["y"] + 150)
    page.wait_for_timeout(800)
    path = OUT_DIR / f"{name}.png"
    page.screenshot(path=str(path), clip=box)
    print(f"saved {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8501")
    parser.add_argument("--demo", action="store_true", help="screenshot with demo data")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base = args.base_url.rstrip("/")
    demo = "&demo=true" if args.demo else ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        schemes: tuple[Scheme, ...] = ("light", "dark")
        for scheme in schemes:
            page = desktop(browser, scheme, 1060)
            shoot(page, f"{base}/?ticker=AAPL&range=6M{demo}", f"overview-{scheme}", selector=RAIL)
            page.context.close()

        page = desktop(browser, "light", 1000)
        shoot(page, f"{base}/?ticker=AAPL&range=1Y&view=Compare{demo}", "compare")
        shoot(page, f"{base}/?view=Watchlist{demo}", "watchlist", charts=0, selector=TABLE)
        page.context.close()

        page = desktop(browser, "light", 1200)
        crosshair(page, f"{base}/?ticker=AAPL&range=3M{demo}", "crosshair")
        page.context.close()

        mobile = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            color_scheme="light",
        )
        shoot(mobile.new_page(), f"{base}/?ticker=AAPL&range=6M{demo}", "mobile")
        mobile.close()
        browser.close()


if __name__ == "__main__":
    main()
