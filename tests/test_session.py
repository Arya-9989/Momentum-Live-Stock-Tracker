"""Identity inside a script run (``ui.session``), with Streamlit's objects faked."""

from __future__ import annotations

import builtins
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from momentum import identity, settings, store
from momentum.identity import COOKIE_NAME
from momentum.store import StoreUnavailableError
from momentum.ui import session


class FakeUser(dict[str, Any]):
    @property
    def is_logged_in(self) -> bool:
        return bool(self.get("is_logged_in"))


class Secrets(dict[str, Any]):
    pass


class BrokenSecrets:
    def __contains__(self, key: object) -> bool:
        raise FileNotFoundError("no secrets.toml")


def fake_st(
    monkeypatch: pytest.MonkeyPatch,
    *,
    secrets: object = None,
    user: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    fake = SimpleNamespace(
        secrets=Secrets() if secrets is None else secrets,
        user=FakeUser(user or {}),
        context=SimpleNamespace(cookies=cookies or {}),
        session_state=state,
    )
    monkeypatch.setattr(session, "st", fake)
    return state


AUTH = Secrets(auth={"client_id": "x"})
CLAIMS = {"is_logged_in": True, "iss": "https://id", "sub": "42", "tokens": {"id": "secret"}}


def test_auth_is_off_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_st(monkeypatch, secrets=BrokenSecrets())
    assert not session.auth_configured()
    fake_st(monkeypatch)
    assert not session.auth_configured()


def test_auth_needs_authlib(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_st(monkeypatch, secrets=AUTH)
    assert session.auth_configured()
    real_import = builtins.__import__

    def no_authlib(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "authlib":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_authlib)
    with caplog.at_level(logging.WARNING):
        assert not session.auth_configured()
    assert "Authlib is not installed" in caplog.text


def test_signed_in_user_becomes_an_account_without_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    state = fake_st(monkeypatch, secrets=AUTH, user=CLAIMS)
    who = session.current_identity()
    assert who.kind == "account"
    assert state["_mm_identity"] is who
    assert session.identity_of_session() is who
    claims = session._user_claims()
    assert claims is not None
    assert "tokens" not in claims


def test_signed_out_user_uses_the_device_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    key = identity.device_keys(settings.load())[0]
    token = identity.issue_device_token(key)
    fake_st(monkeypatch, secrets=AUTH, user={"is_logged_in": False}, cookies={COOKIE_NAME: token})
    assert session.current_identity().kind == "device"


def test_a_broken_user_object_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_st(monkeypatch, secrets=AUTH)

    class Exploding:
        @property
        def is_logged_in(self) -> bool:
            raise RuntimeError("auth misconfigured")

    session.st.user = Exploding()  # type: ignore[attr-defined]
    assert session._user_claims() is None


def test_unusable_keys_disable_device_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_st(monkeypatch, cookies={COOKIE_NAME: "anything"})

    def broken(_settings: settings.Settings) -> tuple[bytes, ...]:
        raise OSError("read-only file system")

    monkeypatch.setattr(identity, "device_keys", broken)
    assert session.current_identity().kind == "session"


def test_identity_of_session_resolves_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_st(monkeypatch)
    assert session.identity_of_session().kind == "session"


def test_store_outage_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = fake_st(monkeypatch)

    def broken() -> None:
        raise StoreUnavailableError("down")

    monkeypatch.setattr(store, "get_store", broken)
    with caplog.at_level(logging.ERROR):
        assert session.open_store() is None
        assert session.open_store() is None
    assert caplog.text.count("Saved data is unavailable") == 1
    assert state["_mm_store_warned"]
