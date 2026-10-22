"""Runtime settings from environment variables, with safe defaults.

Static product configuration (ranges, intervals, cache lifetimes) lives in
``momentum.config``; this module holds what an operator sets per deployment:
where data is stored, the signing key and the optional delivery channels.
Nothing here is required. Everything is read once per call to ``load()`` so
tests can change the environment freely.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from momentum.config import is_truthy

DEFAULT_DATA_DIR: Final = ".momentum"
DEFAULT_DEVICE_RETENTION_DAYS: Final = 400


class SettingsError(ValueError):
    """An environment variable holds a value the app cannot use."""


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_url: str
    secret_key: str | None
    previous_secret_key: str | None
    cookie_secure: bool | None  # None: decide per request from the URL scheme
    device_retention_days: int

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://"))


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        msg = f"{name} must be an integer, got {raw!r}"
        raise SettingsError(msg) from exc
    if value < minimum:
        msg = f"{name} must be at least {minimum}, got {value}"
        raise SettingsError(msg)
    return value


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _tristate(env: Mapping[str, str], name: str) -> bool | None:
    raw = env.get(name, "").strip().lower()
    if raw in {"", "auto"}:
        return None
    return is_truthy(raw)


def load(env: Mapping[str, str] | None = None) -> Settings:
    """Settings from ``env`` (the process environment by default)."""
    env = dict(os.environ) if env is None else env
    data_dir = Path(env.get("MOMENTUM_DATA_DIR", "").strip() or DEFAULT_DATA_DIR).resolve()
    database_url = env.get("MOMENTUM_DATABASE_URL", "").strip()
    if not database_url:
        database_url = f"sqlite:///{(data_dir / 'momentum.db').as_posix()}"
    if not database_url.startswith(("sqlite:///", "postgresql://", "postgres://")):
        msg = "MOMENTUM_DATABASE_URL must start with sqlite:/// or postgresql://"
        raise SettingsError(msg)
    secret = _optional(env, "MOMENTUM_SECRET_KEY")
    if secret is not None and len(secret) < 32:
        msg = "MOMENTUM_SECRET_KEY must be at least 32 characters"
        raise SettingsError(msg)
    return Settings(
        data_dir=data_dir,
        database_url=database_url,
        secret_key=secret,
        previous_secret_key=_optional(env, "MOMENTUM_SECRET_KEY_PREVIOUS"),
        cookie_secure=_tristate(env, "MOMENTUM_COOKIE_SECURE"),
        device_retention_days=_int(
            env, "MOMENTUM_DEVICE_RETENTION_DAYS", DEFAULT_DEVICE_RETENTION_DAYS
        ),
    )
