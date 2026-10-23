"""The stylesheet for the Alerts, Portfolio and Fundamentals views (features.css)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import streamlit as st

_CSS = Path(__file__).with_name("features.css")


@cache
def _css() -> str:
    return _CSS.read_text(encoding="utf-8")


def inject_feature_styles() -> None:
    st.html(f"<style>{_css()}</style>")
