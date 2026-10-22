"""Reusable Streamlit rendering helpers.

Any text that originates from Yahoo (names, exchanges, summaries) is HTML-escaped
before it is placed into custom markup.
"""

from __future__ import annotations

from functools import cache
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from momentum import formatting as fmt
from momentum.data import Profile, Quote
from momentum.ui.kpis import Kpi

_MARKET_STATES = {
    "REGULAR": ("Market open", "live"),
    "PRE": ("Pre-market", ""),
    "PREPRE": ("Pre-market", ""),
    "POST": ("After hours", ""),
    "POSTPOST": ("After hours", ""),
    "CLOSED": ("Market closed", ""),
}


@cache
def _stylesheet() -> str:
    return Path(__file__).with_name("styles.css").read_text(encoding="utf-8")


def inject_styles() -> None:
    st.html(f"<style>{_stylesheet()}</style>")


def _chip(text: str, kind: str = "") -> str:
    return f'<span class="mm-chip {kind}">{escape(text)}</span>'


def quote_header(quote: Quote, *, demo: bool) -> None:
    """Instrument name, metadata chips and the headline price with day change."""
    chips = [_chip(quote.symbol)]
    if quote.exchange:
        chips.append(_chip(quote.exchange))
    if quote.currency and not quote.is_index:
        chips.append(_chip(quote.currency))
    if quote.quote_type:
        chips.append(_chip(quote.quote_type.replace("CRYPTOCURRENCY", "CRYPTO").title()))
    if quote.market_state in _MARKET_STATES:
        label, kind = _MARKET_STATES[quote.market_state]
        chips.append(_chip(label, kind))
    if demo:
        chips.append(_chip("Demo data", "demo"))

    change, pct = quote.change, quote.change_percent
    direction = "mm-up" if (change or 0) > 0 else ("mm-down" if (change or 0) < 0 else "")
    delta = ""
    if change is not None:
        delta = (
            f'<div class="delta {direction}">'
            f"{escape(fmt.format_change(change, quote.currency, quote.quote_type))} "
            f"({escape(fmt.format_percent(pct))}) today</div>"
        )
    as_of = ""
    if quote.as_of is not None:
        stamp = quote.as_of.strftime("%b %d, %H:%M UTC")
        as_of = f'<div class="asof">Last update {escape(stamp)}</div>'

    st.html(
        f"""
        <div class="mm-header">
          <div>
            <div class="mm-eyebrow">Momentum · Market overview</div>
            <h1 class="mm-title">{escape(quote.name)}</h1>
            <div class="mm-meta">{"".join(chips)}</div>
          </div>
          <div class="mm-price">
            <div class="value">
              {escape(fmt.format_price(quote.price, quote.currency, quote.quote_type))}
            </div>
            {delta}{as_of}
          </div>
        </div>
        """
    )


def section_title(text: str) -> None:
    st.html(f'<div class="mm-section">{escape(text)}</div>')


def kpi_row(kpis: list[Kpi]) -> None:
    # The keyed container lets styles.css lay the cards out two-up on phones.
    with st.container(key="kpi_row"):
        for column, kpi in zip(st.columns(len(kpis)), kpis, strict=True):
            with column:
                st.metric(
                    kpi.label,
                    kpi.value,
                    kpi.delta,
                    delta_color=kpi.delta_color,
                    help=kpi.help,
                    border=True,
                    height="stretch",
                )


def range_bar(
    label: str, value: float | None, low: float | None, high: float | None, quote: Quote
) -> None:
    position = fmt.range_position(value, low, high)
    if position is None or low is None or high is None:
        return
    c, t = quote.currency, quote.quote_type
    st.html(
        f"""
        <div class="mm-range">
          <div class="mm-range-head"><span>{escape(label)}</span>
            <span>{position * 100:.0f}% of range</span></div>
          <div class="mm-range-track">
            <div class="mm-range-dot" style="left:{position * 100:.1f}%"></div>
          </div>
          <div class="mm-range-foot">
            <span>{escape(fmt.format_price(low, c, t))}</span>
            <span>{escape(fmt.format_price(high, c, t))}</span>
          </div>
        </div>
        """
    )


def stats_grid(rows: list[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="mm-stat"><span>{escape(label)}</span><span>{escape(value)}</span></div>'
        for label, value in rows
    )
    st.html(f'<div class="mm-stats">{cells}</div>')


def profile_card(profile: Profile, quote: Quote) -> None:
    if profile.is_empty:
        kind = "an index" if quote.is_index else "this instrument"
        st.caption(f"Yahoo Finance has no company profile for {kind}.")
        return
    chips = [p for p in (profile.sector, profile.industry, profile.country) if p]
    facts = []
    if profile.employees:
        facts.append(f"{profile.employees:,} employees")
    if profile.city:
        facts.append(escape(profile.city))
    link = ""
    if profile.website and profile.website.startswith(("https://", "http://")):
        url = escape(profile.website, quote=True)
        host = escape(profile.website.split("://", 1)[1].rstrip("/"))
        link = f' · <a href="{url}" target="_blank" rel="noopener noreferrer">{host}</a>'
    summary = escape(profile.summary or "")
    st.html(
        f"""
        <div class="mm-profile">
          <div class="mm-meta">{"".join(_chip(c) for c in chips)}</div>
          <p style="margin-top:0.6rem;font-size:0.82rem;opacity:0.7">
            {" · ".join(facts)}{link}</p>
          <p>{summary}</p>
        </div>
        """
    )


def demo_banner() -> None:
    st.warning(
        "**Demo mode** - prices, volumes and fundamentals are synthetic and generated "
        "locally. They are not real market data.",
        icon=":material/science:",
    )


def csv_download(
    frame: pd.DataFrame, filename: str, label: str = "Download CSV", key: str | None = None
) -> None:
    st.download_button(
        label,
        data=frame.to_csv(float_format="%.6g").encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        icon=":material/download:",
        key=key,
    )
