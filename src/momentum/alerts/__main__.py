"""Command line for the alert loop.

    python -m momentum.alerts run              # the loop, as its own process
    python -m momentum.alerts once             # one cycle (cron-style), then exit
    python -m momentum.alerts replay AAPL 190 201 202
                                               # feed prices to AAPL's alerts, one
                                               # cycle each, then deliver (testing)

``run`` stops cleanly on SIGINT/SIGTERM: the current step finishes first.
Set ``MOMENTUM_ALERT_WORKER=external`` on the app so it does not also start
its in-process loop (running both is safe, just redundant).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Sequence
from types import FrameType

from momentum import data, settings
from momentum.alerts import delivery, engine, worker
from momentum.store import get_store


class ReplayObserver:
    """Serves the given prices for one symbol, one per cycle."""

    def __init__(self, symbol: str, prices: Sequence[float]) -> None:
        self.symbol = symbol
        self.prices = list(prices)
        self.index = 0
        self.start = int(time.time() * 1000)

    def observe(self, symbol: str, metrics: frozenset[str], demo: bool) -> engine.Observation:
        readings: dict[str, float | None] = dict.fromkeys(metrics)
        readings["price"] = self.prices[self.index]
        return engine.Observation(readings, self.start + self.index)


def _replay(symbol: str, prices: Sequence[float]) -> int:
    store = get_store()
    observer = ReplayObserver(data.normalize_symbol(symbol), prices)
    only = [observer.symbol]
    senders = worker.senders_for(settings.load())
    for index, price in enumerate(prices):
        observer.index = index
        stats = engine.evaluate(store.alerts, observer, symbols=only)
        for event in stats.events:
            print(f"{price:>12g}  fired: {event.message}")
        if not stats.events:
            print(f"{price:>12g}  -")
    sent = delivery.dispatch(store.alerts, senders)
    print(f"deliveries: {sent.sent} sent, {sent.retried} to retry, {sent.failed} failed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m momentum.alerts", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the loop until stopped")
    commands.add_parser("once", help="run one cycle and exit")
    replay = commands.add_parser("replay", help="feed prices to one symbol's alerts")
    replay.add_argument("symbol")
    replay.add_argument("prices", nargs="+", type=float)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "replay":
        return _replay(args.symbol, args.prices)
    loop = worker.Worker()
    if args.command == "once":
        cycle, sent = loop.run_once()
        print(
            f"{cycle.alerts} alerts checked, {len(cycle.events)} fired; "
            f"{sent.sent} deliveries sent, {sent.retried} to retry, {sent.failed} failed"
        )
        return 0

    def stop(signum: int, _frame: FrameType | None) -> None:
        logging.info("Signal %d: stopping after the current step", signum)
        loop.request_stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    loop.loop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
