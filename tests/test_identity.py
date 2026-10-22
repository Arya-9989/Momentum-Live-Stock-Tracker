"""Identity: signed device tokens, resolution order, the key file and the cookie middleware."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from momentum import identity, server, settings
from momentum.identity import (
    COOKIE_NAME,
    DeviceCookieMiddleware,
    issue_device_token,
    resolve,
    sign_device_id,
    verify_device_token,
)

KEY = b"k" * 32
OLD = b"o" * 32


class TestTokens:
    def test_round_trip(self) -> None:
        token = issue_device_token(KEY)
        parsed = verify_device_token(token, [KEY])
        assert parsed is not None
        assert parsed.key_index == 0
        assert not parsed.needs_resign
        assert len(parsed.device_id) == 22
        assert token == sign_device_id(parsed.device_id, KEY)

    def test_tokens_are_unique(self) -> None:
        assert len({issue_device_token(KEY) for _ in range(500)}) == 500

    def test_previous_key_is_accepted_but_needs_resigning(self) -> None:
        parsed = verify_device_token(issue_device_token(OLD), [KEY, OLD])
        assert parsed is not None
        assert parsed.needs_resign

    @pytest.mark.parametrize(
        "mangle",
        [
            lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),  # tag changed
            lambda t: t.replace(t.split(".")[1], "A" * 22),  # a chosen id, old tag
            lambda t: "v2" + t[2:],  # unknown version
            lambda t: t + ".extra",
            lambda t: t.split(".")[0] + "." + t.split(".")[1],  # no tag
            lambda t: t.replace(".", ".=", 1),
            lambda t: t + "A" * 100,  # oversized
            lambda t: "",
        ],
    )
    def test_tampered_tokens_are_rejected(self, mangle: Any) -> None:
        assert verify_device_token(mangle(issue_device_token(KEY)), [KEY]) is None

    def test_a_token_from_another_deployment_is_rejected(self) -> None:
        assert verify_device_token(issue_device_token(OLD), [KEY]) is None

    def test_non_canonical_base64_is_rejected(self) -> None:
        # The same 16 bytes can be spelled two ways in unpadded base64 if the
        # trailing bits are set; only the canonical spelling is accepted.
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        device_id = issue_device_token(KEY).split(".")[1]
        # The last of 22 characters carries 2 bits; the other 4 must be zero.
        twin = alphabet[alphabet.index(device_id[-1]) + 1]
        forged = sign_device_id(device_id[:-1] + twin, KEY)
        assert identity._unb64(device_id[:-1] + twin) == identity._unb64(device_id)
        assert verify_device_token(forged, [KEY]) is None

    def test_no_keys_means_no_device_identity(self) -> None:
        assert verify_device_token(issue_device_token(KEY), []) is None


class TestResolve:
    def test_account_is_keyed_by_issuer_and_subject(self) -> None:
        user = {
            "is_logged_in": True,
            "iss": "https://id.example",
            "sub": "123",
            "email": "a@example.com",
            "email_verified": True,
            "name": "Ada",
        }
        who = resolve(user, {COOKIE_NAME: issue_device_token(KEY)}, [KEY])
        assert who.kind == "account"
        assert who.persistent
        assert who.owner is not None
        assert who.owner.id.startswith("acct_")
        assert who.owner == resolve(user | {"email": "new@example.com"}, {}, [KEY]).owner
        assert who.owner != resolve(user | {"iss": "https://other.example"}, {}, [KEY]).owner
        assert (who.email, who.name) == ("a@example.com", "Ada")

    def test_unverified_email_is_not_used(self) -> None:
        user = {"is_logged_in": True, "sub": "1", "email": "a@example.com"}
        assert resolve(user, {}, [KEY]).email is None
        assert resolve(user | {"email_verified": "true"}, {}, [KEY]).email is None

    def test_account_without_subject_falls_back_to_the_device(self) -> None:
        token = issue_device_token(KEY)
        who = resolve({"is_logged_in": True, "email": "x@example.com"}, {COOKIE_NAME: token}, [KEY])
        assert who.kind == "device"

    def test_device_cookie(self) -> None:
        token = issue_device_token(KEY)
        who = resolve(None, {COOKIE_NAME: token}, [KEY])
        assert who.kind == "device"
        assert who.owner is not None
        assert who.owner.id == f"dev_{token.split('.')[1]}"

    def test_nothing_usable_is_a_session(self) -> None:
        assert resolve(None, {}, [KEY]) is identity.SESSION
        assert resolve({"is_logged_in": False}, {COOKIE_NAME: "forged"}, [KEY]).kind == "session"
        assert not identity.SESSION.persistent


class TestKeys:
    def test_key_file_is_created_once_and_private(self, isolated_data_dir: Path) -> None:
        conf = settings.load()
        first = identity.device_keys(conf)
        identity._deployment_secrets.cache_clear()
        assert identity.device_keys(conf) == first  # read back, not regenerated
        path = isolated_data_dir / "secret.key"
        assert len(bytes.fromhex(path.read_text())) == 32
        if sys.platform != "win32":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_lost_creation_race_reads_the_winners_key(
        self, isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        isolated_data_dir.mkdir(parents=True)
        real_open = os.open

        def racing_open(path: Any, flags: int, mode: int = 0o777) -> int:
            Path(path).write_text("ab" * 32, encoding="ascii")  # another process won
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", racing_open)
        assert identity._key_file_secret(isolated_data_dir) == bytes.fromhex("ab" * 32)

    def test_env_secret_wins_and_previous_is_kept(self) -> None:
        conf = settings.load(
            {"MOMENTUM_SECRET_KEY": "s" * 32, "MOMENTUM_SECRET_KEY_PREVIOUS": "p" * 32}
        )
        keys = identity.device_keys(conf)
        assert keys == (
            identity.derive_key("s" * 32, b"momentum/device-cookie/v1"),
            identity.derive_key("p" * 32, b"momentum/device-cookie/v1"),
        )

    def test_short_secret_is_refused(self) -> None:
        with pytest.raises(settings.SettingsError, match="32 characters"):
            settings.load({"MOMENTUM_SECRET_KEY": "short"})


# --------------------------------------------------------------------------- middleware


async def _document(scope: dict[str, Any], receive: Any, send: Any) -> None:
    path = scope["path"]
    content_type = b"application/javascript" if path.startswith("/static/") else b"text/html"
    status = 404 if path == "/missing" else 200
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type),
                (b"cache-control", b"public, max-age=600"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"<html></html>", "more_body": False})


def call(
    path: str = "/",
    *,
    cookie: str | None = None,
    scheme: str = "http",
    method: str = "GET",
    conf: settings.Settings | None = None,
) -> dict[bytes, list[bytes]]:
    app = DeviceCookieMiddleware(
        _document, conf or settings.load({"MOMENTUM_SECRET_KEY": "s" * 32})
    )
    headers = [(b"cookie", cookie.encode())] if cookie else []
    scope = {"type": "http", "method": method, "path": path, "headers": headers, "scheme": scheme}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    out: dict[bytes, list[bytes]] = {}
    for key, value in sent[0].get("headers", []):
        out.setdefault(key, []).append(value)
    return out


def cookie_value(headers: dict[bytes, list[bytes]]) -> str:
    (raw,) = headers[b"set-cookie"]
    return raw.decode().split(";")[0].split("=", 1)[1]


def keys() -> tuple[bytes, ...]:
    return identity.device_keys(settings.load({"MOMENTUM_SECRET_KEY": "s" * 32}))


class TestMiddleware:
    def test_first_visit_gets_a_signed_http_only_cookie(self) -> None:
        headers = call()
        (raw,) = headers[b"set-cookie"]
        assert b"HttpOnly" in raw
        assert b"SameSite=Lax" in raw
        assert b"Path=/" in raw
        assert b"Max-Age=34560000" in raw
        assert b"Secure" not in raw  # plain http
        assert verify_device_token(cookie_value(headers), keys()) is not None
        # A personalised response must not be cached by a shared cache.
        assert headers[b"cache-control"] == [b"no-cache, private"]

    def test_https_cookie_is_secure(self) -> None:
        assert b"Secure" in call(scheme="https")[b"set-cookie"][0]
        forced = settings.load({"MOMENTUM_SECRET_KEY": "s" * 32, "MOMENTUM_COOKIE_SECURE": "1"})
        assert b"Secure" in call(conf=forced)[b"set-cookie"][0]

    def test_valid_cookie_is_left_alone(self) -> None:
        token = cookie_value(call())
        headers = call(cookie=f"other=1; {COOKIE_NAME}={token}")
        assert b"set-cookie" not in headers
        assert headers[b"cache-control"] == [b"public, max-age=600"]

    def test_forged_cookie_is_replaced(self) -> None:
        forged = f"v1.{'A' * 22}.{'B' * 22}"
        headers = call(cookie=f"{COOKIE_NAME}={forged}")
        fresh = cookie_value(headers)
        assert fresh != forged
        assert verify_device_token(fresh, keys()) is not None

    def test_rotation_keeps_the_device_and_resigns(self) -> None:
        old_conf = settings.load({"MOMENTUM_SECRET_KEY": "o" * 32})
        old = cookie_value(call(conf=old_conf))
        rotated = settings.load(
            {"MOMENTUM_SECRET_KEY": "s" * 32, "MOMENTUM_SECRET_KEY_PREVIOUS": "o" * 32}
        )
        new = cookie_value(call(cookie=f"{COOKIE_NAME}={old}", conf=rotated))
        assert new != old
        assert new.split(".")[1] == old.split(".")[1]  # same device, new signature

    @pytest.mark.parametrize("path", ["/static/js/index.js", "/_stcore/health", "/media/x.csv"])
    def test_assets_and_api_never_get_a_cookie(self, path: str) -> None:
        assert b"set-cookie" not in call(path)

    def test_errors_and_non_get_methods_get_no_cookie(self) -> None:
        assert b"set-cookie" not in call("/missing")
        assert b"set-cookie" not in call(method="POST")
        assert b"set-cookie" in call(method="HEAD")

    def test_cookie_header_parsing(self) -> None:
        parsed = identity.parse_cookie_header(f' a=1;{COOKIE_NAME}="x"; {COOKIE_NAME}=y; junk')
        assert parsed == {"a": "1", COOKIE_NAME: "x"}

    def test_serve_installs_the_middleware_outermost(self) -> None:
        outermost: object = server.MIDDLEWARE[0].cls
        assert outermost is DeviceCookieMiddleware


class TestOidcSecrets:
    ENV = {  # noqa: RUF012
        "MOMENTUM_OIDC_SERVER_METADATA_URL": "http://127.0.0.1:5424/realms/m/.well-known/openid-configuration",
        "MOMENTUM_OIDC_CLIENT_ID": "momentum",
        "MOMENTUM_OIDC_CLIENT_SECRET": "secret",
        "MOMENTUM_OIDC_REDIRECT_URI": "http://localhost:5425/oauth2callback",
    }

    def test_off_by_default(self) -> None:
        assert server.oidc_secrets({}) == {}

    def test_partial_configuration_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="MOMENTUM_OIDC_CLIENT_SECRET"):
            server.oidc_secrets({k: v for k, v in self.ENV.items() if "SECRET" not in k})

    def test_cookie_secret_is_derived_or_explicit(self) -> None:
        with pytest.raises(ValueError, match="COOKIE_SECRET"):
            server.oidc_secrets(self.ENV)
        derived = server.oidc_secrets(self.ENV | {"MOMENTUM_SECRET_KEY": "s" * 32})["auth"]
        assert isinstance(derived, dict)
        assert derived["client_id"] == "momentum"
        assert len(str(derived["cookie_secret"])) == 64
        assert derived["cookie_secret"] != "s" * 32  # never the raw deployment secret
        explicit = server.oidc_secrets(self.ENV | {"MOMENTUM_OIDC_COOKIE_SECRET": "c" * 40})["auth"]
        assert isinstance(explicit, dict)
        assert explicit["cookie_secret"] == "c" * 40


class TestSettings:
    def test_defaults(self, isolated_data_dir: Path) -> None:
        conf = settings.load()
        assert conf.data_dir == isolated_data_dir.resolve()
        assert conf.database_url.startswith("sqlite:///")
        assert conf.database_url.endswith("momentum.db")
        assert not conf.is_postgres
        assert conf.cookie_secure is None
        assert conf.device_retention_days == 400

    @pytest.mark.parametrize(
        ("env", "message"),
        [
            ({"MOMENTUM_DEVICE_RETENTION_DAYS": "soon"}, "integer"),
            ({"MOMENTUM_DEVICE_RETENTION_DAYS": "0"}, "at least 1"),
        ],
    )
    def test_invalid_values(self, env: dict[str, str], message: str) -> None:
        with pytest.raises(settings.SettingsError, match=message):
            settings.load(env)

    def test_postgres_and_cookie_flag(self) -> None:
        conf = settings.load(
            {"MOMENTUM_DATABASE_URL": "postgresql://u:p@h/db", "MOMENTUM_COOKIE_SECURE": "false"}
        )
        assert conf.is_postgres
        assert conf.cookie_secure is False
