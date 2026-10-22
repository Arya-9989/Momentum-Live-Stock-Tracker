"""The Alerts tab and the in-app notification toasts.

Alerts are evaluated by the background loop (``momentum.alerts.worker``), not by
this script; this module only creates, lists and deletes them, and shows the
notifications the loop wrote. See docs/adr/0002-alerts.md.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime
from functools import partial
from html import escape
from typing import Final
from urllib.parse import urlsplit

import streamlit as st

from momentum import audit, config, data
from momentum import formatting as fmt
from momentum.alerts import delivery, engine, rules
from momentum.data import DataError
from momentum.identity import Identity
from momentum.settings import load as load_settings
from momentum.store import StoreError
from momentum.store.alerts import (
    Alert,
    AlertLimitError,
    Channel,
    Event,
    NewAlert,
    SqlAlertRepository,
)
from momentum.ui import components, messages, session

logger = logging.getLogger(__name__)

NOTICE_KEY: Final = "_alerts_notice"
SECRET_KEY: Final = "_alerts_webhook_secret"
_CURSOR_KEY: Final = "_alerts_toast_cursor"
POLL_SECONDS: Final = 30
FORM_ROW_HEIGHT: Final = 68  # px: a label above a 40 px field
ACTIONS_ROW_HEIGHT: Final = 40

NO_IDENTITY = messages.Copy(
    "Alerts need a saved identity.",
    "This session isn't remembered, so an alert couldn't be kept. Sign in, or open Momentum "
    "with cookies allowed.",
)
UNAVAILABLE = messages.Copy(
    "Alerts are unavailable right now.",
    "Saved data can't be reached. Your alerts are safe; try again in a moment.",
)


# --------------------------------------------------------------------------- pure helpers

_MARKDOWN_SPECIAL: Final = frozenset("\\`*_{}[]()#+-.!|~$<>")


def plain_markdown(text: str) -> str:
    """Escape ``text`` so Markdown shows it verbatim.

    ``st.toast`` renders Markdown, where ``$200.00 ... $201.00`` is LaTeX math and
    the dollar signs vanish; every punctuation character is backslash-escaped.
    """
    return "".join(f"\\{ch}" if ch in _MARKDOWN_SPECIAL else ch for ch in text)


def toast_text(events: list[Event]) -> str:
    """One toast for everything new: a stack of toasts reflows as each one closes."""
    latest = events[-1].message
    others = len(events) - 1
    if not others:
        return latest
    more = "1 more alert" if others == 1 else f"{others} more alerts"
    return f"{latest} And {more} in the Alerts tab."


def format_ago(then_ms: int | None, now_ms: int) -> str:
    """``just now``, ``4 min ago``, ``3 h ago``, ``Oct 21``."""
    if then_ms is None:
        return "not checked yet"
    seconds = max(0, (now_ms - then_ms) // 1000)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86_400:
        return f"{seconds // 3600} h ago"
    return datetime.fromtimestamp(then_ms / 1000, tz=UTC).strftime("%b %d")


def status_text(alert: Alert, now_ms: int) -> str:
    """One line: where the alert stands and when it was last checked."""
    checked = format_ago(alert.last_checked_at, now_ms)
    if alert.last_error:
        return f"Couldn't check: {alert.last_error} · {checked}"
    if alert.state == "pending":
        return "Waiting for the first check"
    if alert.state == "fired":
        when = format_ago(alert.last_fired_at, now_ms) if alert.last_fired_at else None
        lead = f"Triggered {when}" if when else "Past the level"
        return f"{lead}; re-arms after it moves back · {checked}"
    return f"Waiting · checked {checked}"


def level_label(condition: rules.Condition, currency: str | None, quote_type: str | None) -> str:
    if condition.unit == "price":
        symbol = "" if fmt.is_unitless(quote_type) else fmt.currency_symbol(currency).strip()
        suffix = fmt.MINOR_UNIT_SUFFIXES.get(currency or "", "")
        unit = symbol or suffix.strip()
        return f"Level ({unit})" if unit else "Level"
    if condition.unit == "percent":
        return "Day change (%)"
    if condition.unit == "points":
        return "RSI level"
    return "Level"


def default_level(condition: rules.Condition, current: float | None) -> float:
    """A sensible starting value for the level field."""
    if condition.metric == "rsi14":
        return 70.0 if condition.direction == "above" else 30.0
    if condition.metric == "change_pct":
        return 3.0 if condition.direction == "above" else -3.0
    if condition.unit == "price" and current is not None and math.isfinite(current):
        step = 1.05 if condition.direction == "above" else 0.95
        return float(f"{current * step:.{fmt.price_decimals(current)}f}")
    return 0.0


def current_hint(
    symbol: str, condition: rules.Condition, value: float | None, alert_like: Alert | None
) -> str:
    """``AAPL is $228.00 now.`` (or the indicator's value), for context while creating."""
    if value is None:
        return f"No current value for {symbol} yet."
    shown = engine.describe_value(alert_like, value) if alert_like else f"{value:g}"
    what = {
        "price": f"{symbol} is",
        "change_pct": f"{symbol} is",
        "rsi14": f"RSI (14) for {symbol} is",
    }.get(condition.metric, f"The SMA gap for {symbol} is")
    suffix = " today" if condition.metric == "change_pct" else " now"
    return f"{what} {shown}{suffix}."


# --------------------------------------------------------------------------- data


def _repo() -> SqlAlertRepository | None:
    store = session.open_store()
    return store.alerts if store is not None else None


def _quote_and_closes(symbol: str, demo: bool) -> tuple[data.Quote | None, object]:
    try:
        info = data.cached_info(symbol, demo)
    except DataError:
        return None, None
    quote = data.parse_quote(symbol, info)
    try:
        closes = data.cached_history(symbol, "2y", "1d", demo)["Close"]
    except DataError:
        closes = None
    return quote, closes


def _preview_alert(symbol: str, condition: rules.Condition, quote: data.Quote | None) -> Alert:
    """An unsaved ``Alert`` used only to format values like a saved one."""
    return Alert(
        id="preview",
        owner_id="preview",
        symbol=symbol,
        condition=condition.key,
        metric=condition.metric,
        direction=condition.direction,
        threshold=0.0,
        hysteresis=0.0,
        cooldown_s=0,
        demo=False,
        channels=(),
        currency=quote.currency if quote else None,
        quote_type=quote.quote_type if quote else None,
        state="pending",
        fire_count=0,
        last_value=None,
        last_observed_at=None,
        last_fired_at=None,
        last_checked_at=None,
        last_error=None,
        created_at=0,
    )


# --------------------------------------------------------------------------- callbacks


def _create(who: Identity, demo: bool, currency: str | None, quote_type: str | None) -> None:
    repo = _repo()
    if repo is None or who.owner is None:
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    raw_symbol = str(st.session_state.get("alert_symbol") or "")
    key = str(st.session_state.get("alert_condition") or "price_above")
    level = st.session_state.get("alert_level")
    channels: list[Channel] = []
    if st.session_state.get("alert_email"):
        channels.append("email")
    if st.session_state.get("alert_webhook"):
        channels.append("webhook")
    try:
        symbol = data.normalize_symbol(raw_symbol)
        if not _exists(symbol, demo):
            st.session_state[NOTICE_KEY] = f"No data for {symbol}, so no alert was created."
            return
        alert = repo.create(
            who.owner,
            NewAlert(
                symbol=symbol,
                condition=key,
                threshold=float(level) if level is not None else math.nan,
                demo=demo,
                channels=tuple(channels),
                currency=currency,
                quote_type=quote_type,
            ),
        )
    except (DataError, rules.RuleError, AlertLimitError) as exc:
        st.session_state[NOTICE_KEY] = str(exc)
        return
    except StoreError as exc:
        logger.warning("Could not create an alert: %r", exc)
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    audit.record("alert.created", who.owner, symbol=alert.symbol, condition=alert.condition)
    st.session_state[NOTICE_KEY] = f"Alert created: {alert.symbol} {engine.describe(alert)}."


def _exists(symbol: str, demo: bool) -> bool:
    try:
        data.cached_info(symbol, demo)
    except data.DataSourceError:
        return True
    except DataError:
        return False
    return True


def _delete(who: Identity, alert_id: str) -> None:
    repo = _repo()
    if repo is None or who.owner is None:
        return
    try:
        if repo.delete(who.owner, alert_id):
            audit.record("alert.deleted", who.owner)
    except StoreError:
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title


def _mark_read(who: Identity) -> None:
    repo = _repo()
    if repo is not None and who.owner is not None:
        try:
            repo.mark_read(who.owner)
        except StoreError:
            st.session_state[NOTICE_KEY] = UNAVAILABLE.title


def _save_webhook(who: Identity) -> None:
    repo = _repo()
    if repo is None or who.owner is None:
        return
    url = str(st.session_state.get("webhook_url") or "").strip()
    try:
        delivery.check_url(url, allow_private=load_settings().webhook_allow_private)
    except delivery.DeliveryError as exc:
        st.session_state[NOTICE_KEY] = str(exc)
        return
    try:
        st.session_state[SECRET_KEY] = repo.set_webhook(who.owner, url)
    except StoreError:
        st.session_state[NOTICE_KEY] = UNAVAILABLE.title
        return
    audit.record("webhook.saved", who.owner, host=urlsplit(url).hostname)
    st.session_state[NOTICE_KEY] = "Webhook saved. Copy the signing secret below now."


def _rotate_secret(who: Identity) -> None:
    repo = _repo()
    if repo is not None and who.owner is not None:
        secret = repo.rotate_webhook_secret(who.owner)
        if secret:
            audit.record("webhook.secret_rotated", who.owner)
            st.session_state[SECRET_KEY] = secret
            st.session_state[NOTICE_KEY] = "New signing secret created; the old one stops working."


def _remove_webhook(who: Identity) -> None:
    repo = _repo()
    if repo is not None and who.owner is not None:
        repo.remove_webhook(who.owner)
        audit.record("webhook.removed", who.owner)
        st.session_state.pop(SECRET_KEY, None)
        st.session_state[NOTICE_KEY] = "Webhook removed."


def _send_test(who: Identity, channel: Channel) -> None:
    repo = _repo()
    if repo is not None and who.owner is not None:
        repo.send_test(who.owner, channel)
        audit.record("delivery.test_queued", who.owner, channel=channel)
        st.session_state[NOTICE_KEY] = f"Test {channel} queued; it is sent within a minute."


# --------------------------------------------------------------------------- view


def panel(copy: messages.Copy, *, height: int = 160, key: str = "feature_panel") -> None:
    """An empty or error state in a slot of its own height (no shift while it loads)."""
    with st.container(key=key, height=height, border=False):
        components.error_panel(copy, height=height)


def subsection_html(title: str, detail: str | None = None) -> str:
    """Geometry is inline, like the page title's, so it is right from the first paint
    (features.css arrives with the first st.html element and can come later)."""
    sub = (
        f'<p class="mm-fsub" style="margin:0;font-size:15px;line-height:20px">{escape(detail)}</p>'
        if detail
        else ""
    )
    return (
        '<div class="mm-fsection" style="display:flex;align-items:baseline;gap:0.75rem;'
        'margin:2.75rem 0 0.5rem">'
        f'<h2 style="margin:0;padding:0;font-size:21px;line-height:25px;font-weight:600">'
        f"{escape(title)}</h2>{sub}</div>"
    )


def subsection(title: str, detail: str | None = None) -> None:
    """A heading inside a tab: smaller than the page title, with an optional count."""
    st.html(subsection_html(title, detail))


def alerts(raw_symbol: str, demo: bool, who: Identity) -> None:
    components.page_title(
        "Alerts",
        "Get a notification when a price or an indicator crosses a level. Checked every minute.",
    )
    if who.owner is None:
        panel(NO_IDENTITY)
        return
    repo = _repo()
    if repo is None:
        panel(UNAVAILABLE)
        return
    try:
        saved = repo.for_owner(who.owner)
        channels = repo.channels(who.owner)
        if who.kind == "account" and who.email and channels.email != who.email:
            repo.set_email(who.owner, who.email)
            channels = repo.channels(who.owner)
        recent = repo.events(who.owner, limit=10)
    except StoreError:
        panel(UNAVAILABLE)
        return

    _create_form(
        raw_symbol,
        demo,
        who,
        email=channels.email,
        has_webhook=bool(channels.webhook_url),
        count=len(saved),
    )
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        components.notice(notice)
    _alert_list(saved, who)
    _notifications(recent, who)
    _webhook_settings(who, channels.webhook_url)


def _create_form(
    raw_symbol: str,
    demo: bool,
    who: Identity,
    *,
    email: str | None,
    has_webhook: bool,
    count: int,
) -> None:
    subsection("New alert")
    if "alert_symbol" not in st.session_state:
        st.session_state["alert_symbol"] = raw_symbol.strip().upper() or config.DEFAULT_TICKER
    # Three fixed-height rows (their content is 68, 68 and 40 px at every width),
    # top-aligned: as Streamlit loads each widget's code, nothing moves.
    with st.container(
        key="alert_form",
        horizontal=True,
        vertical_alignment="top",
        gap="small",
        height=FORM_ROW_HEIGHT,
        border=False,
    ):
        symbol_raw = st.text_input("Symbol", key="alert_symbol", max_chars=21, width=120)
        key = st.selectbox(
            "Condition",
            [c.key for c in rules.CONDITIONS],
            format_func=lambda k: rules.CONDITIONS_BY_KEY[k].label,
            key="alert_condition",
            width="stretch",
        )
    condition = rules.CONDITIONS_BY_KEY[key]
    try:
        symbol = data.normalize_symbol(symbol_raw or "")
    except DataError as exc:
        components.fineprint(str(exc))
        return
    quote, closes = _quote_and_closes(symbol, demo)
    preview = _preview_alert(symbol, condition, quote)
    value = (
        engine.metric_values([condition.metric], quote, closes).get(condition.metric)  # type: ignore[arg-type]
        if quote is not None
        else None
    )
    with st.container(
        key="alert_form_level",
        horizontal=True,
        vertical_alignment="top",
        gap="medium",
        height=FORM_ROW_HEIGHT,
        border=False,
    ):
        if condition.fixed_threshold is None:
            if st.session_state.get("_alert_level_for") != (symbol, key):
                st.session_state["alert_level"] = default_level(condition, value)
                st.session_state["_alert_level_for"] = (symbol, key)
            st.number_input(
                level_label(condition, preview.currency, preview.quote_type),
                key="alert_level",
                format="%.4f" if condition.unit == "price" and (value or 1) < 1 else "%.2f",
                width=160,
            )
        hint = escape(current_hint(symbol, condition, value, preview))
        st.html(f'<p class="mm-hint">{hint}</p>', width="stretch")
    email_ok = bool(email) and load_settings().smtp is not None
    with st.container(
        key="alert_form_actions",
        horizontal=True,
        vertical_alignment="center",
        gap="small",
        height=ACTIONS_ROW_HEIGHT,
        border=False,
    ):
        # Fixed widths: each item's slot is final before its code loads.
        st.checkbox(
            "Email",
            key="alert_email",
            width=88,
            disabled=not email_ok,
            help=f"Sent to {email}, the address your sign-in verified."
            if email_ok
            else "Email alerts need a signed-in account with a verified address, and an email "
            "server configured for this app.",
        )
        st.checkbox(
            "Webhook",
            key="alert_webhook",
            width=116,
            disabled=not has_webhook,
            help="Sent to your webhook (see Webhook delivery below)."
            if has_webhook
            else "Add a webhook below first.",
        )
        st.button(
            "Create alert",
            key="alert_create",
            type="primary",
            width=120,
            disabled=count >= rules.MAX_ALERTS_PER_OWNER,
            on_click=_create,
            args=(who, demo, preview.currency, preview.quote_type),
        )


def _alert_list(saved: list[Alert], who: Identity) -> None:
    subsection("Your alerts", f"{len(saved)} of {rules.MAX_ALERTS_PER_OWNER}")
    if not saved:
        components.empty_panel(
            "No alerts yet.",
            "Create one above; it is checked even when this page is closed.",
            height=96,
        )
        return
    now = int(time.time() * 1000)
    for alert in saved:
        with st.container(
            key=f"alert_row_{alert.id}",
            horizontal=True,
            vertical_alignment="center",
            gap="small",
        ):
            st.html(alert_row_html(alert, now), width="stretch")
            st.button(
                "Delete",
                key=f"alert_delete_{alert.id}",
                type="tertiary",
                on_click=partial(_delete, who, alert.id),
            )


def alert_row_html(alert: Alert, now_ms: int) -> str:
    """One alert: symbol, condition and status (escaped; pure for tests)."""
    channels = " · ".join(["In app", *(c.capitalize() for c in alert.channels)])
    demo = ' <span class="mm-tag">Demo data</span>' if alert.demo else ""
    return (
        '<div class="mm-alert">'
        f'<p class="mm-alert-title"><span class="mm-sym">{escape(alert.symbol)}</span> '
        f"{escape(engine.describe(alert))}{demo}</p>"
        f'<p class="mm-alert-meta">{escape(status_text(alert, now_ms))} · {escape(channels)}</p>'
        "</div>"
    )


def _notifications(recent: list[Event], who: Identity) -> None:
    unread = sum(1 for e in recent if e.read_at is None)
    with st.container(key="alert_events_head", horizontal=True, vertical_alignment="bottom"):
        subsection("Notifications", f"{unread} new" if unread else "All caught up")
        if unread:
            st.button("Mark all as read", key="alert_mark_read", type="tertiary",
                      on_click=_mark_read, args=(who,))  # fmt: skip
    if not recent:
        components.fineprint("Triggered alerts appear here, and as a notice on any open page.")
        return
    st.html('<ul class="mm-events">' + "".join(event_html(e) for e in recent) + "</ul>")


def event_html(event: Event) -> str:
    when = datetime.fromtimestamp(event.created_at / 1000, tz=UTC).strftime("%b %d, %H:%M UTC")
    state = "" if event.read_at else ' data-unread="true"'
    return (
        f'<li class="mm-event"{state}><span>{escape(event.message)}</span>'
        f'<time datetime="{datetime.fromtimestamp(event.created_at / 1000, tz=UTC).isoformat()}">'
        f"{escape(when)}</time></li>"
    )


def _webhook_settings(who: Identity, url: str | None) -> None:
    panel = st.expander("Webhook delivery", key="webhook_panel", on_change="rerun")
    if not panel.open:
        return
    with panel:
        components.fineprint(
            "Momentum POSTs JSON to this HTTPS URL when an alert with Webhook ticked fires. "
            "Each request is signed: check the Momentum-Signature header with your secret "
            "(HMAC-SHA256 of the timestamp, a dot and the body)."
        )
        if url:
            components.fineprint(f"Sending to {url}")
        st.text_input(
            "Webhook URL", key="webhook_url", placeholder="https://example.com/hooks/momentum"
        )
        with st.container(horizontal=True, gap="small"):
            st.button("Save webhook", key="webhook_save", on_click=_save_webhook, args=(who,))
            if url:
                st.button("Send test", key="webhook_test", type="tertiary",
                          on_click=_send_test, args=(who, "webhook"))  # fmt: skip
                st.button("New secret", key="webhook_rotate", type="tertiary",
                          on_click=_rotate_secret, args=(who,))  # fmt: skip
                st.button("Remove", key="webhook_remove", type="tertiary",
                          on_click=_remove_webhook, args=(who,))  # fmt: skip
        secret = st.session_state.pop(SECRET_KEY, None)
        if secret:
            components.fineprint("Signing secret (shown once):")
            st.code(secret, language=None)


# --------------------------------------------------------------------------- toasts


@st.fragment(run_every=POLL_SECONDS)
def notification_toasts() -> None:
    """Every 30 s, show new notifications for this viewer as toasts (once each)."""
    who = session.identity_of_session()
    if who.owner is None:
        return
    repo = _repo()
    if repo is None:
        return
    cursor = st.session_state.get(_CURSOR_KEY)
    if cursor is None:
        # On a fresh page, mention what arrived in the last day and is still unread.
        cursor = int(time.time() * 1000) - 24 * 60 * 60 * 1000
    try:
        fresh = repo.unread_since(who.owner, int(cursor))
    except StoreError:
        return
    if fresh:
        st.toast(plain_markdown(toast_text(fresh)), icon=":material/notifications:")
    if fresh:
        st.session_state[_CURSOR_KEY] = max(e.created_at for e in fresh)
    else:
        st.session_state.setdefault(_CURSOR_KEY, int(cursor))
