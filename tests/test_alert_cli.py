"""``python -m momentum.alerts``: replay, once and run."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from momentum import store
from momentum.alerts import __main__ as cli
from momentum.alerts import worker
from momentum.store.alerts import NewAlert
from momentum.store.owners import Owner

OWNER = Owner("dev_CCCCCCCCCCCCCCCCCCCCCC", "device")


def test_replay_fires_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    repo = store.get_store().alerts
    repo.create(OWNER, NewAlert("AAPL", "price_above", 200.0, demo=True, currency="USD"))
    repo.create(OWNER, NewAlert("MSFT", "price_above", 10.0, demo=True))  # not replayed
    assert cli.main(["replay", "aapl", "190", "201", "202"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["190", "-"]
    assert out[1].strip() == "201  fired: AAPL rose above $200.00 (now $201.00)."
    assert out[2].split() == ["202", "-"]
    assert out[3] == "deliveries: 0 sent, 0 to retry, 0 failed"
    (msft,) = [a for a in repo.for_owner(OWNER) if a.symbol == "MSFT"]
    assert msft.last_checked_at is None  # other symbols are left alone


def test_once(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert cli.main(["once"]) == 0
    assert "0 alerts checked, 0 fired" in capsys.readouterr().out


def test_run_installs_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(signal, "signal", handlers.setdefault)

    def fake_loop(self: worker.Worker) -> None:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert self._stop.is_set()

    monkeypatch.setattr(worker.Worker, "loop", fake_loop)
    assert cli.main(["run"]) == 0
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}


def test_usage_errors_exit_nonzero() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["replay", "AAPL"])
    assert info.value.code == 2
