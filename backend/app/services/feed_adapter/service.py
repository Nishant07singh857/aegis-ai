# backend/app/services/feed_adapter/service.py
"""Feed service: adapter registry + active-adapter selection."""

from __future__ import annotations

from typing import Dict, Optional, TYPE_CHECKING

import structlog

from .simulator import SimulatorFeedAdapter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.event_bus import EventBus
    from .base import BaseFeedAdapter

logger = structlog.get_logger("aegis.feed_adapter.service")


class FeedService:
    """Owns the adapter registry. The simulator adapter is the default
    active source the moment attach(bus) is called (coordinator wires it
    in app/main.py lifespan)."""

    def __init__(self) -> None:
        self._bus: Optional["EventBus"] = None
        self._adapters: Dict[str, "BaseFeedAdapter"] = {}
        self._active_id: Optional[str] = None

    async def attach(self, bus: "EventBus") -> None:
        """Bind the service to the process event bus and activate the
        simulator adapter (the only source that works without a real
        feed)."""
        self._bus = bus
        sim = SimulatorFeedAdapter(bus)
        await sim.connect()
        self._adapters[sim.adapter_id] = sim
        self._active_id = sim.adapter_id
        logger.info("Feed service attached; active adapter: simulator")

    def register(self, adapter: "BaseFeedAdapter") -> None:
        self._adapters[adapter.adapter_id] = adapter

    def get_adapter(self, adapter_id: str) -> Optional["BaseFeedAdapter"]:
        """Return a registered adapter by id, or None."""
        return self._adapters.get(adapter_id)

    def set_active(self, adapter_id: str) -> None:
        if adapter_id not in self._adapters:
            raise KeyError(adapter_id)
        self._active_id = adapter_id

    @property
    def active(self) -> Optional["BaseFeedAdapter"]:
        return self._adapters.get(self._active_id) if self._active_id else None

    def status(self) -> Dict:
        active = self.active
        return {
            "active_adapter": active.status() if active else None,
            "registered_adapters": sorted(self._adapters.keys()),
            "bus_attached": self._bus is not None,
            # Phase 26: every registered adapter's status, not only the
            # active one — the vision adapter carries the lifespan
            # multi-feed monitor's status, which must stay visible
            # while the simulator is the active source.
            "adapters": {
                adapter_id: adapter.status()
                for adapter_id, adapter in sorted(self._adapters.items())
            },
        }


#: Process-wide singleton used by the API router and app/main.py.
feed_service = FeedService()
