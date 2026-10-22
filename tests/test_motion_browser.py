"""The motion components in a real browser (Playwright + Chromium).

Each component module is mounted the way Streamlit's v2 components mount it: a
shadow root holding the component's CSS, and the module's default export called
with ``data`` and ``parentElement`` on every rerun. The payloads come from the
same Python builders the app uses.

Skipped when Chromium is not installed (``uv run playwright install chromium``).
"""

from __future__ import annotations

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


# The header is plain st.html: React swaps its innerHTML on every change, which is
# what STAGE does, inside an app root carrying the real stylesheet.
STAGE = """
([css, html]) => {
  if (!document.getElementById("app")) {
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);
    const app = document.createElement("div");
    app.id = "app";
    app.dataset.testid = "stApp";
    document.body.appendChild(app);
  }
  document.getElementById("app").innerHTML = html;
}
"""
# The price as assistive tech reads it (aria-hidden glyphs left out), and as seen.
SPOKEN = """() => { const n = document.querySelector('.mm-price .mm-num').cloneNode(true);
  n.querySelectorAll('[aria-hidden]').forEach(e => e.remove()); return n.textContent; }"""
SEEN = "() => document.querySelector('.mm-price .mm-num').innerText"
ANIMATING = "() => document.getAnimations().length"


def stage(page: Any, current: Quote, previous: Quote | None = None) -> None:
    html = components.quote_header_html(current, previous)
    page.evaluate(STAGE, [asset("styles.css"), html])


def test_header_roll_ends_on_the_exact_static_text(browser: Any) -> None:
    page = page_with(browser)
    aapl, msft = quote("AAPL", 228.0, 227.4923), quote("MSFT", 1234.56, 1240.0)
    stage(page, aapl)
    assert page.evaluate(ANIMATING) == 0  # a first render shows plain figures
    stage(page, msft, aapl)
    assert page.evaluate(ANIMATING) > 0
    assert page.evaluate(SPOKEN) == "$1,234.56"  # spoken right away, once
    page.wait_for_function("() => document.getAnimations().length === 0", timeout=3000)
    assert page.evaluate(SEEN) == "$1,234.56"  # the old glyphs end hidden
    change = page.evaluate("() => document.querySelector('.mm-change .mm-num').innerText")
    assert change == "-$5.44 (-0.44%)"


def test_header_roll_moves_nothing_around_it(browser: Any) -> None:
    page = page_with(browser)
    aapl, msft = quote("AAPL", 228.0, 227.0), quote("MSFT", 512.0, 518.35)
    box = (
        "() => { const r = document.querySelector('.mm-asof').getBoundingClientRect();"
        " return [r.x, r.y]; }"
    )
    stage(page, msft)
    plain = page.evaluate(box)
    stage(page, msft, aapl)
    assert page.evaluate(ANIMATING) > 0
    rolling = page.evaluate(box)
    assert rolling == pytest.approx(plain, abs=0.5)  # the "As of" time keeps its place


def test_header_rolls_down_when_the_value_fell(browser: Any) -> None:
    page = page_with(browser)
    names = (
        "() => [...document.querySelectorAll('.mm-price .mm-in')]"
        ".map(n => getComputedStyle(n).animationName)"
    )
    stage(page, quote("AAPL", 229.0, 227.0), quote("AAPL", 228.0, 227.0))
    assert set(page.evaluate(names)) == {"mm-roll-in-up"}
    stage(page, quote("AAPL", 227.0, 227.0), quote("AAPL", 229.0, 227.0))
    assert set(page.evaluate(names)) == {"mm-roll-in-down"}


def test_header_under_reduced_motion_shows_the_final_value_at_once(browser: Any) -> None:
    page = page_with(browser, reduced=True)
    aapl, msft = quote("AAPL", 228.0, 227.0), quote("MSFT", 512.0, 518.35)
    stage(page, msft, aapl)
    assert page.evaluate(ANIMATING) == 0
    assert page.evaluate(SEEN) == "$512.00"


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


def test_header_roll_never_shows_an_empty_slot(browser: Any) -> None:
    """During its stagger delay a slot still shows the old glyph (backwards fill)."""
    page = page_with(browser)
    stage(page, quote("MSFT", 512.0, 518.35), quote("AAPL", 228.0, 227.0))
    shown = page.evaluate(
        """() => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = 0; });
          return [...document.querySelectorAll('.mm-price .mm-out')]
            .map(n => getComputedStyle(n).visibility + ' ' + getComputedStyle(n).opacity); }"""
    )
    assert shown == ["visible 1"] * 3  # "228" fully there until each digit's turn


def test_a_shorter_kpi_rolls_its_old_prefix_out_and_lands_exactly(browser: Any) -> None:
    page = page_with(browser)
    mount(page, "kpis", asset("numeric.js", "kpis.js"), asset("kpis.css"))
    render(page, "kpis", kpi_cards("6,673.32", 6673.32, "+1.00%"))
    render(page, "kpis", kpi_cards("$4.40T", 4.4e12, "+1.00%"))
    lead = shadow(page, "kpis", "return root.querySelector('.value .mm-lead .mm-out').textContent")
    assert lead == "6,6"  # "$" arrives where "6,6" leaves
    page.wait_for_function(SETTLED, arg="kpis", timeout=3000)
    assert shadow(page, "kpis", "return root.querySelector('.value').textContent") == "$4.40T"
