"""End to end: OIDC sign-in (Keycloak) keys the watchlist to the account and enables email.

Needs Keycloak and Mailpit from compose.yaml and a live ``serve.py`` configured for both::

    docker compose -p roadmap-momentum --profile oidc up -d keycloak mailpit
    REALM=http://127.0.0.1:5424/realms/momentum
    MOMENTUM_DEMO=1 MOMENTUM_ALERT_WORKER=external MOMENTUM_DATA_DIR=/tmp/m \\
      MOMENTUM_SECRET_KEY=$(python -c "print('k' * 40)") \\
      MOMENTUM_OIDC_SERVER_METADATA_URL=$REALM/.well-known/openid-configuration \\
      MOMENTUM_OIDC_CLIENT_ID=momentum MOMENTUM_OIDC_CLIENT_SECRET=momentum-local-client-secret \\
      MOMENTUM_OIDC_REDIRECT_URI=http://127.0.0.1:5425/oauth2callback \\
      MOMENTUM_SMTP_HOST=127.0.0.1 MOMENTUM_SMTP_PORT=5422 MOMENTUM_SMTP_STARTTLS=0 \\
      MOMENTUM_SMTP_FROM="Momentum <alerts@momentum.test>" \\
      uv run streamlit run serve.py --server.port 5425
    MOMENTUM_E2E_URL=http://127.0.0.1:5425 MOMENTUM_E2E_DATA_DIR=/tmp/m MOMENTUM_E2E_OIDC=1 \\
      MOMENTUM_TEST_MAILPIT_API=http://127.0.0.1:5423 uv run --group screenshots pytest tests/e2e
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

import pytest

from tests.e2e.helpers import add_watchlist_symbol, open_app, rail_symbols, settle

pytestmark = pytest.mark.e2e

MAILPIT = os.environ.get("MOMENTUM_TEST_MAILPIT_API", "").rstrip("/")


@pytest.fixture
def oidc() -> None:
    if os.environ.get("MOMENTUM_E2E_OIDC") != "1" or not MAILPIT:
        pytest.skip("set MOMENTUM_E2E_OIDC=1 and MOMENTUM_TEST_MAILPIT_API (see the docstring)")


def sign_in(page: Any, username: str, password: str) -> None:
    button = page.get_by_role("button", name="Sign in")
    try:
        button.wait_for(timeout=20_000)
    except Exception:
        raise AssertionError(page.inner_text("body")[-600:]) from None
    button.click()
    page.wait_for_url("**/realms/momentum/**", timeout=30_000)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#kc-login")
    page.wait_for_url("http://127.0.0.1:5425/**", timeout=30_000)
    open_app(page)


def test_account_owns_its_watchlist_and_gets_email(e2e_url: str, browser: Any, oidc: None) -> None:
    data_dir = os.environ["MOMENTUM_E2E_DATA_DIR"]
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    open_app(page, f"{e2e_url}/?view=Watchlist")
    assert "Saved in this browser. Sign in to use it on other devices." in page.inner_text("body")
    add_watchlist_symbol(page, "TSLA")  # saved to the device, not to the account

    sign_in(page, "viewer", "viewer-local-password")
    page.goto(f"{e2e_url}/?view=Watchlist")
    open_app(page)
    body = page.inner_text("body")
    assert "Saved to viewer@momentum.test." in body
    add_watchlist_symbol(page, "NVDA")

    # Another browser profile, same account: the list follows the sign-in.
    other = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    open_app(other, f"{e2e_url}/?view=Watchlist")  # "Sign in" sits under the list
    sign_in(other, "viewer", "viewer-local-password")
    other.goto(f"{e2e_url}/?view=Overview")
    open_app(other)
    assert "NVDA" in rail_symbols(other)
    assert "TSLA" not in rail_symbols(other)

    # Email is offered to the verified address; the alert fires into Mailpit.
    page.goto(f"{e2e_url}/?view=Alerts&ticker=MSFT")
    open_app(page)
    email = page.locator(".st-key-alert_email")
    assert email.locator("input").is_enabled()
    email.get_by_text("Email").click()
    page.locator(".st-key-alert_level input").fill("600")
    page.locator(".st-key-alert_level input").press("Enter")
    settle(page)
    page.get_by_role("button", name="Create alert").click()
    settle(page)
    assert "Alert created: MSFT Price crosses above $600.00." in page.inner_text("body")

    # The replay stands in for the external alert worker, so it gets the server's mail setup.
    env = {
        **os.environ,
        "MOMENTUM_DATA_DIR": data_dir,
        "MOMENTUM_SMTP_HOST": "127.0.0.1",
        "MOMENTUM_SMTP_PORT": os.environ.get("MOMENTUM_TEST_MAILPIT_SMTP_PORT", "5422"),
        "MOMENTUM_SMTP_STARTTLS": "0",
        "MOMENTUM_SMTP_FROM": "Momentum <alerts@momentum.test>",
    }
    result = subprocess.run(
        [sys.executable, "-m", "momentum.alerts", "replay", "MSFT", "590", "605"],
        env=env, capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    assert "deliveries: 1 sent" in result.stdout, result.stdout + result.stderr
    query = urllib.parse.quote("to:viewer@momentum.test subject:MSFT")
    found: dict[str, Any] = {}
    for _ in range(10):
        with urllib.request.urlopen(f"{MAILPIT}/api/v1/search?query={query}", timeout=5) as r:
            found = json.load(r)
        if found.get("messages_count"):
            break
        time.sleep(0.5)
    assert found["messages_count"] >= 1
    assert found["messages"][0]["Subject"] == "Alert: MSFT rose above $600.00 (now $605.00)."
    context.close()
    other.context.close()


def test_unverified_email_is_never_used(e2e_url: str, browser: Any, oidc: None) -> None:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    open_app(page, f"{e2e_url}/?view=Watchlist")
    sign_in(page, "unverified", "unverified-local-password")
    page.goto(f"{e2e_url}/?view=Watchlist")
    open_app(page)
    body = page.inner_text("body")
    assert "Saved to Una Verified." in body  # the name, not the unverified address
    page.goto(f"{e2e_url}/?view=Alerts")
    open_app(page)
    assert page.locator(".st-key-alert_email input").is_disabled()
    context.close()
