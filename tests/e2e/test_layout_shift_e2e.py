"""End to end: no tab shifts its layout while it loads, on a phone or a desktop.

Streamlit draws each element as a 40 px placeholder until its code has loaded, so any
element without a slot of its final height moves everything below it when it arrives.
Each tab is opened by a new viewer (nothing saved, empty states), as Lighthouse sees it.

Needs a live ``serve.py`` in demo mode (see test_watchlist_persistence.py).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.e2e.helpers import open_app

pytestmark = pytest.mark.e2e

BUDGET = 0.02  # the bar is 0.05; this leaves room for noise and still catches a real shift
TABS = ("Overview", "Fundamentals", "Compare", "Watchlist", "Portfolio", "Alerts")
OBSERVER = """
window.__shifts = [];
new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    if (entry.hadRecentInput) continue;
    window.__shifts.push({
      value: entry.value,
      at: Math.round(entry.startTime),
      moved: (entry.sources || []).map((s) => {
        const el = s.node && s.node.nodeType === 1 ? s.node : s.node && s.node.parentElement;
        const text = el ? (el.innerText || '').trim().slice(0, 40) : '';
        return `${text || (el && el.className) || '?'}: y ${Math.round(s.previousRect.y)}` +
          ` -> ${Math.round(s.currentRect.y)}, h ${Math.round(s.previousRect.height)}` +
          ` -> ${Math.round(s.currentRect.height)}`;
      }),
    });
  }
}).observe({ type: 'layout-shift', buffered: true });
"""


@pytest.mark.parametrize("device", ["Pixel 7", "desktop"])
@pytest.mark.parametrize("tab", TABS)
def test_tab_loads_without_layout_shift(
    e2e_url: str, browser: Any, playwright_driver: Any, tab: str, device: str
) -> None:
    options = (
        playwright_driver.devices["Pixel 7"]
        if device == "Pixel 7"
        else {"viewport": {"width": 1440, "height": 900}}
    )
    context = browser.new_context(**options)
    context.add_init_script(OBSERVER)
    page = context.new_page()
    open_app(page, f"{e2e_url}/?view={tab}")
    # Scroll through once, as a reader (and the UI audit) would, then let it settle.
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(600)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(600)
    shifts = page.evaluate("window.__shifts")
    context.close()
    total = sum(s["value"] for s in shifts)
    worst = sorted(shifts, key=lambda s: -s["value"])[:3]
    assert total < BUDGET, f"{tab} on {device}: CLS {total:.4f}\n{json.dumps(worst, indent=1)}"
