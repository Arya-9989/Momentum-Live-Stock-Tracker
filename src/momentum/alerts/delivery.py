"""Deliver alert events by email and webhook, and drain the outbox.

Webhooks are requests the server makes to an address someone else chose, so
they are treated as hostile input (see docs/adr/0002-alerts.md):

* ``https`` only, no credentials in the URL;
* every address the host resolves to must be public, and the connection goes
  to the address that was checked (no DNS-rebinding window), with SNI and
  certificate checks for the original host name;
* no redirects, 5 s timeouts, at most 64 KB of the response is read.

Each request is signed: ``Momentum-Signature: t=<unix>,v1=<hex>`` where
``hex = HMAC-SHA256(secret, f"{t}.{body}")``.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import smtplib
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from momentum.settings import SmtpSettings
from momentum.store.alerts import DeliveryJob, Event, SqlAlertRepository

logger = logging.getLogger(__name__)

TIMEOUT_S: Final = 5.0
MAX_RESPONSE_BYTES: Final = 64 * 1024
MAX_URL_LENGTH: Final = 2048
USER_AGENT: Final = (
    "Momentum-Alerts/1.0 (+https://github.com/Arya-9989/Momentum-Live-Stock-Tracker)"
)
# Cloud metadata endpoints are link-local or private already; listed for clarity.
_METADATA_HOSTS: Final = frozenset({"metadata.google.internal", "metadata"})


class DeliveryError(Exception):
    """A delivery failed; ``permanent`` failures are not retried."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class UnsafeUrlError(DeliveryError):
    """The webhook URL is not allowed (scheme, credentials or a non-public address)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


# --------------------------------------------------------------------------- webhooks

Resolver = Callable[[str, int], list[str]]


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        msg = f"Cannot resolve {host}."
        raise DeliveryError(msg) from exc
    return sorted({str(info[4][0]) for info in infos})


@dataclass(frozen=True, slots=True)
class WebhookTarget:
    scheme: str
    host: str
    port: int
    path: str  # path plus query
    addresses: tuple[str, ...]


def check_url(
    url: str, *, allow_private: bool = False, resolver: Resolver = _resolve
) -> WebhookTarget:
    """Validate a webhook URL and the addresses it resolves to; raises ``UnsafeUrlError``."""
    if len(url) > MAX_URL_LENGTH or any(ch.isspace() for ch in url):
        msg = "That URL is too long or contains spaces."
        raise UnsafeUrlError(msg)
    parts = urlsplit(url)
    allowed = {"https", "http"} if allow_private else {"https"}
    if parts.scheme not in allowed:
        msg = "Webhook URLs must start with https://."
        raise UnsafeUrlError(msg)
    if parts.username or parts.password:
        msg = "Webhook URLs cannot contain a user name or password."
        raise UnsafeUrlError(msg)
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        msg = "That URL has no host."
        raise UnsafeUrlError(msg)
    if host in _METADATA_HOSTS and not allow_private:
        msg = "That host is not allowed."
        raise UnsafeUrlError(msg)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        msg = "That URL has an invalid port."
        raise UnsafeUrlError(msg) from exc
    addresses = resolver(host, port)
    if not addresses:
        msg = f"Cannot resolve {host}."
        raise DeliveryError(msg)
    if not allow_private:
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%")[0])
            mapped = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
            if not (mapped or ip).is_global or (mapped or ip).is_multicast:
                msg = "Webhook URLs must point to a public internet address."
                raise UnsafeUrlError(msg)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return WebhookTarget(parts.scheme, host, port, path, tuple(addresses))


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(
    secret: str, header: str, body: bytes, *, now: int | None = None, tolerance_s: int = 300
) -> bool:
    """Receiver-side check (documented in the README; used by the tests)."""
    fields = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    try:
        timestamp = int(fields.get("t", ""))
    except ValueError:
        return False
    if abs((now if now is not None else int(time.time())) - timestamp) > tolerance_s:
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, fields.get("v1", ""))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a pre-checked IP address, with SNI and certificate checks for ``host``."""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


