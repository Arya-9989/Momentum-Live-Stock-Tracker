"""A viewer's saved data: everything held about one owner, to download or erase.

``export`` gathers the watchlist, alerts, delivery channels, notifications and
portfolio into one JSON document. Secrets are not included (a webhook's signing
secret is reported only as set or not). ``erase`` deletes the owner row, and
every table that references it cascades with it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

from momentum.store import Store
from momentum.store.owners import Owner, delete_owner

FORMAT_VERSION: Final = 1


def _iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat() if ms is not None else None


def export(store: Store, owner: Owner) -> dict[str, Any]:
    """Everything stored for ``owner`` (an empty document if nothing was ever saved)."""
    with store.db.transaction(write=False) as tx:
        row = tx.one("SELECT created_at, last_seen_at FROM owners WHERE id = ?", (owner.id,))
    watchlist = store.watchlists.get(owner)
    channels = store.alerts.channels(owner)
    return {
        "format": "momentum-export",
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "identity": {
            "kind": owner.kind,
            "first_saved_at": _iso(int(row[0])) if row else None,
            "last_seen_at": _iso(int(row[1])) if row else None,
        },
        "watchlist": list(watchlist.symbols) if watchlist else None,
        "alerts": [
            {
                "symbol": a.symbol,
                "condition": a.condition,
                "threshold": a.threshold,
                "channels": ["app", *a.channels],
                "demo_data": a.demo,
                "state": a.state,
                "created_at": _iso(a.created_at),
                "last_fired_at": _iso(a.last_fired_at),
            }
            for a in store.alerts.for_owner(owner)
        ],
        "delivery": {
            "email": channels.email,
            "webhook_url": channels.webhook_url,
            "webhook_secret_set": channels.webhook_secret is not None,
        },
        "notifications": [
            {"message": e.message, "created_at": _iso(e.created_at), "read": e.read_at is not None}
            for e in store.alerts.events(owner, limit=200)
        ],
        "portfolio": [
            {
                "date": t.trade_date.isoformat(),
                "type": t.kind,
                "symbol": t.symbol,
                "quantity": str(t.quantity),
                "price": str(t.price),
                "fees": str(t.fees),
                "currency": t.currency,
            }
            for t in store.portfolio.transactions(owner)
        ],
    }


def export_json(store: Store, owner: Owner) -> bytes:
    return json.dumps(export(store, owner), indent=2, ensure_ascii=False).encode("utf-8")


def erase(store: Store, owner: Owner) -> bool:
    """Delete everything stored for ``owner``; False if there was nothing to delete."""
    return delete_owner(store.db, owner)
