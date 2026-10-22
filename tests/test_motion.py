"""Motion rules that can be checked without a browser: what is animated, how
reduced motion is honoured, and the data the number transitions run on."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from momentum.data import Quote
from momentum.ui import components
from momentum.ui.kpis import Kpi, build_kpis

UI = Path(components.__file__).parent
STYLESHEETS = ("styles.css", "rail.css", "quote.css", "kpis.css")
# Hard rule: only compositor-friendly properties move (plus colour fades on hover).
ANIMATABLE = {"opacity", "transform", "clip-path", "visibility"}
HOVER_FADES = {"background-color"}


def css(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def keyframe_properties(text: str) -> set[str]:
    props: set[str] = set()
    for body in re.findall(r"@keyframes [\w-]+ \{(.*?)\}\s*\}", text, re.S):
        props |= set(re.findall(r"([a-z-]+)\s*:", body))
    return props


def transition_properties(text: str) -> set[str]:
    props: set[str] = set()
    for value in re.findall(r"transition:\s*([^;]+);", text):
        # Split on top-level commas only (var(--x, y) holds one).
        for part in re.split(r",\s*(?![^()]*\))", value):
            word = part.strip().split()[0]
            if word != "none":
                props.add(word)
    return props


@pytest.mark.parametrize("name", STYLESHEETS)
def test_only_opacity_transform_and_clip_path_animate(name: str) -> None:
    text = css(name)
    assert keyframe_properties(text) <= ANIMATABLE
    assert transition_properties(text) <= ANIMATABLE | HOVER_FADES


def test_chart_sweep_runs_only_without_a_reduced_motion_preference() -> None:
    text = css("styles.css")
    block = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", text, re.S)
    assert block is not None
    assert "animation: mm-sweep" in block.group(1)
    assert text.count("animation: mm-sweep") == 1  # nowhere outside that block
    assert ".mlayer" in block.group(1)  # traces only: axes and grid are there at once


@pytest.mark.parametrize("name", ["rail.css", "kpis.css"])
def test_shadow_root_stylesheets_have_their_own_reduced_motion_rule(name: str) -> None:
    # styles.css's global reduced-motion rule cannot reach into shadow roots.
    assert "prefers-reduced-motion: reduce" in css(name)


def test_number_transition_honours_reduced_motion_and_ends_on_the_static_text() -> None:
    js = css("numeric.js")
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js
    assert "node.textContent = next" in js  # the final DOM is the formatted string itself
    assert "innerHTML" not in js
    # Four keyframes (in and out, from and to), each moving transform and opacity only.
    frames = re.findall(r"\{ (\w+): [^\n]*?, (\w+): [01] \}", js)
    assert frames == [("transform", "opacity")] * 4


@pytest.mark.parametrize("name", ["quote.js", "kpis.js", "rail.js"])
def test_components_never_build_markup_from_strings(name: str) -> None:
    js = css(name)
    assert "textContent" in js
    assert "innerHTML" not in js


def test_components_get_the_shared_number_transition() -> None:
    import inspect

    source = inspect.getsource(components)
    assert '_asset("numeric.js") + _asset("quote.js")' in source
    assert '_asset("numeric.js") + _asset("kpis.js")' in source


EQUITY = Quote(
    symbol="AAPL",
    name="Apple Inc.",
    currency="USD",
    quote_type="EQUITY",
    price=190.0,
    previous_close=188.0,
    day_low=187.0,
    day_high=191.0,
    market_cap=2.95e12,
    trailing_pe=29.6,
    volume=40e6,
    average_volume=50e6,
)


def frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes}, index=pd.bdate_range("2026-01-01", periods=len(closes)))


def test_kpi_payload_carries_the_static_strings_and_their_numbers() -> None:
    kpis = build_kpis(EQUITY, frame([100.0, 110.0, 120.0]), "6M", "1d")
    cards = components.kpi_payload(kpis)["cards"]
    assert json.loads(json.dumps(cards, allow_nan=False)) == cards
    by_label = {c["label"]: c for c in cards}
    assert by_label["6M return"]["value"] == "+$20.00"
    assert by_label["6M return"]["value_number"] == pytest.approx(20.0)
    assert by_label["6M return"]["delta_number"] == pytest.approx(20.0)
    assert by_label["Market cap"]["value_number"] == 2.95e12
    assert by_label["Volume"]["delta"] == "-20% vs avg"
    assert by_label["Volume"]["delta_number"] == pytest.approx(-20.0)
    assert by_label["Volume"]["help"]


def test_index_kpis_carry_numbers_too() -> None:
    index = Quote(symbol="^X", name="X", quote_type="INDEX", price=5.0, day_low=4.0, day_high=6.0)
    cards = components.kpi_payload(build_kpis(index, frame([5.0, 4.0, 5.0]), "1Y", "1d"))["cards"]
    numbers = {c["label"]: c["value_number"] for c in cards}
    assert numbers["Day high"] == 6.0
    assert numbers["Day low"] == 4.0
    assert numbers["Drawdown"] == pytest.approx(-20.0)


@pytest.mark.parametrize(
    ("delta", "color", "expected"),
    [
        ("+1.00%", "normal", ("up", "up")),
        ("-1.00%", "normal", ("down", "down")),
        ("\N{MINUS SIGN}1.00%", "normal", ("down", "down")),
        ("-1.00%", "inverse", ("up", "down")),
        ("+1.00%", "inverse", ("down", "up")),
        ("-20% vs avg", "off", ("off", "down")),
        (None, "normal", ("up", "up")),
    ],
)
def test_kpi_tone_matches_st_metric(
    delta: str | None, color: str, expected: tuple[str, str]
) -> None:
    kpi = Kpi("X", "1", delta, color)  # type: ignore[arg-type]
    assert components.kpi_tone(kpi) == expected
