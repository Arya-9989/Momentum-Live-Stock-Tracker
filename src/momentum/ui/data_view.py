""" "Your data": download everything Momentum keeps for this viewer, or erase it.

Shown only to viewers with a saved identity (a device or an account). The
download is generated when clicked (``st.download_button`` with a callable), so
opening the popover costs nothing. Deleting needs an explicit confirmation.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Final

import streamlit as st

from momentum import audit, privacy
from momentum.identity import Identity
from momentum.store import StoreError
from momentum.ui import components, saved, session

logger = logging.getLogger(__name__)

NOTICE_KEY: Final = "_data_notice"
CONFIRM_KEY: Final = "data_delete_confirm"


def export_bytes(who: Identity) -> bytes:
    """The JSON download (generated on click)."""
    store = session.open_store()
    if store is None or who.owner is None:
        return b'{"error": "Saved data is unavailable right now."}\n'
    payload = privacy.export_json(store, who.owner)
    audit.record("data.exported", who.owner, bytes=len(payload))
    return payload


def erase(who: Identity) -> None:
    store = session.open_store()
    if store is None or who.owner is None:
        st.session_state[NOTICE_KEY] = "Saved data is unavailable right now; nothing was deleted."
        return
    try:
        deleted = privacy.erase(store, who.owner)
    except StoreError as exc:
        logger.warning("Could not erase saved data: %r", exc)
        st.session_state[NOTICE_KEY] = "Saved data is unavailable right now; nothing was deleted."
        return
    audit.record("data.deleted", who.owner, existed=deleted)
    # Forget the session's copies, so nothing deleted reappears on screen.
    for key in (saved.WATCHLIST_KEY, saved.WIDGET_KEY, "_watchlist_loaded"):
        st.session_state.pop(key, None)
    st.session_state[CONFIRM_KEY] = False
    st.session_state[NOTICE_KEY] = (
        "Deleted your watchlist, alerts, delivery settings, notifications and portfolio."
        if deleted
        else "There was nothing saved to delete."
    )


def your_data(who: Identity) -> None:
    if who.owner is None:
        return
    popover = st.popover(
        "Your data", icon=":material/folder_shared:", type="tertiary", key="your_data",
        on_change="rerun",
    )  # fmt: skip
    notice = st.session_state.pop(NOTICE_KEY, None)
    if notice:
        components.fineprint(notice)
    if not popover.open:
        return
    with popover:
        components.fineprint(
            "Everything Momentum keeps for you: your watchlist, alerts, delivery settings, "
            "notifications and portfolio. Webhook secrets are never included."
        )
        st.download_button(
            "Download my data (JSON)",
            data=partial(export_bytes, who),
            file_name="momentum-data.json",
            mime="application/json",
            on_click="ignore",
            icon=":material/download:",
            key="data_export",
        )
        components.hairline()
        confirmed = st.checkbox(
            "I understand this permanently deletes my watchlist, alerts and portfolio.",
            key=CONFIRM_KEY,
        )
        st.button(
            "Delete my data",
            key="data_delete",
            type="primary",
            disabled=not confirmed,
            on_click=erase,
            args=(who,),
        )
