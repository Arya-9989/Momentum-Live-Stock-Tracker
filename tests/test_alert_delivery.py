"""Webhook and email delivery: SSRF defences, signatures, retries, and the outbox drain."""

from __future__ import annotations

import http.client
import http.server
import json
import os
import smtplib
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import replace
from email.message import EmailMessage
from typing import Any

import pytest

from momentum.alerts import delivery
from momentum.alerts.delivery import (
    DeliveryError,
    EmailSender,
    UnsafeUrlError,
    WebhookSender,
    check_url,
    sign,
    verify_signature,
)
from momentum.settings import SmtpSettings
from momentum.store.alerts import Channels, DeliveryJob, Event, SqlAlertRepository
from momentum.store.db import Database
from momentum.store.owners import Owner

ACCOUNT = Owner("acct_" + "c" * 40, "account")
EVENT = Event(
    id="evt_test123",
    owner_id=ACCOUNT.id,
    alert_id="alt_abc",
    kind="triggered",
    symbol="AAPL",
    condition="price_above",
    threshold=200.0,
    value=201.3,
    message="AAPL rose above $200.00 (now $201.30).",
    observed_at=1_700_000_000_000,
    created_at=1_700_000_060_000,
    read_at=None,
)


def public(_host: str, _port: int) -> list[str]:
    return ["93.184.215.14"]


def resolving_to(*addresses: str) -> delivery.Resolver:
    return lambda _host, _port: list(addresses)


# --------------------------------------------------------------------------- URL checks


class TestCheckUrl:
    def test_public_https_is_allowed(self) -> None:
        target = check_url("https://hooks.example.com:8443/in?x=1", resolver=public)
        assert (target.host, target.port, target.path) == ("hooks.example.com", 8443, "/in?x=1")
        assert target.addresses == ("93.184.215.14",)

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("http://hooks.example.com/", "https://"),
            ("ftp://hooks.example.com/", "https://"),
            ("javascript:alert(1)", "https://"),
            ("https://user:pass@hooks.example.com/", "user name or password"),
            ("https:///nohost", "no host"),
            ("https://hooks.example.com:99999/", "invalid port"),
            ("https://hooks.example.com/a b", "spaces"),
            ("https://hooks.example.com/" + "a" * 3000, "too long"),
            ("https://metadata.google.internal/computeMetadata/v1/", "not allowed"),
        ],
    )
    def test_rejected_urls(self, url: str, message: str) -> None:
        with pytest.raises(UnsafeUrlError, match=message):
            check_url(url, resolver=public)

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.5",
            "172.16.3.4",
            "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "224.0.0.1",
        ],
    )
    def test_non_public_addresses_are_rejected(self, address: str) -> None:
        with pytest.raises(UnsafeUrlError, match="public internet address"):
            check_url("https://evil.example.com/", resolver=resolving_to(address))

    def test_one_private_answer_among_public_ones_is_enough_to_refuse(self) -> None:
        with pytest.raises(UnsafeUrlError):
            check_url("https://x.example.com/", resolver=resolving_to("93.184.215.14", "10.1.1.1"))

    def test_development_override(self) -> None:
        target = check_url(
            "http://127.0.0.1:5426/hook", allow_private=True, resolver=resolving_to("127.0.0.1")
        )
        assert target.scheme == "http"

    def test_unresolvable_host_is_transient(self) -> None:
        with pytest.raises(DeliveryError) as info:
            check_url("https://nope.invalid/", resolver=resolving_to())
        assert not info.value.permanent
        with pytest.raises(DeliveryError, match="Cannot resolve"):
            delivery._resolve("does-not-exist.invalid", 443)


class TestSignature:
    def test_round_trip_and_tampering(self) -> None:
        body = b'{"id":"evt_1"}'
        header = sign("whsec_x", 1_700_000_000, body)
        assert header.startswith("t=1700000000,v1=")
        assert verify_signature("whsec_x", header, body, now=1_700_000_100)
        assert not verify_signature("whsec_x", header, body + b" ", now=1_700_000_100)
        assert not verify_signature("whsec_y", header, body, now=1_700_000_100)
        assert not verify_signature("whsec_x", header, body, now=1_700_001_000)  # replayed late
        assert not verify_signature("whsec_x", "t=abc,v1=00", body)
        assert not verify_signature("whsec_x", "garbage", body)

    def test_payload_shape(self) -> None:
        payload = json.loads(delivery.webhook_payload(EVENT))
        assert payload["type"] == "alert.triggered"
        assert payload["alert"] == {
            "id": "alt_abc",
            "symbol": "AAPL",
            "condition": "price_above",
            "threshold": 200.0,
        }
        assert payload["observation"]["value"] == 201.3
        assert payload["created_at"].endswith("+00:00")
        test = json.loads(delivery.webhook_payload(_test_event()))
        assert test["type"] == "alert.test"
        assert test["alert"] is None


