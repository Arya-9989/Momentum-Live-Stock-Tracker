"""The chart component in a real browser (Playwright + Chromium).

The built bundle (``ui/chart/momentum-chart.js``) is mounted the way Streamlit's v2
components mount it, as in test_motion_browser.py: a shadow root holding the
component's CSS, and the module's default export called with ``data`` and
``parentElement`` on every rerun. The payloads come from ``momentum.charts``.

Skipped when Chromium is not installed (``uv run playwright install chromium``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd
import pytest

from momentum import charts
from momentum import indicators as ind
from momentum.ui import chart_component

sync_api = pytest.importorskip("playwright.sync_api")

JS = (chart_component.ASSET_DIR / chart_component.JS).read_text(encoding="utf-8")
CSS = (chart_component.ASSET_DIR / chart_component.CSS).read_text(encoding="utf-8")

MOUNT = """
async ({ js, css }) => {
  const host = document.createElement("div");
  host.id = "chart";
  host.style.width = "900px";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  const parent = document.createElement("div");
  root.append(style, parent);
  const url = URL.createObjectURL(new Blob([js], { type: "text/javascript" }));
  const mod = await import(url);
  window.__triggers = [];
  window.__sweeps = 0;
  root.addEventListener("animationstart", () => { window.__sweeps += 1; });
  window.__render = (data) => mod.default({
    data, parentElement: parent,
    setTriggerValue: (event, value) => window.__triggers.push([event, value]),
  });
}
"""


def frame(n: int = 120) -> pd.DataFrame:
    close = np.linspace(200, 228, n) + np.sin(np.arange(n) / 5) * 4
    index = pd.bdate_range("2026-05-01", periods=n, name="Date")
    raw = pd.DataFrame(
        {"Open": close - 1, "High": close + 2, "Low": close - 2, "Close": close, "Volume": 1e6},
        index=index,
    )
    return ind.add_indicators(raw, ind.IndicatorSettings(sma_windows=(20,), rsi=True))


def payload(key: str = "AAPL|6M|1d|Line", style: charts.ChartStyle = "Line") -> dict[str, Any]:
    return charts.price_chart(
        frame(), interval="1d", style=style, price_prefix="$", price_decimals=2, height=480,
        key=key, alt="Apple Inc. (AAPL), 6M daily chart", pickable=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as pw:
        try:
            chromium = pw.chromium.launch()
        except sync_api.Error as exc:  # pragma: no cover - depends on the machine
            pytest.skip(f"Chromium is not installed: {exc}")
        yield chromium
        chromium.close()


def page_with(browser: Any, *, reduced: bool = False) -> Any:
    context = browser.new_context(
        viewport={"width": 1000, "height": 700},
        reduced_motion="reduce" if reduced else "no-preference",
    )
    page = context.new_page()
    page.set_content("<!doctype html><html><body style='margin:0'></body></html>")
    page.evaluate(MOUNT, {"js": JS, "css": CSS})
    return page


def render(page: Any, data: dict[str, Any]) -> None:
    page.evaluate("(data) => window.__render(data)", data)
    page.wait_for_timeout(150)


def shadow(page: Any, selector: str) -> Any:
    return page.locator(f"#chart >> {selector}")


def test_draws_and_reads_out_the_latest_bar(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    assert shadow(page, "canvas").count() > 3  # price, volume and RSI panes, axes
    readout = shadow(page, ".mm-chart-readout").inner_text()
    last = frame()["Close"].iloc[-1]
    assert readout.startswith(f"{frame().index[-1]:%a %b %d, %Y}")
    assert f"${last:,.2f}" in readout
    assert "Close" in readout
    assert "SMA 20" in readout
    assert shadow(page, "figure").get_attribute("aria-label") == "Apple Inc. (AAPL), 6M daily chart"
    labels = [shadow(page, ".mm-chart-pane-label").nth(i).text_content() for i in range(3)]
    assert labels[1:] == ["Volume", "RSI (14)  ·  30 / 70 band"]


def test_attribution_links_to_tradingview(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    link = shadow(page, 'a[href*="tradingview.com"]')
    assert link.count() == 1  # the licence's link, on the chart itself


def test_series_sweep_in_on_mount_and_when_the_key_changes(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    curtain = shadow(page, ".mm-chart-curtain")
    assert "mm-sweeping" in (curtain.get_attribute("class") or "")
    assert curtain.evaluate("el => getComputedStyle(el).animationName") == "mm-sweep"
    # The curtain covers the plot area, never the price axis.
    plot_width = curtain.evaluate("el => el.offsetWidth")  # layout width, not mid-scale
    assert 600 < plot_width < 900
    page.wait_for_timeout(800)
    sweeps = page.evaluate("window.__sweeps")
    render(page, payload())  # same key (a rerun): no replay
    assert page.evaluate("window.__sweeps") == sweeps
    render(page, payload(key="AAPL|1Y|1d|Line"))  # a new range: the sweep replays
    page.wait_for_timeout(100)
    assert page.evaluate("window.__sweeps") == sweeps + 1


def test_reduced_motion_shows_the_final_chart_at_once(browser: Any) -> None:
    page = page_with(browser, reduced=True)
    render(page, payload())
    curtain = shadow(page, ".mm-chart-curtain")
    assert "mm-sweeping" not in (curtain.get_attribute("class") or "")
    assert curtain.evaluate("el => getComputedStyle(el).display") == "none"
    assert page.evaluate("window.__sweeps") == 0


def test_hover_moves_the_readout(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    before = shadow(page, ".mm-chart-time").inner_text()
    box = shadow(page, ".mm-chart-plot").bounding_box()
    page.mouse.move(box["x"] + 60, box["y"] + 150)
    page.wait_for_timeout(100)
    after = shadow(page, ".mm-chart-time").inner_text()
    assert after != before
    assert after.endswith("2026")


def test_click_offers_an_alert_at_that_price(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    pick = shadow(page, ".mm-chart-pick")
    assert pick.is_hidden()
    box = shadow(page, ".mm-chart-plot").bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + 120)
    assert pick.is_visible()
    text = pick.inner_text()
    assert text.startswith("Set an alert at $")
    pick.click()
    ((event, value),) = page.evaluate("window.__triggers")
    assert event == "alert"
    assert f"${value['price']:,.2f}" in text
    # Escape takes the offer back.
    page.mouse.click(box["x"] + 300, box["y"] + 120)
    pick.focus()
    page.keyboard.press("Escape")
    assert pick.is_hidden()


def test_clicks_outside_the_price_pane_offer_nothing(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    box = shadow(page, ".mm-chart-plot").bounding_box()
    page.mouse.click(box["x"] + 300, box["y"] + box["height"] - 40)  # the RSI pane
    assert shadow(page, ".mm-chart-pick").is_hidden()


def test_switching_to_candles_rebuilds_the_chart(browser: Any) -> None:
    page = page_with(browser)
    render(page, payload())
    render(page, payload(key="AAPL|6M|1d|Candles", style="Candles"))
    assert shadow(page, "figure").count() == 1  # the old chart was removed
    assert "Close" in shadow(page, ".mm-chart-readout").inner_text()


def test_comparison_chart(browser: Any) -> None:
    page = page_with(browser)
    index = pd.bdate_range("2026-01-01", periods=60)
    normalized = pd.DataFrame(
        {"AAPL": np.linspace(0, 12.4, 60), "MSFT": np.linspace(0, -3.1, 60)}, index=index
    )
    render(page, charts.comparison_chart(normalized, key="AAPL|MSFT|1Y", alt="Returns"))
    readout = shadow(page, ".mm-chart-readout").inner_text()
    assert "+12.40%" in readout
    assert "-3.10%" in readout
    assert shadow(page, ".mm-chart-pick").is_hidden()  # no alert offer on returns
