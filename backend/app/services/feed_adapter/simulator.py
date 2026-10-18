# backend/app/services/feed_adapter/simulator.py
"""Simulator feed adapter — works TODAY, needs no real game feed.

Wraps the output the EXISTING match simulator (backend/app/main.py:
match_simulator) already publishes to the event bus (kill_event /
knock_event). This adapter does NOT re-subscribe or re-publish those
events — doing so would create a feedback loop (publish -> receive ->
publish). It simply declares the simulator as the active feed source
so /feed/status is truthful, and it implements ingest() for any
additional normalized events an operator posts (e.g. manual marks via
POST /feed/ingest), pushing them through the same bus path.

When a real feed arrives, it replaces this adapter; nothing downstream
(replay buffer, highlights, kill feed) changes.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.event_bus import EventBus

from .base import BaseFeedAdapter

logger = structlog.get_logger("aegis.feed_adapter.simulator")


class SimulatorFeedAdapter(BaseFeedAdapter):
    """Feed adapter for the built-in match simulator."""

    adapter_id = "simulator"
    name = "Built-in match simulator"

    def __init__(self, bus: Optional["EventBus"] = None) -> None:
        super().__init__(bus)
        self._note = (
            "Underlying simulator already publishes kill_event/knock_event "
            "to the event bus; no re-forwarding is performed (avoids "
            "feedback loops). ingest() handles additional normalized events."
        )

    async def connect(self) -> None:
        self._connected = True
        logger.info("Simulator feed adapter connected", note=self._note)

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Simulator feed adapter disconnected")

    def status(self) -> dict:
        base = super().status()
        base["mode"] = "passthrough"
        base["note"] = self._note
        return base