def _test_event() -> Event:
    return Event("evt_t", ACCOUNT.id, None, "test", None, None, None, None, "Test", None, 1, None)


# --------------------------------------------------------------------------- webhook sender


class Receiver(http.server.BaseHTTPRequestHandler):
    status = 204
    delay_s = 0.0
    received: list[tuple[dict[str, str], bytes]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        Receiver.received.append(({k.lower(): v for k, v in self.headers.items()}, body))
        time.sleep(self.delay_s)
        self.send_response(self.status)
        if self.status == 302:
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def receiver() -> Iterator[str]:
    Receiver.received = []
    Receiver.status = 204
    Receiver.delay_s = 0.0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/hook"
    server.shutdown()
    server.server_close()


def job(url: str, secret: str = "whsec_test", event: Event = EVENT) -> DeliveryJob:
    return DeliveryJob(
        "dlv_1", "webhook", 1, event, Channels(webhook_url=url, webhook_secret=secret)
    )


class TestWebhookSender:
    def test_delivers_a_signed_request(self, receiver: str) -> None:
        WebhookSender(allow_private=True).send(job(receiver))
        ((headers, body),) = Receiver.received
        assert headers["content-type"] == "application/json"
        assert headers["momentum-event-id"] == "evt_test123"
        assert headers["user-agent"].startswith("Momentum-Alerts/")
        assert verify_signature("whsec_test", headers["momentum-signature"], body)
        assert json.loads(body)["message"] == EVENT.message

    def test_server_errors_are_retried_client_errors_are_not(self, receiver: str) -> None:
        Receiver.status = 503
        with pytest.raises(DeliveryError, match="HTTP 503") as info:
            WebhookSender(allow_private=True).send(job(receiver))
        assert not info.value.permanent
        Receiver.status = 404
        with pytest.raises(DeliveryError, match="HTTP 404") as info:
            WebhookSender(allow_private=True).send(job(receiver))
        assert info.value.permanent

    def test_redirects_are_not_followed(self, receiver: str) -> None:
        Receiver.status = 302
        with pytest.raises(DeliveryError, match="HTTP 302"):
            WebhookSender(allow_private=True).send(job(receiver))
        assert len(Receiver.received) == 1  # nothing went to the Location

    def test_slow_endpoints_time_out(self, receiver: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(delivery, "TIMEOUT_S", 0.2)
        Receiver.delay_s = 1.0
        with pytest.raises(DeliveryError, match="Could not reach the webhook") as info:
            WebhookSender(allow_private=True).send(job(receiver))
        assert not info.value.permanent

    def test_private_targets_are_refused_by_default(self, receiver: str) -> None:
        with pytest.raises(UnsafeUrlError):
            WebhookSender().send(job(receiver.replace("http://", "https://")))
        assert Receiver.received == []

    def test_dns_rebinding_cannot_swap_the_address(self) -> None:
        """The address checked is the address connected to."""
        answers = [["93.184.215.14"], ["127.0.0.1"]]
        connected: list[str] = []

        class Refused(http.client.HTTPConnection):
            def request(self, *args: Any, **kwargs: Any) -> None:
                raise ConnectionRefusedError

        def connector(target: delivery.WebhookTarget, address: str) -> http.client.HTTPConnection:
            connected.append(address)
            return Refused("unused")

        sender = WebhookSender(resolver=lambda h, p: answers.pop(0), connector=connector)
        with pytest.raises(DeliveryError):
            sender.send(job("https://rebind.example.com/"))
        assert connected == ["93.184.215.14"]
        # The next attempt re-resolves; the now-private answer is refused before connecting.
        with pytest.raises(UnsafeUrlError):
            sender.send(job("https://rebind.example.com/"))
        assert connected == ["93.184.215.14"]

    def test_removed_webhook_fails_permanently(self) -> None:
        with pytest.raises(DeliveryError) as info:
            WebhookSender().send(DeliveryJob("d", "webhook", 1, EVENT, Channels()))
        assert info.value.permanent


# --------------------------------------------------------------------------- email


SMTP = SmtpSettings(
    host="smtp.example.com",
    port=587,
    sender="Momentum <alerts@momentum.example>",
    starttls=True,
    ssl=False,
    username="user",
    password="pass",
)


class FakeSMTP:
    instances: list[FakeSMTP] = []  # noqa: RUF012
    fail: Exception | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.args = (host, port, timeout)
        self.calls: list[str] = []
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: object) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if FakeSMTP.fail is not None:
            raise FakeSMTP.fail
        self.sent.append(message)


@pytest.fixture
def fake_smtp() -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.fail = None
    return FakeSMTP


def email_job(event: Event = EVENT, email: str | None = "me@example.com") -> DeliveryJob:
    return DeliveryJob("dlv_e", "email", 1, event, Channels(email=email))


class TestEmail:
    def test_message(self, fake_smtp: type[FakeSMTP]) -> None:
        EmailSender(SMTP, factory=fake_smtp).send(email_job())
        (client,) = fake_smtp.instances
        assert client.args == ("smtp.example.com", 587, 10.0)
        assert client.calls == ["starttls", "login user", "quit"]
        (message,) = client.sent
        assert message["To"] == "me@example.com"
        assert message["Subject"] == "Alert: AAPL rose above $200.00 (now $201.30)."
        assert message["Message-ID"] == "<evt_test123@momentum.example>"  # stable across retries
        assert "Not financial advice" in message.get_content()

    def test_failures(self, fake_smtp: type[FakeSMTP]) -> None:
        fake_smtp.fail = smtplib.SMTPRecipientsRefused({"me@example.com": (550, b"no")})
        with pytest.raises(DeliveryError) as info:
            EmailSender(SMTP, factory=fake_smtp).send(email_job())
        assert info.value.permanent
        fake_smtp.fail = smtplib.SMTPServerDisconnected("gone")
        with pytest.raises(DeliveryError) as info:
            EmailSender(SMTP, factory=fake_smtp).send(email_job())
        assert not info.value.permanent

    def test_no_address_is_permanent(self) -> None:
        with pytest.raises(DeliveryError) as info:
            EmailSender(SMTP).message(email_job(email=None))
        assert info.value.permanent

    def test_header_injection_is_impossible(self, fake_smtp: type[FakeSMTP]) -> None:
        hostile = replace(EVENT, message="AAPL\r\nBcc: victim@example.com")
        EmailSender(SMTP, factory=fake_smtp).send(email_job(hostile))
        (message,) = fake_smtp.instances[0].sent
        assert message["Bcc"] is None
        assert "\n" not in message["Subject"]


# --------------------------------------------------------------------------- outbox drain


class Recorder:
    def __init__(self, error: DeliveryError | None = None) -> None:
        self.error = error
        self.jobs: list[DeliveryJob] = []

    def send(self, job: DeliveryJob) -> None:
        self.jobs.append(job)
        if self.error:
            raise self.error


def queue(repo: SqlAlertRepository) -> None:
    repo.set_email(ACCOUNT, "me@example.com")
    repo.set_webhook(ACCOUNT, "https://hooks.example.com/x")
    repo.send_test(ACCOUNT, "email")
    repo.send_test(ACCOUNT, "webhook")


def test_dispatch(database: Database) -> None:
    repo = SqlAlertRepository(database)
    queue(repo)
    email, webhook = Recorder(), Recorder(DeliveryError("HTTP 503"))
    stats = delivery.dispatch(repo, {"email": email, "webhook": webhook})
    assert (stats.sent, stats.retried, stats.failed) == (1, 1, 0)
    assert delivery.dispatch(repo, {"email": email, "webhook": webhook}).sent == 0  # backing off


def test_dispatch_without_a_sender_fails_permanently(database: Database) -> None:
    repo = SqlAlertRepository(database)
    queue(repo)
    stats = delivery.dispatch(repo, {"webhook": Recorder()})
    assert (stats.sent, stats.failed) == (1, 1)


# --------------------------------------------------------------------------- Mailpit

MAILPIT_API = os.environ.get("MOMENTUM_TEST_MAILPIT_API", "").rstrip("/")
MAILPIT_SMTP_PORT = int(os.environ.get("MOMENTUM_TEST_MAILPIT_SMTP_PORT", "5422"))


@pytest.mark.mailpit
@pytest.mark.skipif(not MAILPIT_API, reason="set MOMENTUM_TEST_MAILPIT_API to run against Mailpit")
def test_email_reaches_mailpit() -> None:
    smtp = SmtpSettings(
        host="127.0.0.1",
        port=MAILPIT_SMTP_PORT,
        sender="Momentum <alerts@momentum.test>",
        starttls=False,
        ssl=False,
        username=None,
        password=None,
    )
    marker = f"evt_mailpit{int(time.time() * 1000)}"
    event = replace(EVENT, id=marker)
    EmailSender(smtp).send(email_job(event, email="viewer@momentum.test"))
    with urllib.request.urlopen(
        f"{MAILPIT_API}/api/v1/search?query=message-id:{marker}@momentum.test", timeout=5
    ) as r:
        found = json.load(r)
    assert found["messages_count"] == 1
    message = found["messages"][0]
    assert message["Subject"] == "Alert: AAPL rose above $200.00 (now $201.30)."
    assert message["To"][0]["Address"] == "viewer@momentum.test"
