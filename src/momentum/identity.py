"""Who is viewing: a signed-in account, an anonymous device, or just this session.

See docs/adr/0001-watchlist-identity-and-storage.md. In order of preference:

1. **Account**: OIDC through ``st.login`` (when ``[auth]`` is configured). The
   owner id is a hash of the ID token's issuer and subject, never the email.
2. **Device**: the ``momentum_device`` cookie, set by ``DeviceCookieMiddleware``
   in ``serve.py``. It is HttpOnly (page scripts cannot read it), SameSite=Lax,
   and carries 128 random bits plus an HMAC-SHA256 tag, so the server rejects
   identifiers it did not issue before they reach the database.
3. **Session**: neither is available; nothing is persisted.

Everything except ``DeviceCookieMiddleware.__call__`` is pure and unit-tested.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal

from momentum import settings as settings_mod
from momentum.store.owners import Owner

logger = logging.getLogger(__name__)

COOKIE_NAME: Final = "momentum_device"
COOKIE_MAX_AGE_S: Final = 400 * 24 * 60 * 60  # the longest lifetime browsers honour
TOKEN_VERSION: Final = "v1"
_ID_BYTES: Final = 16
_TAG_BYTES: Final = 16
_MAX_TOKEN_LENGTH: Final = 80
_KEY_FILE: Final = "secret.key"
_DEVICE_CONTEXT: Final = b"momentum/device-cookie/v1"

IdentityKind = Literal["account", "device", "session"]


# --------------------------------------------------------------------------- keys


def derive_key(secret: str | bytes, context: bytes) -> bytes:
    """A purpose-specific key from the deployment secret (one secret, many uses)."""
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(raw, context, hashlib.sha256).digest()


def _key_file_secret(data_dir: Path) -> bytes:
    """The generated secret in ``data_dir/secret.key``, created (0600) on first use.

    ``O_EXCL`` makes creation race-free between processes: the loser reads the
    winner's key.
    """
    path = data_dir / _KEY_FILE
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value)
    logger.info("Generated a signing key in %s", path)
    return bytes.fromhex(value)


def deployment_secrets(settings: settings_mod.Settings) -> tuple[bytes, ...]:
    """Current secret first, then the previous one (accepted during a key rotation).

    Cached per configuration, so the key file is read once per process.
    """
    return _deployment_secrets(settings.secret_key, settings.previous_secret_key, settings.data_dir)


@cache
def _deployment_secrets(
    secret_key: str | None, previous: str | None, data_dir: Path
) -> tuple[bytes, ...]:
    current = secret_key.encode("utf-8") if secret_key else _key_file_secret(data_dir)
    return (current, previous.encode("utf-8")) if previous else (current,)


def device_keys(settings: settings_mod.Settings) -> tuple[bytes, ...]:
    """Cookie-signing keys, current first."""
    return tuple(derive_key(s, _DEVICE_CONTEXT) for s in deployment_secrets(settings))


# --------------------------------------------------------------------------- tokens


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _tag(key: bytes, device_id: str) -> str:
    message = f"{TOKEN_VERSION}.{device_id}".encode("ascii")
    return _b64(hmac.new(key, message, hashlib.sha256).digest()[:_TAG_BYTES])


def sign_device_id(device_id: str, key: bytes) -> str:
    return f"{TOKEN_VERSION}.{device_id}.{_tag(key, device_id)}"


def issue_device_token(key: bytes) -> str:
    """A new token: ``v1.<22-char random id>.<22-char tag>``."""
    return sign_device_id(_b64(secrets.token_bytes(_ID_BYTES)), key)


@dataclass(frozen=True, slots=True)
class DeviceToken:
    device_id: str
    key_index: int  # 0: signed with the current key; >0: re-sign it

    @property
    def needs_resign(self) -> bool:
        return self.key_index > 0


def _device_id_and_tag(token: str) -> tuple[str, str] | None:
    """The two parts of a well-formed ``v1`` token, or None."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_VERSION:
        return None
    _, device_id, tag = parts
    try:
        decoded = _unb64(device_id)
        well_formed = len(decoded) == _ID_BYTES and len(_unb64(tag)) == _TAG_BYTES
    except (ValueError, UnicodeError):
        return None
    # Canonical encoding only, so one device has exactly one spelling.
    return (device_id, tag) if well_formed and _b64(decoded) == device_id else None


