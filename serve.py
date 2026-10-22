"""Production entry point: ``streamlit run serve.py`` (the Docker image runs this).

It serves the same app as ``streamlit_app.py`` through ``st.App``, Streamlit's
ASGI entry point, adding compressed static assets, a robots.txt and a proper
document title and description, and the signed device cookie that keys saved
watchlists (see ``momentum.server`` and ``momentum.identity``). ``streamlit run``
detects the ``app = st.App(...)`` assignment below and runs it as an ASGI app,
so ``STREAMLIT_*`` settings and ``.streamlit/config.toml`` apply as usual.

Requires the ``momentum`` package to be installed (``uv sync`` or the image).
"""

from __future__ import annotations

import os

import streamlit as st

from momentum.server import MIDDLEWARE, ROUTES, oidc_secrets

app = st.App(
    "streamlit_app.py",
    routes=ROUTES,
    middleware=MIDDLEWARE,
    secrets=oidc_secrets(os.environ) or None,
)
