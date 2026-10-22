"""Choosing the market data provider, and the fallback chain across several.

See docs/adr/0004-second-data-provider.md. ``FallbackProvider`` tries each
provider in order:

* a transient failure (network, rate limit) or a symbol the provider does not
  cover moves on to the next provider;
* "no such symbol" from a provider that covers it stops the chain (a typo should
  not spend the next provider's quota);
* a provider that rate-limits is skipped for a cool-down.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from typing import Any, Final

import pandas as pd

from momentum import fundamentals as fu
from momentum import settings as settings_mod
from momentum.data import (
    DataError,
    DataSourceError,
    MarketDataProvider,
    NoDataError,
    RateLimitError,
    YahooProvider,
)

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_S: Final = 60.0


class FallbackProvider:
    """A ``MarketDataProvider`` that asks several providers in turn."""

    def __init__(
        self,
        providers: Sequence[tuple[str, MarketDataProvider]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            msg = "At least one provider is required."
            raise ValueError(msg)
        self.providers = list(providers)
        self._clock = clock
        self._skip_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self.last_source: str | None = None

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.providers]

    def _cooling(self, name: str) -> bool:
        with self._lock:
            return self._skip_until.get(name, 0.0) > self._clock()

    def _cool(self, name: str, exc: RateLimitError) -> None:
        delay = float(getattr(exc, "retry_after_s", DEFAULT_COOLDOWN_S))
        with self._lock:
            self._skip_until[name] = self._clock() + delay
        logger.info("Provider %s is rate-limited; skipping it for %.0f s", name, delay)

    def _call(self, method: str, *args: Any) -> Any:
        from momentum.alphavantage import UnsupportedSymbolError

        errors: list[DataError] = []
        for name, provider in self.providers:
            if method == "fundamentals" and not isinstance(provider, fu.FundamentalsProvider):
                continue
            if self._cooling(name):
                errors.append(RateLimitError(f"{name} is cooling down after a rate limit."))
                continue
            try:
                result = getattr(provider, method)(*args)
            except UnsupportedSymbolError as exc:
                errors.append(exc)
                continue
            except RateLimitError as exc:
                self._cool(name, exc)
                errors.append(exc)
                continue
            except DataSourceError as exc:
                errors.append(exc)
                continue
            except NoDataError:
                raise  # the provider covers the symbol and says it does not exist
            self.last_source = name
            return result
        raise _most_informative(errors, method)

    def history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        frame: pd.DataFrame = self._call("history", symbol, period, interval)
        return frame

    def info(self, symbol: str) -> Mapping[str, Any]:
        info: Mapping[str, Any] = self._call("info", symbol)
        return info

    def fundamentals(self, symbol: str, frequency: fu.Frequency) -> fu.Fundamentals:
        result: fu.Fundamentals = self._call("fundamentals", symbol, frequency)
        return result


def _most_informative(errors: Sequence[DataError], method: str) -> DataError:
    """A rate limit or an outage says more than "not covered"."""
    for kind in (RateLimitError, DataSourceError):
        for exc in reversed(errors):
            if isinstance(exc, kind):
                return exc
    if errors:
        return errors[-1]
    msg = (
        "No configured data source reports fundamentals."
        if method == "fundamentals"
        else ("No data source is configured.")
    )
    return NoDataError(msg)


def build_chain(
    names: Sequence[str], alphavantage_key: str | None, daily_budget: int
) -> MarketDataProvider:
    """Yahoo alone (the default), or a fallback chain in the order given."""
    chain: list[tuple[str, MarketDataProvider]] = []
    for name in names:
        if name == "yahoo":
            chain.append((name, YahooProvider()))
        elif name == "alphavantage":
            if not alphavantage_key:
                logger.warning(
                    "alphavantage is listed but ALPHAVANTAGE_API_KEY is not set; skipped"
                )
                continue
            from momentum.alphavantage import AlphaVantageProvider, RateLimiter

            limiter = RateLimiter(daily_budget=daily_budget)
            chain.append((name, AlphaVantageProvider(alphavantage_key, limiter=limiter)))
    if not chain:
        logger.warning("No usable provider configured; falling back to Yahoo Finance")
        chain.append(("yahoo", YahooProvider()))
    if len(chain) == 1:
        return chain[0][1]
    return FallbackProvider(chain)


def build(config: settings_mod.Settings) -> MarketDataProvider:
    return build_chain(config.providers, config.alphavantage_key, config.alphavantage_daily_budget)


@cache
def _cached(names: tuple[str, ...], key: str | None, budget: int) -> MarketDataProvider:
    return build_chain(names, key, budget)


def live_provider() -> MarketDataProvider:
    """One provider per configuration for the whole process, so rate limiters and
    cool-downs are shared by every session and the alert loop."""
    config = settings_mod.load()
    return _cached(config.providers, config.alphavantage_key, config.alphavantage_daily_budget)
