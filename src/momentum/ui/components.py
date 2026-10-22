"""Reusable Streamlit rendering helpers.

Any text that originates from Yahoo (names, exchanges, summaries) is HTML-escaped
before it is placed into custom markup. Markup builders that need no Streamlit
(``*_html``) are pure so they can be unit-tested.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from momentum import formatting as fmt
from momentum.data import Profile, Quote
from momentum.ui.kpis import Kpi
from momentum.ui.rail import RailRow, rail_height, rail_payload

_HERE = Path(__file__).parent

MARKET_STATES = {
    "REGULAR": "Market open",
    "PRE": "Pre-market",
    "PREPRE": "Pre-market",
    "POST": "After hours",
    "POSTPOST": "After hours",
    "CLOSED": "Market closed",
}

# A small ink mark (a rising line in a rounded square), drawn by styles.css
# because st.html strips inline SVG. Decorative only.
_LOGO = '<span class="mm-logo" aria-hidden="true"></span>'


@cache
def _asset(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8")


def inject_styles() -> None:
    st.html(f"<style>{_asset('styles.css')}</style>")


# --------------------------------------------------------------------- v2 components
# Registered once at import. st.components.v2 mounts inline (no iframe), so
# these are ordinary DOM nodes that keyboard and screen-reader users can reach.

_RAIL = st.components.v2.component(
    "momentum_watchlist_rail", css=_asset("rail.css"), js=_asset("rail.js")
)

# Streamlit does not rerun when the viewer switches theme (from the OS or the
# main menu), but the Plotly figures are drawn per theme on the server. This
# zero-height watcher notices the switch and asks for one rerun.
_THEME_WATCH_JS = """
export default function (component) {
  const { setTriggerValue, parentElement } = component;
  const app = (parentElement.host || parentElement).closest('[data-testid="stApp"]');
  const scheme = () => (app ? getComputedStyle(app).colorScheme : "");
  let last = scheme();
  let timer = 0;
  const check = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const now = scheme();
      if (now && now !== last) { last = now; setTriggerValue("changed", now); }
    }, 60);
  };
  const observer = new MutationObserver(check);
  observer.observe(document.head, { childList: true, subtree: true, characterData: true });
  const media = matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", check);
  return () => {
    observer.disconnect();
    media.removeEventListener("change", check);
    clearTimeout(timer);
  };
}
"""
_THEME_WATCH = st.components.v2.component("momentum_theme_watch", js=_THEME_WATCH_JS)


def theme_watcher() -> None:
    with st.container(key="theme_watch"):
        _THEME_WATCH(key="theme_watch_component", on_changed_change=lambda: None, height=0)


def watchlist_rail(rows: Sequence[RailRow], on_select: Callable[[], None]) -> None:
    _RAIL(
        key="rail_list",
        data=rail_payload(rows),
        on_select_change=on_select,
        height=rail_height(len(rows)),
    )


# --------------------------------------------------------------------- skeletons
# Shown only on a session's first run, at the exact size of what replaces them,
# so nothing below moves when the data arrives (CLS stays at zero).


def rail_skeleton(count: int) -> None:
    rows = "".join('<div class="mm-sk-row"><i></i><i></i></div>' for _ in range(max(count, 1)))
    st.html(
        f'<div class="mm-skeleton mm-sk-rail" style="height:{rail_height(count)}px" '
        f'aria-hidden="true">{rows}</div>'
    )


def header_skeleton() -> None:
    st.html(
        f'<div class="mm-quote mm-skeleton" style="height:{HEADER_HEIGHT}px" aria-busy="true" '
        'aria-label="Loading quote">'
        '<i class="mm-sk-meta"></i><i class="mm-sk-name"></i><i class="mm-sk-price"></i>'
        '<i class="mm-sk-change"></i></div>'
    )


def chart_skeleton(height: int) -> None:
    st.html(
        f'<div class="mm-skeleton mm-sk-chart" style="height:{height}px" aria-hidden="true">'
        "<i></i></div>"
    )


# --------------------------------------------------------------------- page chrome


# Fixed slot heights (px). Streamlit loads each element type lazily and shows a
# 40 px placeholder meanwhile, so every slot above the fold is a fixed-height
# container: whatever loads first, nothing around it moves.
HEADER_HEIGHT = 176
TOOLBAR_HEIGHT = 48
NOTICE_HEIGHT = 40
RAIL_HEADER_HEIGHT = 40


def wordmark() -> None:
    # Inline geometry: it must match the 40 px search field before styles.css loads.
    st.html(
        '<div class="mm-wordmark" style="display:flex;align-items:center;gap:8px;height:40px;'
        f'font-size:17px;font-weight:600">{_LOGO}<span>Momentum</span></div>',
        width="content",
    )


def hairline() -> None:
    st.html('<hr class="mm-hairline" style="margin:0;border:0;height:1px">')


def fineprint(text: str) -> None:
    st.html(f'<p class="mm-fineprint">{escape(text)}</p>')


def demo_notice() -> None:
    """One-line, fixed-height label that the data is synthetic (shown whenever demo is on)."""
    with st.container(key="demo_notice", height=NOTICE_HEIGHT, border=False):
        st.html(
            f'<p class="mm-demo-notice" role="status" style="height:{NOTICE_HEIGHT}px;'
            f"line-height:{NOTICE_HEIGHT}px;margin:0;white-space:nowrap;overflow:hidden;"
            'text-overflow:ellipsis"><strong>Demo mode</strong>: synthetic prices and '
            "fundamentals, not real market data.</p>"
        )


def eyebrow(text: str) -> None:
    st.html(f'<p class="mm-eyebrow">{escape(text)}</p>')


def section_title(text: str, *, level: int = 2, subtitle: str | None = None) -> None:
    sub = f'<p class="mm-subhead">{escape(subtitle)}</p>' if subtitle else ""
    st.html(f'<div class="mm-section"><h{level}>{escape(text)}</h{level}>{sub}</div>')


PAGE_TITLE_HEIGHT = 112  # h1 + a subtitle of up to two lines at phone width


def page_title(title: str, subtitle: str) -> None:
    """The h1 of the Compare and Watchlist views, in a fixed-height slot."""
    with st.container(key="page_title", height=PAGE_TITLE_HEIGHT, border=False):
        st.html(
            f'<div class="mm-section mm-page-title" style="height:{PAGE_TITLE_HEIGHT}px;'
            f'margin:0;padding-top:20px;box-sizing:border-box;overflow:hidden">'
            f"<h1>{escape(title)}</h1>"
            f'<p class="mm-subhead">{escape(subtitle)}</p></div>'
        )


def empty_panel(title: str, body: str, *, height: int) -> None:
    """Designed empty/error state at the size of the slot it fills."""
    st.html(
        f'<div class="mm-empty" style="height:{height}px;box-sizing:border-box">'
        f"<strong>{escape(title)}</strong><span>{escape(body)}</span></div>"
    )


def notice(text: str, *, kind: str = "info") -> None:
    st.html(f'<p class="mm-notice mm-notice-{escape(kind)}" role="status">{escape(text)}</p>')


# --------------------------------------------------------------------- quote header


def _direction_class(change: float | None) -> str:
    if change is None or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def quote_header_html(quote: Quote) -> str:
    """Symbol line, instrument name, the hero price and the day change."""
    meta = [f'<span class="mm-sym">{escape(quote.symbol)}</span>']
    meta += [
        f"<span>{escape(part)}</span>"
        for part in (
            quote.exchange,
            (quote.quote_type or "").replace("CRYPTOCURRENCY", "CRYPTO").title() or None,
            None if quote.is_index else quote.currency,
        )
        if part
    ]
    state = MARKET_STATES.get(quote.market_state or "")
    badges = ""
    if state:
        code = escape(quote.market_state or "", quote=True)
        badges += f'<span class="mm-state" data-state="{code}">{escape(state)}</span>'

    change, pct = quote.change, quote.change_percent
    change_html = '<p class="mm-change flat">&nbsp;</p>'
    if change is not None:
        places = fmt.price_decimals(quote.price, quote.quote_type)
        absolute = fmt.format_change(change, quote.currency, quote.quote_type, decimals=places)
        change_html = (
            f'<p class="mm-change {_direction_class(change)}">'
            f'<span class="mm-num">{escape(absolute)} ({escape(fmt.format_percent(pct))})</span>'
            '<span class="mm-when">Today</span></p>'
        )
    as_of = ""
    if quote.as_of is not None:
        as_of = (
            f'<time class="mm-asof" datetime="{quote.as_of.isoformat()}">'
            f"As of {escape(quote.as_of.strftime('%b %d, %H:%M UTC'))}</time>"
        )
    price = fmt.format_price(quote.price, quote.currency, quote.quote_type)
    return (
        f'<header class="mm-quote" style="height:{HEADER_HEIGHT}px">'
        f'<p class="mm-meta">{'<span class="mm-sep" aria-hidden="true">·</span>'.join(meta)}'
        f"{badges}</p>"
        f'<h1 class="mm-name" title="{escape(quote.name, quote=True)}">{escape(quote.name)}</h1>'
        f'<p class="mm-price"><span class="mm-num">{escape(price)}</span>{as_of}</p>'
        f"{change_html}</header>"
    )


def quote_header(quote: Quote) -> None:
    st.html(quote_header_html(quote))


# --------------------------------------------------------------------- KPIs and stats


def kpi_row(kpis: list[Kpi]) -> None:
    # The keyed container lets styles.css lay the cards out two-up on phones.
    with st.container(key="kpi_row"):
        for column, kpi in zip(st.columns(len(kpis), gap="small"), kpis, strict=True):
            with column:
                st.metric(
                    kpi.label,
                    kpi.value,
                    kpi.delta,
                    delta_color=kpi.delta_color,
                    help=kpi.help,
                    height="stretch",
                )


def range_bar_html(
    label: str, value: float | None, low: float | None, high: float | None, quote: Quote
) -> str:
    """A neutral low-high track with the current price marked; ``""`` when not computable."""
    position = fmt.range_position(value, low, high)
    if position is None or low is None or high is None:
        return ""
    c, t = quote.currency, quote.quote_type
    pct = position * 100
    return (
        '<div class="mm-range">'
        f'<div class="mm-range-head"><span>{escape(label)}</span>'
        f'<span class="mm-num">{pct:.0f}% of range</span></div>'
        f'<div class="mm-range-track" role="img" aria-label="{escape(label)}: price at '
        f'{pct:.0f}% between low and high">'
        f'<span class="mm-range-fill" style="width:{pct:.1f}%"></span>'
        f'<span class="mm-range-dot" style="left:{pct:.1f}%"></span></div>'
        '<div class="mm-range-foot">'
        f'<span class="mm-num">{escape(fmt.format_price(low, c, t))}</span>'
        f'<span class="mm-num">{escape(fmt.format_price(high, c, t))}</span></div></div>'
    )


def range_bars(quote: Quote) -> None:
    bars = [
        range_bar_html("Day range", quote.price, quote.day_low, quote.day_high, quote),
        range_bar_html(
            "52-week range",
            quote.price,
            quote.fifty_two_week_low,
            quote.fifty_two_week_high,
            quote,
        ),
    ]
    markup = "".join(bars)
    if markup:
        st.html(f'<div class="mm-ranges">{markup}</div>')


def stats_grid(rows: list[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="mm-stat"><dt>{escape(label)}</dt>'
        f'<dd class="mm-num">{escape(value)}</dd></div>'
        for label, value in rows
    )
    st.html(f'<dl class="mm-stats">{cells}</dl>')


def profile_card(profile: Profile, quote: Quote) -> None:
    if profile.is_empty:
        kind = "an index" if quote.is_index else "this instrument"
        st.html(f'<p class="mm-muted">Yahoo Finance has no company profile for {kind}.</p>')
        return
    tags = " · ".join(escape(p) for p in (profile.sector, profile.industry, profile.country) if p)
    facts = []
    if profile.employees:
        facts.append(f"{profile.employees:,} employees")
    if profile.city:
        facts.append(escape(profile.city))
    link = ""
    if profile.website and profile.website.startswith(("https://", "http://")):
        url = escape(profile.website, quote=True)
        host = escape(profile.website.split("://", 1)[1].rstrip("/"))
        link = f'<a href="{url}" target="_blank" rel="noopener noreferrer">{host}</a>'
    facts_line = " · ".join([*facts, link] if link else facts)
    summary = escape(profile.summary or "")
    st.html(
        f"""
        <div class="mm-profile">
          {f'<p class="mm-tags">{tags}</p>' if tags else ""}
          {f'<p class="mm-facts">{facts_line}</p>' if facts_line else ""}
          {f'<p class="mm-summary">{summary}</p>' if summary else ""}
        </div>
        """
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
        type="tertiary",
        key=key,
    )


def plotly_config() -> dict[str, Any]:
    # Drag to zoom, double-click to reset; the modebar is hidden because it
    # overlaps the legend on phones and adds little over those gestures.
    return {"displaylogo": False, "displayModeBar": False, "scrollZoom": False, "responsive": True}
