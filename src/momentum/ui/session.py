"""The viewer's identity and saved data inside a Streamlit script run.

This is the only module that reads ``st.user`` and ``st.context.cookies``.
The store is opened lazily; if it cannot be opened (a read-only disk, Postgres
down) the app keeps working with session-only data and says so.
"""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from momentum import identity, settings, store
from momentum.identity import Identity
from momentum.store import Store, StoreError

logger = logging.getLogger(__name__)

_IDENTITY_KEY = "_mm_identity"
SESSION_IDENTITY = identity.SESSION


def auth_configured() -> bool:
    """Whether OIDC sign-in (``[auth]`` in secrets) is set up and usable."""
    try:
        configured = "auth" in st.secrets
    except Exception:  # no secrets file at all
        return False
    if not configured:
        return False
    try:
        import authlib  # noqa: F401
    except ImportError:
        logger.warning("[auth] is configured but Authlib is not installed; sign-in is off")
        return False
    return True


def _user_claims() -> dict[str, Any] | None:
    if not auth_configured():
        return None
    try:
        if not st.user.is_logged_in:
            return None
        # Claims only: tokens (if exposed at all) are never copied into session state.
        claims = {key: st.user.get(key) for key in st.user if key != "tokens"}
        return claims | {"is_logged_in": True}
    except Exception as exc:
        logger.warning("Could not read st.user: %r", exc)
        return None


def current_identity() -> Identity:
    """Resolved once per script run and kept in session state for the callbacks."""
    try:
        keys = identity.device_keys(settings.load())
    except (OSError, ValueError) as exc:
        logger.error("Signing keys unavailable, device identity disabled: %r", exc)
        keys = ()
    who = identity.resolve(_user_claims(), dict(st.context.cookies), keys)
    st.session_state[_IDENTITY_KEY] = who
    return who


def identity_of_session() -> Identity:
    """The identity resolved for this run (callbacks run before the script body)."""
    who = st.session_state.get(_IDENTITY_KEY)
    return who if isinstance(who, Identity) else current_identity()


def open_store() -> Store | None:
    """The process-wide store, or None (logged once per session) if it is unavailable."""
    try:
        return store.get_store()
    except (StoreError, OSError, ValueError) as exc:
        if not st.session_state.get("_mm_store_warned"):
            logger.error("Saved data is unavailable: %r", exc)
            st.session_state["_mm_store_warned"] = True
        return None