def verify_device_token(token: str | None, keys: Sequence[bytes]) -> DeviceToken | None:
    """The device id inside a token we issued, or None for anything else."""
    if not token or len(token) > _MAX_TOKEN_LENGTH or not keys:
        return None
    parsed = _device_id_and_tag(token)
    if parsed is None:
        return None
    device_id, tag = parsed
    for index, key in enumerate(keys):
        if hmac.compare_digest(_tag(key, device_id), tag):
            return DeviceToken(device_id, index)
    return None


# --------------------------------------------------------------------------- identity


@dataclass(frozen=True, slots=True)
class Identity:
    kind: IdentityKind
    owner: Owner | None = None
    email: str | None = None  # only when the identity provider verified it
    name: str | None = None

    @property
    def persistent(self) -> bool:
        return self.owner is not None


SESSION: Final = Identity("session")


def account_owner_id(issuer: str, subject: str) -> str:
    digest = hashlib.sha256(f"{issuer}\x1f{subject}".encode()).hexdigest()
    return f"acct_{digest[:40]}"


def _claim(user: Mapping[str, Any], name: str) -> str | None:
    value = user.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def resolve(
    user: Mapping[str, Any] | None,
    cookies: Mapping[str, str],
    keys: Sequence[bytes],
) -> Identity:
    """The viewer's identity from ``st.user`` claims (None when not signed in) and cookies."""
    if user and user.get("is_logged_in"):
        subject, issuer = _claim(user, "sub"), _claim(user, "iss") or ""
        if subject:
            email = _claim(user, "email") if user.get("email_verified") is True else None
            return Identity(
                "account",
                Owner(account_owner_id(issuer, subject), "account"),
                email=email,
                name=_claim(user, "name") or _claim(user, "preferred_username"),
            )
        logger.warning("Signed-in user without a 'sub' claim; treating as anonymous")
    token = verify_device_token(cookies.get(COOKIE_NAME), keys)
    if token is not None:
        return Identity("device", Owner(f"dev_{token.device_id}", "device"))
    return SESSION


# --------------------------------------------------------------------------- middleware

Scope = dict[str, Any]

_SKIP_PREFIXES: Final = ("/_stcore/", "/static/", "/media/", "/component/", "/app/")


def parse_cookie_header(header: str) -> dict[str, str]:
    """``a=1; b=2`` -> dict (first occurrence wins, as browsers send the most specific first)."""
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in cookies:
            cookies[name] = value.strip().strip('"')
    return cookies


def set_cookie_header(token: str, *, secure: bool) -> bytes:
    attributes = [
        f"{COOKIE_NAME}={token}",
        "Path=/",
        f"Max-Age={COOKIE_MAX_AGE_S}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes).encode("ascii")


class DeviceCookieMiddleware:
    """Give every browser a signed device cookie on its first page load.

    Only the HTML document gets ``Set-Cookie`` (the websocket handshake that
    follows carries the cookie to the script run). Static assets, the API and
    downloads stream through untouched.
    """

    def __init__(self, app: Any, settings: settings_mod.Settings | None = None) -> None:
        self.app = app
        self._settings = settings
        self._keys: tuple[bytes, ...] | None = None

    def _signing_keys(self) -> tuple[bytes, ...]:
        if self._keys is None:
            self._keys = device_keys(self._settings or settings_mod.load())
        return self._keys

    def _secure(self, scope: Scope) -> bool:
        configured = (self._settings or settings_mod.load()).cookie_secure
        return configured if configured is not None else scope.get("scheme") == "https"

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        if (
            scope["type"] != "http"
            or scope.get("method") not in {"GET", "HEAD"}
            or path.startswith(_SKIP_PREFIXES)
        ):
            await self.app(scope, receive, send)
            return
        header = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"cookie"),
            "",
        )
        keys = self._signing_keys()
        current = verify_device_token(parse_cookie_header(header).get(COOKIE_NAME), keys)
        if current is not None and not current.needs_resign:
            await self.app(scope, receive, send)
            return
        token = (
            sign_device_id(current.device_id, keys[0])
            if current is not None
            else issue_device_token(keys[0])
        )
        cookie = set_cookie_header(token, secure=self._secure(scope))

        async def with_cookie(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and int(message.get("status", 0)) == 200:
                headers = list(message.get("headers", []))
                content_type = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if content_type.startswith(b"text/html"):
                    # A response that sets an identity must never be stored by a
                    # shared cache, or the next visitor would receive this cookie.
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers += [(b"set-cookie", cookie), (b"cache-control", b"no-cache, private")]
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_cookie)
