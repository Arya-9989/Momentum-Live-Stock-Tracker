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
DEFAULT_ALERT_INTERVAL_S: Final = 60
ALERT_WORKER_MODES: Final = ("auto", "external", "off")
PROVIDERS: Final = ("yahoo", "alphavantage")


class SettingsError(ValueError):
    """An environment variable holds a value the app cannot use."""


@dataclass(frozen=True, slots=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    starttls: bool
    ssl: bool
    username: str | None
    password: str | None
    timeout_s: float = 10.0


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_url: str
    secret_key: str | None
    previous_secret_key: str | None
    cookie_secure: bool | None  # None: decide per request from the URL scheme
    device_retention_days: int
    alert_worker: str = "auto"
    alert_interval_s: int = DEFAULT_ALERT_INTERVAL_S
    smtp: SmtpSettings | None = None
    webhook_allow_private: bool = False
    providers: tuple[str, ...] = ("yahoo",)
    alphavantage_key: str | None = None
    alphavantage_daily_budget: int = 25

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
    worker = env.get("MOMENTUM_ALERT_WORKER", "").strip().lower() or "auto"
    if worker not in ALERT_WORKER_MODES:
        msg = f"MOMENTUM_ALERT_WORKER must be one of {', '.join(ALERT_WORKER_MODES)}"
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
        alert_worker=worker,
        alert_interval_s=_int(
            env, "MOMENTUM_ALERT_INTERVAL_S", DEFAULT_ALERT_INTERVAL_S, minimum=10
        ),
        smtp=_smtp(env),
        webhook_allow_private=is_truthy(env.get("MOMENTUM_WEBHOOK_ALLOW_PRIVATE", "")),
        providers=_providers(env),
        alphavantage_key=_optional(env, "ALPHAVANTAGE_API_KEY"),
        alphavantage_daily_budget=_int(env, "ALPHAVANTAGE_DAILY_BUDGET", 25),
    )


def _providers(env: Mapping[str, str]) -> tuple[str, ...]:
    """``MOMENTUM_PROVIDERS=yahoo,alphavantage``: the order is the fallback order."""
    raw = env.get("MOMENTUM_PROVIDERS", "").strip().lower() or "yahoo"
    names = tuple(dict.fromkeys(n.strip() for n in raw.split(",") if n.strip()))
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown or not names:
        msg = f"MOMENTUM_PROVIDERS accepts {', '.join(PROVIDERS)}; got {', '.join(unknown) or raw}"
        raise SettingsError(msg)
    return names


def _smtp(env: Mapping[str, str]) -> SmtpSettings | None:
    """Email delivery is on when ``MOMENTUM_SMTP_HOST`` is set."""
    host = _optional(env, "MOMENTUM_SMTP_HOST")
    if host is None:
        return None
    sender = _optional(env, "MOMENTUM_SMTP_FROM")
    if sender is None or "@" not in sender or any(c in sender for c in "\r\n"):
        msg = "MOMENTUM_SMTP_FROM must be an address such as Momentum <alerts@example.com>"
        raise SettingsError(msg)
    ssl = is_truthy(env.get("MOMENTUM_SMTP_SSL", ""))
    starttls_raw = env.get("MOMENTUM_SMTP_STARTTLS", "").strip()
    return SmtpSettings(
        host=host,
        port=_int(env, "MOMENTUM_SMTP_PORT", 465 if ssl else 587),
        sender=sender,
        starttls=False if ssl else (is_truthy(starttls_raw) if starttls_raw else True),
        ssl=ssl,
        username=_optional(env, "MOMENTUM_SMTP_USERNAME"),
        password=_optional(env, "MOMENTUM_SMTP_PASSWORD"),
    )
