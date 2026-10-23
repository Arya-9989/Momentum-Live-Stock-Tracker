"""The watchlist as this session sees it, kept in step with the store.

``st.session_state[WATCHLIST_KEY]`` is the session's copy, read by the rail and
the Watchlist tab. ``sync_watchlist`` reloads it at the start of every run when
the saved version changed (another tab, another device after sign-in), and
``on_watchlist_change`` turns an edit of the multiselect into add/remove
operations on the store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal

import streamlit as st

from momentum import config, data
from momentum.data import DataError, DataSourceError
from momentum.identity import Identity
from momentum.store import StoreError
from momentum.ui import session

logger = logging.getLogger(__name__)

WATCHLIST_KEY: Final = "_watchlist"
WIDGET_KEY: Final = "watchlist_symbols"
_LOADED_KEY: Final = "_watchlist_loaded"  # (owner id, version) last loaded from the store
_STATUS_KEY: Final = "_watchlist_status"
NOTICE_KEY: Final = "_watchlist_notice"

SaveStatus = Literal["account", "device", "session", "unavailable"]


@dataclass(frozen=True, slots=True)
class StatusCopy:
    text: str
    sign_in: bool  # offer "Sign in" next to it


def status_copy(status: SaveStatus, who: Identity, *, can_sign_in: bool) -> StatusCopy:
    """One quiet line saying where the watchlist lives."""
    if status == "account":
        label = who.email or who.name or "your account"
        return StatusCopy(f"Saved to {label}.", sign_in=False)
    if status == "device":
        tail = " Sign in to use it on other devices." if can_sign_in else ""
        return StatusCopy(f"Saved in this browser.{tail}", sign_in=can_sign_in)
    if status == "unavailable":
        return StatusCopy(
            "Saving is unavailable right now, so changes last for this session only.",
            sign_in=False,
        )
    tail = " Sign in to save it." if can_sign_in else ""
    return StatusCopy(f"Kept for this session only.{tail}", sign_in=can_sign_in)


def watchlist() -> list[str]:
    """This session's watchlist (the defaults until something else is loaded)."""
    raw = st.session_state.get(WATCHLIST_KEY, list(config.DEFAULT_WATCHLIST))
    symbols, _ = data.parse_symbol_list(raw, config.MAX_WATCHLIST)
    return symbols


def status() -> SaveStatus:
    value = st.session_state.get(_STATUS_KEY, "session")
    return value if value in {"account", "device", "session", "unavailable"} else "session"


def _set(symbols: list[str], loaded: tuple[str, int] | None) -> None:
    st.session_state[WATCHLIST_KEY] = symbols
    st.session_state[_LOADED_KEY] = loaded


def sync_watchlist(who: Identity) -> list[str]:
    """Load the saved list into the session when it is new or changed elsewhere."""
    owner = who.owner
    if owner is None:
        st.session_state[_STATUS_KEY] = "session"
        st.session_state.setdefault(WATCHLIST_KEY, list(config.DEFAULT_WATCHLIST))
        return watchlist()
    store = session.open_store()
    if store is None:
        st.session_state[_STATUS_KEY] = "unavailable"
        st.session_state.setdefault(WATCHLIST_KEY, list(config.DEFAULT_WATCHLIST))
        return watchlist()
    try:
        saved = store.watchlists.get(owner)
        if saved is not None:
            store.watchlists.touch(owner, saved.last_seen_at)
    except StoreError as exc:
        logger.warning("Could not load the watchlist: %r", exc)
        st.session_state[_STATUS_KEY] = "unavailable"
        st.session_state.setdefault(WATCHLIST_KEY, list(config.DEFAULT_WATCHLIST))
        return watchlist()
    st.session_state[_STATUS_KEY] = who.kind
    loaded = st.session_state.get(_LOADED_KEY)
    if saved is None:
        # Nothing saved yet: show the defaults (a switch of owner resets the list).
        if WATCHLIST_KEY not in st.session_state or (loaded and loaded[0] != owner.id):
            _set(list(config.DEFAULT_WATCHLIST), None)
    elif loaded != (owner.id, saved.version):
        _set(list(saved.symbols), (owner.id, saved.version))
    return watchlist()


def symbol_exists(symbol: str, demo: bool) -> bool:
    """False only when the data source says the symbol has no data.

    A network failure is not proof that a symbol does not exist, so it counts
    as "exists": the row then shows "Quote unavailable" until the source answers.
    """
    try:
        data.cached_info(symbol, demo)
    except DataSourceError:
        return True
    except DataError:
        return False
    return True


def _demo() -> bool:
    if "demo" in st.session_state:
        return bool(st.session_state["demo"])
    raw = st.query_params.get("demo")
    return config.is_truthy(raw) if raw is not None else config.demo_mode_enabled()


def on_watchlist_change() -> None:
    """Apply an edit of the watchlist multiselect: validate, save, then show the result."""
    raw = list(st.session_state.get(WIDGET_KEY) or [])
    valid, rejected = data.parse_symbol_list(raw, limit=len(raw) or 1)
    before = watchlist()
    removed = [s for s in before if s not in valid]
    added = [s for s in valid if s not in before]
    demo = _demo()
    unknown = [s for s in added if not symbol_exists(s, demo)]
    added = [s for s in added if s not in unknown]

    who = session.identity_of_session()
    over: list[str] = []
    store = session.open_store() if who.owner is not None else None
    saved = False
    if who.owner is not None and store is not None:
        try:
            change = store.watchlists.apply(who.owner, add=added, remove=removed, base=before)
            _set(list(change.watchlist.symbols), (who.owner.id, change.watchlist.version))
            over = list(change.over_limit)
            saved = True
        except StoreError as exc:
            logger.warning("Could not save the watchlist: %r", exc)
            st.session_state[_STATUS_KEY] = "unavailable"
    if not saved:
        kept = [s for s in before if s not in removed]
        room = max(config.MAX_WATCHLIST - len(kept), 0)
        over = added[room:]
        st.session_state[WATCHLIST_KEY] = kept + added[:room]
    st.session_state[WIDGET_KEY] = watchlist()
    st.session_state[NOTICE_KEY] = edit_notice(unknown, rejected, over)


def edit_notice(unknown: list[str], rejected: list[str], over: list[str]) -> str | None:
    """Why some of an edit was not applied, in one line (None when all of it was)."""
    parts = []
    if unknown:
        parts.append(f"No data for {', '.join(unknown)}, so not added")
    if rejected:
        parts.append(f"not ticker symbols: {', '.join(rejected)}")
    if over:
        parts.append(
            f"the watchlist holds {config.MAX_WATCHLIST} symbols ({', '.join(over)} left out)"
        )
    if not parts:
        return None
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."