def webhook_payload(event: Event) -> bytes:
    """The JSON body: stable field names, no internal identifiers beyond the event's."""
    created = datetime.fromtimestamp(event.created_at / 1000, tz=UTC).isoformat()
    observed = (
        datetime.fromtimestamp(event.observed_at / 1000, tz=UTC).isoformat()
        if event.observed_at is not None
        else None
    )
    body = {
        "id": event.id,
        "type": "alert.test" if event.kind == "test" else "alert.triggered",
        "created_at": created,
        "message": event.message,
        "alert": None
        if event.kind == "test"
        else {
            "id": event.alert_id,
            "symbol": event.symbol,
            "condition": event.condition,
            "threshold": event.threshold,
        },
        "observation": None
        if event.kind == "test"
        else {"value": event.value, "observed_at": observed},
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


Connector = Callable[[WebhookTarget, str], http.client.HTTPConnection]


def _connect(target: WebhookTarget, address: str) -> http.client.HTTPConnection:
    if target.scheme == "https":
        return _PinnedHTTPSConnection(target.host, address, target.port, TIMEOUT_S)
    return _PinnedHTTPConnection(target.host, address, target.port, TIMEOUT_S)


class WebhookSender:
    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolver: Resolver = _resolve,
        connector: Connector = _connect,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._allow_private = allow_private
        self._resolver = resolver
        self._connector = connector
        self._clock = clock

    def send(self, job: DeliveryJob) -> None:
        url, secret = job.target.webhook_url, job.target.webhook_secret
        if not url or not secret:
            msg = "The webhook was removed."
            raise DeliveryError(msg, permanent=True)
        # Checked again at send time: the DNS answer may have changed since it was saved.
        target = check_url(url, allow_private=self._allow_private, resolver=self._resolver)
        body = webhook_payload(job.event)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Momentum-Event-Id": job.event.id,
            "Momentum-Signature": sign(secret, int(self._clock()), body),
        }
        connection = self._connector(target, target.addresses[0])
        try:
            connection.request("POST", target.path, body=body, headers=headers)
            response = connection.getresponse()
            response.read(MAX_RESPONSE_BYTES)
            status = response.status
        except (OSError, http.client.HTTPException) as exc:
            msg = f"Could not reach the webhook: {type(exc).__name__}."
            raise DeliveryError(msg) from exc
        finally:
            connection.close()
        if 200 <= status < 300:
            return
        retry = status in {408, 425, 429} or status >= 500
        msg = f"The webhook answered HTTP {status}."
        raise DeliveryError(msg, permanent=not retry)


# --------------------------------------------------------------------------- email


class EmailSender:
    def __init__(self, smtp: SmtpSettings, *, factory: Callable[..., Any] | None = None) -> None:
        self._smtp = smtp
        self._factory = factory

    def message(self, job: DeliveryJob) -> EmailMessage:
        event = job.event
        if not job.target.email:
            msg = "There is no verified email address for this account any more."
            raise DeliveryError(msg, permanent=True)
        email = EmailMessage()
        subject = "Momentum test email" if event.kind == "test" else f"Alert: {event.message}"
        # One line, always: a header can never be smuggled in through the text.
        email["Subject"] = " ".join(subject.split())[:200]
        email["From"] = self._smtp.sender
        email["To"] = job.target.email
        email["Date"] = formatdate(event.created_at / 1000, usegmt=True)
        domain = self._smtp.sender.rsplit("@", 1)[-1].strip(" >")
        # The same event always has the same Message-ID, so a retried send is a duplicate
        # that mail clients and servers recognise.
        email["Message-ID"] = f"<{event.id}@{domain}>"
        email.set_content(
            f"{event.message}\n\n"
            "You receive this because you created this alert in Momentum. Delete the alert "
            "in the Alerts tab to stop these emails.\n\n"
            "Market data may be delayed or incomplete. Not financial advice.\n"
        )
        return email

    def send(self, job: DeliveryJob) -> None:
        email = self.message(job)
        smtp = self._smtp
        factory = self._factory or (smtplib.SMTP_SSL if smtp.ssl else smtplib.SMTP)
        try:
            with factory(smtp.host, smtp.port, timeout=smtp.timeout_s) as client:
                if smtp.starttls:
                    client.starttls(context=ssl.create_default_context())
                if smtp.username and smtp.password:
                    client.login(smtp.username, smtp.password)
                client.send_message(email)
        except smtplib.SMTPRecipientsRefused as exc:
            msg = "The mail server refused the address."
            raise DeliveryError(msg, permanent=True) from exc
        except (OSError, smtplib.SMTPException) as exc:
            msg = f"Could not send email: {type(exc).__name__}."
            raise DeliveryError(msg) from exc


# --------------------------------------------------------------------------- outbox


class Sender(Protocol):
    def send(self, job: DeliveryJob) -> None: ...


@dataclass(slots=True)
class DispatchStats:
    sent: int = 0
    retried: int = 0
    failed: int = 0


def dispatch(repo: SqlAlertRepository, senders: Mapping[str, Sender]) -> DispatchStats:
    """Send every due delivery once; failures are retried with backoff by the repository."""
    stats = DispatchStats()
    for job in repo.claim_due():
        sender = senders.get(job.channel)
        try:
            if sender is None:
                msg = f"{job.channel.capitalize()} delivery is not configured on this server."
                raise DeliveryError(msg, permanent=True)
            sender.send(job)
        except DeliveryError as exc:
            logger.warning("Delivery %s (%s) failed: %s", job.id, job.channel, exc)
            if repo.mark_failed(job, str(exc), permanent=exc.permanent):
                stats.retried += 1
            else:
                stats.failed += 1
            continue
        repo.mark_sent(job.id)
        stats.sent += 1
    return stats
