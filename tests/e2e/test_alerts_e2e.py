"""End to end: create an alert in the browser, fire it, see the toast and the signed webhook.

Needs a live ``serve.py`` in demo mode whose alert loop is driven from here::

    MOMENTUM_DEMO=1 MOMENTUM_ALERT_WORKER=external MOMENTUM_WEBHOOK_ALLOW_PRIVATE=1 \\
      MOMENTUM_DATA_DIR=/tmp/m uv run streamlit run serve.py --server.port 5425
    MOMENTUM_E2E_URL=http://127.0.0.1:5425 MOMENTUM_E2E_DATA_DIR=/tmp/m \\
      uv run --group screenshots pytest tests/e2e

The prices come from ``python -m momentum.alerts replay``, so the run is
deterministic: 240 (armed), then 251 (crosses 250).
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from momentum.alerts.delivery import verify_signature
from tests.e2e.helpers import open_app, settle

pytestmark = pytest.mark.e2e
RECEIVER_PORT = 5426


class Hook(http.server.BaseHTTPRequestHandler):
    received: list[tuple[dict[str, str], bytes]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        Hook.received.append(({k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def receiver() -> Iterator[str]:
    Hook.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", RECEIVER_PORT), Hook)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{RECEIVER_PORT}/momentum"
    server.shutdown()
    server.server_close()


def test_alert_fires_into_the_page_and_the_webhook(
    e2e_url: str, browser: Any, receiver: str
) -> None:
    data_dir = os.environ.get("MOMENTUM_E2E_DATA_DIR", "").strip()
    if not data_dir:
        pytest.skip("set MOMENTUM_E2E_DATA_DIR to the data directory of the running app")
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    open_app(page, f"{e2e_url}/?view=Alerts&ticker=AAPL")

    # Webhook first, so the alert can use it.
    page.get_by_text("Webhook delivery").click()
    settle(page)
    page.locator(".st-key-webhook_url input").fill(receiver)
    page.get_by_role("button", name="Save webhook").click()
    settle(page)
    secret = page.locator("code").filter(has_text="whsec_").inner_text().strip()
    assert secret.startswith("whsec_")

    page.locator(".st-key-alert_level input").fill("250")
    page.locator(".st-key-alert_level input").press("Enter")
    settle(page)
    page.locator(".st-key-alert_webhook").get_by_text("Webhook").click()
    page.get_by_role("button", name="Create alert").click()
    settle(page)
    assert "Alert created: AAPL Price crosses above $250.00." in page.inner_text("body")

    env = {**os.environ, "MOMENTUM_DATA_DIR": data_dir, "MOMENTUM_WEBHOOK_ALLOW_PRIVATE": "1"}
    replay = subprocess.run(
        [sys.executable, "-m", "momentum.alerts", "replay", "AAPL", "240", "251"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "fired: AAPL rose above $250.00 (now $251.00)." in replay.stdout
    assert "deliveries: 1 sent" in replay.stdout

    ((headers, body),) = Hook.received
    assert verify_signature(secret, headers["momentum-signature"], body)
    payload = json.loads(body)
    assert payload["type"] == "alert.triggered"
    assert payload["alert"]["symbol"] == "AAPL"
    assert payload["observation"]["value"] == 251.0

    # The open page polls every 30 s and shows the notification as a toast.
    toast = page.locator('[data-testid="stToast"]').filter(has_text="AAPL rose above $250.00")
    toast.wait_for(timeout=45_000)
    page.context.close()
