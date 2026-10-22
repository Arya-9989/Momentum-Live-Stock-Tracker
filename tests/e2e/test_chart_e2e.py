"""End to end: the chart in the real app, and its one event back to Python.

Click the price pane, choose "Set an alert at ...", and the Alerts tab opens with
the form filled in. Needs a live ``serve.py`` in demo mode (see the other e2e tests).
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.e2e.helpers import open_app, settle

pytestmark = pytest.mark.e2e


def test_chart_click_prefills_an_alert(e2e_url: str, browser: Any) -> None:
    page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
    open_app(page, f"{e2e_url}/?ticker=MSFT&range=6M&view=Overview")
    plot = page.locator(".st-key-chart_slot .mm-chart-plot")
    plot.locator("canvas").first.wait_for(timeout=60_000)
    assert page.locator('.st-key-chart_slot a[href*="tradingview.com"]').count() == 1
    box = plot.bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + 90)
    pick = page.locator(".mm-chart-pick")
    pick.wait_for(state="visible", timeout=5_000)
    text = pick.inner_text()
    match = re.fullmatch(r"Set an alert at \$([\d,]+\.\d\d)", text)
    assert match, text
    level = float(match.group(1).replace(",", ""))
    pick.click()
    settle(page)
    assert "view=Alerts" in page.url
    assert page.locator(".st-key-alert_symbol input").input_value() == "MSFT"
    assert float(page.locator(".st-key-alert_level input").input_value()) == pytest.approx(level)
    condition = page.locator(".st-key-alert_condition input").input_value()
    assert condition == "Price crosses above"  # the top of the pane is above $512.00
    page.context.close()
