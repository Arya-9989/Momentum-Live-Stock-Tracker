"""The chart component's Python side: registration, the alert hand-off and the attribution."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import streamlit
from streamlit.errors import StreamlitAPIException

from momentum.ui import chart_component, views

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_declares_the_built_assets() -> None:
    manifest = tomllib.loads((ROOT / "src" / "momentum" / "pyproject.toml").read_text("utf-8"))
    assert manifest["project"]["name"] == "momentum"  # must match the distribution
    (component,) = manifest["tool"]["streamlit"]["component"]["components"]
    assert component == {"name": "price_chart", "asset_dir": "ui/chart"}
    assert (chart_component.ASSET_DIR / chart_component.JS).is_file()
    assert (chart_component.ASSET_DIR / chart_component.CSS).is_file()
    assert (chart_component.ASSET_DIR / "THIRD_PARTY_NOTICES.md").is_file()


def test_bundle_keeps_the_licence_banner_and_builds_no_markup_from_strings() -> None:
    bundle = (chart_component.ASSET_DIR / chart_component.JS).read_text("utf-8")
    assert "TradingView Lightweight Charts™ v5.2.1" in bundle
    assert "Licensed under Apache License 2.0" in bundle
    source = (ROOT / "frontend" / "chart" / "src" / "index.ts").read_text("utf-8")
    assert "attributionLogo: true" in source
    assert "innerHTML" not in source


def test_registration_prefers_the_packaged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[str] = []

    def component(name: str, *, js: str, css: str) -> Any:
        registered.append(name)
        assert (js, css) == ("momentum-chart.js", "momentum-chart.css")  # paths, not content
        return lambda **_: None

    monkeypatch.setattr(streamlit.components.v2, "component", component)
    monkeypatch.setattr(chart_component, "_registered", {})
    assert chart_component.served_as_files()
    assert registered == ["momentum.price_chart"]


def test_inline_fallback_without_a_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[tuple[str, int]] = []

    def component(name: str, *, js: str, css: str) -> Any:
        if name == chart_component.PACKAGED_NAME:
            raise StreamlitAPIException("must be declared in pyproject.toml with asset_dir")
        registered.append((name, len(js)))
        return lambda **_: None

    monkeypatch.setattr(streamlit.components.v2, "component", component)
    monkeypatch.setattr(chart_component, "_registered", {})
    assert not chart_component.served_as_files()
    assert registered[0][0] == "momentum_price_chart"
    assert registered[0][1] > 100_000  # the whole bundle, inline


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"alert": {"price": 251.2}}, 251.2),
        ({"alert": {"price": 0}}, None),
        ({"alert": {"price": True}}, None),
        ({"alert": {"price": "251"}}, None),
        ({"alert": None}, None),
        ({}, None),
        (None, None),
    ],
)
def test_alert_price(state: object, expected: float | None) -> None:
    assert chart_component.alert_price(state) == expected


def test_alert_price_reads_attributes_too() -> None:
    class Result:
        @property
        def alert(self) -> dict[str, float]:
            return {"price": 99.5}

    assert chart_component.alert_price(Result()) == 99.5


@pytest.mark.parametrize(("price", "condition"), [(260.0, "price_above"), (210.0, "price_below")])
def test_chart_pick_prefills_the_alert_form(
    monkeypatch: pytest.MonkeyPatch, price: float, condition: str
) -> None:
    state: dict[str, Any] = {"price_chart": {"alert": {"price": price}}}
    monkeypatch.setattr(streamlit, "session_state", state)
    views.alert_from_chart("AAPL", 228.0)
    assert state["alert_symbol"] == "AAPL"
    assert state["alert_condition"] == condition
    assert state["alert_level"] == price
    assert state["_alert_level_for"] == ("AAPL", condition)
    assert state["view"] == "Alerts"


def test_a_pick_without_a_price_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict[str, Any] = {"price_chart": {}}
    monkeypatch.setattr(streamlit, "session_state", state)
    views.alert_from_chart("AAPL", 228.0)
    assert state == {"price_chart": {}}


def test_attribution_names_tradingview_with_a_link() -> None:
    assert "TradingView Lightweight Charts™" in chart_component.ATTRIBUTION
    assert "(https://www.tradingview.com/)" in chart_component.ATTRIBUTION
