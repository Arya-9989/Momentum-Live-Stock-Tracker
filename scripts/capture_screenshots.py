"""Capture README screenshots of a running Momentum instance with Playwright.

Usage (app already running on :8501):

    uv run --group screenshots python scripts/capture_screenshots.py
    uv run --group screenshots python scripts/capture_screenshots.py --demo

Requires the Chromium build for the installed Playwright (``playwright install chromium``).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


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
    page.wait_for_timeout(2_500)


def shoot(page: Page, url: str, name: str, *, charts: int = 1, selector: str | None = None) -> None:
    page.goto(url)
    wait_for_app(page, charts=charts, selector=selector)
    path = OUT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"saved {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8501")
    parser.add_argument("--demo", action="store_true", help="screenshot with demo data")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base = args.base_url.rstrip("/")
    suffix = "&demo=true" if args.demo else ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for scheme in ("light", "dark"):
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 1500},
                device_scale_factor=1,
                color_scheme=scheme,
            )
            page = ctx.new_page()
            shoot(page, f"{base}/?ticker=AAPL&range=6M{suffix}", f"overview-{scheme}")
            if scheme == "light":
                page.set_viewport_size({"width": 1440, "height": 1000})
                shoot(
                    page, f"{base}/?ticker=LT.NS&range=1Y&view=Compare{suffix}", "compare", charts=1
                )
                shoot(
                    page,
                    f"{base}/?view=Watchlist{suffix}",
                    "watchlist",
                    charts=0,
                    selector='[data-testid="stDataFrame"] canvas',
                )
            ctx.close()

        mobile = browser.new_context(
            viewport={"width": 390, "height": 1300},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            color_scheme="light",
        )
        shoot(mobile.new_page(), f"{base}/?ticker=AAPL&range=6M{suffix}", "mobile")
        mobile.close()
        browser.close()


if __name__ == "__main__":
    main()
