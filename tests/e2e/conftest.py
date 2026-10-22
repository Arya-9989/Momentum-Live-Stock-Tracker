"""Fixtures for the browser tests: a live app URL and one Chromium per session."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def e2e_url() -> str:
    url = os.environ.get("MOMENTUM_E2E_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("set MOMENTUM_E2E_URL to a running serve.py to run browser tests")
    return url


@pytest.fixture(scope="session")
def browser(e2e_url: str) -> Iterator[object]:
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        instance = pw.chromium.launch()
        yield instance
        instance.close()
