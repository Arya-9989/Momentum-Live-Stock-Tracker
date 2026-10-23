"""Structured audit events for sensitive actions, one JSON object per line.

Events go to the ``momentum.audit`` logger (stderr by default, so a container's
log collector picks them up). They name the action and a pseudonymous owner
reference, never secrets, email addresses, full webhook URLs or the device id
itself (which, with its signature, is a bearer credential).

Recorded: alerts created and deleted, webhook changes (host only), test
deliveries, data exported and data deleted, and transactions deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from typing import Final

from momentum.store.owners import Owner

logger = logging.getLogger("momentum.audit")
EVENTS: Final = frozenset(
    {
        "alert.created",
        "alert.deleted",
        "webhook.saved",
        "webhook.secret_rotated",
        "webhook.removed",
        "delivery.test_queued",
        "data.exported",
        "data.deleted",
        "portfolio.transactions_deleted",
    }
)

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def owner_ref(owner: Owner | None) -> str | None:
    """``device:3f2a9c1b7d4e``: stable per owner, but not the owner id itself."""
    if owner is None:
        return None
    digest = hashlib.sha256(owner.id.encode("utf-8")).hexdigest()[:12]
    return f"{owner.kind}:{digest}"


def record(event: str, owner: Owner | None, **fields: str | int | float | bool | None) -> None:
    """Emit one audit line; unknown event names are a programming error."""
    if event not in EVENTS:
        msg = f"unknown audit event {event!r}"
        raise ValueError(msg)
    payload = {
        "ts": round(time.time(), 3),
        "audit": event,
        "owner": owner_ref(owner),
        **fields,
    }
    logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))
