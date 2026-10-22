"""The motion components in a real browser (Playwright + Chromium).

Each component module is mounted the way Streamlit's v2 components mount it: a
shadow root holding the component's CSS, and the module's default export called
with ``data`` and ``parentElement`` on every rerun. The payloads come from the
same Python builders the app uses.

Skipped when Chromium is not installed (``uv run playwright install chromium``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from momentum import formatting as fmt
from momentum.data import Quote
from momentum.ui import components
from momentum.ui.kpis import Kpi

sync_api = pytest.importorskip("playwright.sync_api")

UI = Path(components.__file__).parent

MOUNT = """
async ({ js, css, name }) => {
  const host = document.createElement("div");
  host.id = name;
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  const parent = document.createElement("div");
  root.append(style, parent);
  const url = URL.createObjectURL(new Blob([js], { type: "text/javascript" }));
  const mod = await import(url);
  window.__triggers = [];
  window.__mount = window.__mount || {};
  window.__mount[name] = (data) => mod.default({
    data, parentElement: parent,
    setTriggerValue: (event, value) => window.__triggers.push([event, value]),
  });
}
"""

SETTLED = """
(name) => !document.getElementById(name).shadowRoot.querySelector(".mm-roll")
"""


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
        viewport={"width": 1440, "height": 900},
        reduced_motion="reduce" if reduced else "no-preference",
    )
    page = context.new_page()
    page.set_content("<!doctype html><html><body style='margin:0'></body></html>")
    return page


def mount(page: Any, name: str, js: str, css: str) -> None:
    page.evaluate(MOUNT, {"js": js, "css": css, "name": name})


def render(page: Any, name: str, data: dict[str, Any]) -> None:
    page.evaluate("([name, data]) => window.__mount[name](data)", [name, data])


def shadow(page: Any, name: str, script: str) -> Any:
    return page.evaluate(
        f"(name) => {{ const root = document.getElementById(name).shadowRoot; {script} }}", name
    )


def asset(*names: str) -> str:
    return "".join((UI / n).read_text(encoding="utf-8") for n in names)


def quote(symbol: str, price: float, previous: float) -> Quote:
    return Quote(
        symbol=symbol,
        name=f"{symbol} Inc.",
        currency="USD",
        exchange="NasdaqGS",
        quote_type="EQUITY",
        market_state="REGULAR",
        price=price,
        previous_close=previous,
        as_of=datetime(2026, 10, 22, 16, 0, tzinfo=UTC),
    )


def mount_quote(page: Any) -> None:
    mount(page, "quote", asset("numeric.js", "quote.js"), asset("quote.css"))


PRICE = "return root.querySelector('.mm-price .mm-num')"
CHANGE = "return root.querySelector('.mm-change .mm-num')"


# --------------------------------------------------------------------- quote header


def test_first_render_shows_the_final_figures_at_once(browser: Any) -> None:
    page = page_with(browser)
    mount_quote(page)
    payload = components.quote_header_payload(quote("AAPL", 228.0, 227.4923))
    render(page, "quote", payload)
    assert shadow(page, "quote", PRICE + ".textContent") == "$228.00"
    assert shadow(page, "quote", "return root.querySelector('.mm-roll')") is None
    assert shadow(page, "quote", "return root.querySelector('h1').textContent") == "AAPL Inc."


def test_a_new_symbol_rolls_and_ends_on_the_exact_static_text(browser: Any) -> None:
    page = page_with(browser)
    mount_quote(page)
    render(page, "quote", components.quote_header_payload(quote("AAPL", 228.0, 227.4923)))
    after = components.quote_header_payload(quote("MSFT", 1234.56, 1240.0))
    render(page, "quote", after)

    # Mid-transition: digits roll, while assistive tech already has the final value.
    assert shadow(page, "quote", "return !!root.querySelector('.mm-price .mm-roll')")
    assert (
        shadow(page, "quote", "return root.querySelector('.mm-price .mm-sr').textContent")
        == (after["price"])
    )
    assert shadow(
        page, "quote", "return root.querySelector('.mm-price .mm-roll').getAttribute('aria-hidden')"
    ) == ("true")

    page.wait_for_function(SETTLED, arg="quote", timeout=3000)
    assert shadow(page, "quote", PRICE + ".textContent") == "$1,234.56"
    assert shadow(page, "quote", PRICE + ".childNodes.length") == 1  # one plain text node
    assert shadow(page, "quote", CHANGE + ".textContent") == after["change"]["text"]
    assert shadow(page, "quote", "return root.querySelector('.mm-change').className") == (
        "mm-change down"
    )


def test_only_changed_characters_roll(browser: Any) -> None:
    page = page_with(browser)
    mount_quote(page)
    render(page, "quote", components.quote_header_payload(quote("AAPL", 228.0, 227.0)))
    render(page, "quote", components.quote_header_payload(quote("AAPL", 228.5, 227.0)))
    rolling = shadow(
        page,
        "quote",
        "return [...root.querySelectorAll('.mm-price .mm-in')].map(n => n.textContent)",
    )
    assert rolling == ["5"]  # $228.00 -> $228.50: one digit moves, the rest keep still


def test_a_rise_rolls_up_and_a_fall_rolls_down(browser: Any) -> None:
    page = page_with(browser)
    mount_quote(page)
    first_frame = (
        "return root.querySelector('.mm-price .mm-in').getAnimations()[0]"
        ".effect.getKeyframes()[0].transform"
    )
    render(page, "quote", components.quote_header_payload(quote("AAPL", 228.0, 227.0)))
    render(page, "quote", components.quote_header_payload(quote("AAPL", 229.0, 227.0)))
    assert shadow(page, "quote", first_frame) == "translateY(0.6em)"  # in from below
    page.wait_for_function(SETTLED, arg="quote", timeout=3000)
    render(page, "quote", components.quote_header_payload(quote("AAPL", 227.0, 227.0)))
    assert shadow(page, "quote", first_frame) == "translateY(-0.6em)"  # in from above


def test_reduced_motion_shows_the_final_value_immediately(browser: Any) -> None:
    page = page_with(browser, reduced=True)
    mount_quote(page)
    render(page, "quote", components.quote_header_payload(quote("AAPL", 228.0, 227.0)))
    render(page, "quote", components.quote_header_payload(quote("MSFT", 512.0, 518.35)))
    assert shadow(page, "quote", "return root.querySelector('.mm-roll')") is None
    assert shadow(page, "quote", PRICE + ".textContent") == "$512.00"
    assert shadow(page, "quote", "return root.getAnimations().length") == 0


def test_an_interrupted_roll_still_ends_on_the_latest_value(browser: Any) -> None:
    page = page_with(browser)
    mount_quote(page)
    for symbol, price in (("A", 10.0), ("B", 2000.0), ("C", 37.5)):
        render(page, "quote", components.quote_header_payload(quote(symbol, price, price)))
    page.wait_for_function(SETTLED, arg="quote", timeout=3000)
    assert shadow(page, "quote", PRICE + ".textContent") == "$37.50"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (0.0123, 81_499.46),
        (81_499.46, 0.0123),
        (99.99, 100.0),
        (1_000_000.0, 999_999.99),
    ],
)
def test_number_transition_always_lands_on_the_formatted_string(
    browser: Any, before: float, after: float
) -> None:
    page = page_with(browser)
    page.add_script_tag(content=asset("numeric.js"))
    texts = [fmt.format_price(v, "USD", "EQUITY") for v in (before, after)]
    final = page.evaluate(
        """async ([a, b, va, vb]) => {
          const node = document.createElement("span");
          document.body.appendChild(node);
          await mmSetNumber(node, a, va);
          await mmSetNumber(node, b, vb);
          return [node.textContent, node.childNodes.length];
        }""",
        [*texts, before, after],
    )
    assert final == [texts[1], 1]


# --------------------------------------------------------------------- KPI tiles


def kpi_cards(value: str, number: float, delta: str) -> dict[str, Any]:
    return components.kpi_payload(
        [
            Kpi("6M return", value, delta, help="Change over the range.", value_number=number),
            Kpi("Market cap", "$2.95T", value_number=2.95e12),
        ]
    )


def test_kpis_roll_to_the_exact_values_and_keep_their_tiles(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "kpis", asset("numeric.js", "kpis.js"), asset("kpis.css"))
    render(page, "kpis", kpi_cards("+$20.00", 20.0, "+20.00%"))
    shadow(page, "kpis", "root.querySelector('.kpi').__probe = 1; return null")
    render(page, "kpis", kpi_cards("-$4.10", -4.1, "-3.50%"))
    assert shadow(page, "kpis", "return !!root.querySelector('.value .mm-roll')")
    page.wait_for_function(SETTLED, arg="kpis", timeout=3000)
    values = shadow(
        page, "kpis", "return [...root.querySelectorAll('.value')].map(n => n.textContent)"
    )
    assert values == ["-$4.10", "$2.95T"]
    assert shadow(page, "kpis", "return root.querySelector('.kpi').__probe") == 1  # same node
    delta = shadow(
        page, "kpis", "const d = root.querySelector('.delta'); return [d.className, d.textContent]"
    )
    assert delta == ["delta down", "-3.50%"]
    assert shadow(page, "kpis", "return root.querySelectorAll('.delta')[1].hidden") is True


def test_kpis_under_reduced_motion_show_final_values(browser: Any) -> None:
    page = page_with(browser, reduced=True)
    mount(page, "kpis", asset("numeric.js", "kpis.js"), asset("kpis.css"))
    render(page, "kpis", kpi_cards("+$20.00", 20.0, "+20.00%"))
    render(page, "kpis", kpi_cards("-$4.10", -4.1, "-3.50%"))
    assert shadow(page, "kpis", "return root.querySelector('.mm-roll')") is None
    assert shadow(page, "kpis", "return root.querySelector('.value').textContent") == "-$4.10"


def test_kpi_help_is_a_dismissible_toggletip(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "kpis", asset("numeric.js", "kpis.js"), asset("kpis.css"))
    render(page, "kpis", kpi_cards("+$20.00", 20.0, "+20.00%"))
    help_button = page.locator("#kpis button.help").first
    assert help_button.get_attribute("aria-label") == "About 6M return"
    tip_id = help_button.get_attribute("aria-describedby")
    assert shadow(page, "kpis", f"return root.getElementById('{tip_id}').textContent") == (
        "Change over the range."
    )
    help_button.click()
    assert help_button.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert help_button.get_attribute("aria-expanded") == "false"
    assert page.locator("#kpis button.help").nth(1).is_hidden()  # no help, no button


# --------------------------------------------------------------------- watchlist rail


def rail_payload(selected: str) -> dict[str, Any]:
    rows = [
        {
            "symbol": s,
            "name": f"{s} Inc.",
            "price": "$1.00",
            "change": "+1.00%",
            "direction": "up",
            "spark": "",
            "label": s,
            "selected": s == selected,
        }
        for s in ("AAPL", "MSFT", "NVDA", "TSLA")
    ]
    return {"rows": rows}


INDICATOR = (
    "const i = root.querySelector('.indicator'); const cs = getComputedStyle(i);"
    "return [i.style.transform, i.classList.contains('on'), cs.transitionDuration, cs.opacity]"
)


def test_selection_indicator_slides_to_the_selected_row(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "rail", asset("rail.js"), asset("rail.css"))
    render(page, "rail", rail_payload("AAPL"))
    transform, on, duration, _ = shadow(page, "rail", INDICATOR)
    assert (transform, on) == ("translateY(0px)", True)
    assert duration.startswith("0.32s")  # ready to slide, after a still first placement
    render(page, "rail", rail_payload("NVDA"))
    assert shadow(page, "rail", INDICATOR)[0] == "translateY(120px)"
    # The selected row paints no fill of its own: the indicator is the selection.
    background = shadow(
        page,
        "rail",
        "return getComputedStyle(root.querySelector('[aria-current]')).backgroundColor",
    )
    assert background == "rgba(0, 0, 0, 0)"


def test_clicking_a_row_moves_the_indicator_before_the_rerun(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "rail", asset("rail.js"), asset("rail.css"))
    render(page, "rail", rail_payload("AAPL"))
    page.locator("#rail button.row[data-symbol='TSLA']").click()
    assert shadow(page, "rail", INDICATOR)[0] == "translateY(180px)"
    current = shadow(page, "rail", "return root.querySelector('[aria-current]').dataset.symbol")
    assert current == "TSLA"
    assert page.evaluate("window.__triggers") == [["select", "TSLA"]]


def test_indicator_does_not_slide_under_reduced_motion(browser: Any) -> None:
    page = page_with(browser, reduced=True)
    mount(page, "rail", asset("rail.js"), asset("rail.css"))
    render(page, "rail", rail_payload("AAPL"))
    render(page, "rail", rail_payload("MSFT"))
    transform, _, duration, _ = shadow(page, "rail", INDICATOR)
    assert transform == "translateY(60px)"
    assert set(duration.replace(" ", "").split(",")) == {"0s"}


def test_indicator_hides_when_no_row_is_selected(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "rail", asset("rail.js"), asset("rail.css"))
    render(page, "rail", rail_payload("AAPL"))
    render(page, "rail", rail_payload("NONE"))
    assert shadow(page, "rail", INDICATOR)[1] is False


def test_payloads_are_plain_json() -> None:
    payload = components.quote_header_payload(quote("AAPL", 1.0, 1.0))
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
