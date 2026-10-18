# backend/app/services/feed_adapter/base.py
"""Base feed adapter ABC — every game-data source implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

import structlog

from app.core.event_bus import EventPriority

from .schemas import BUS_EVENT_TYPE_MAP, NormalizedEvent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.event_bus import EventBus

logger = structlog.get_logger("aegis.feed_adapter")


class BaseFeedAdapter(ABC):
    """Contract for game feed sources.

    Implementations:
      * connect()    — open the source (socket, file, observer relay...).
      * disconnect() — close it cleanly.
      * ingest()     — validate + publish ONE normalized event to the bus
                       (concrete implementation shared here).
    """

    adapter_id: str = "base"
    name: str = "Base feed adapter"

    def __init__(self, bus: Optional["EventBus"] = None) -> None:
        self._bus = bus
        self._connected = False
        self._ingested = 0

    @abstractmethod
    async def connect(self) -> None:
        """Open the feed source. Must set self._connected = True."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the feed source. Must set self._connected = False."""

    async def ingest(self, event: NormalizedEvent) -> str:
        """Validate (pydantic already did) and publish to the event bus.

        Raises RuntimeError when no bus is attached or the bus dropped
        the event — ingestion must never silently vanish.
        """
        if self._bus is None:
            raise RuntimeError(
                f"Feed adapter {self.adapter_id!r} has no event bus attached."
            )
        bus_type = BUS_EVENT_TYPE_MAP[event.event_type]
        priority = (
            EventPriority.HIGH
            if event.event_type in ("kill", "knock", "team_wipe")
            else EventPriority.MEDIUM
        )
        event_id = await self._bus.publish(
            event_type=bus_type,
            data=event.to_bus_payload(),
            priority=priority,
            match_id=event.match_id,
            source=f"feed_adapter:{self.adapter_id}",
        )
        if event_id is None:
            raise RuntimeError(
                f"Event bus dropped ingest of {event.event_id} "
                f"({event.event_type}); is the bus running?"
            )
        self._ingested += 1
        logger.debug(
            "Feed event ingested",
            adapter=self.adapter_id,
            bus_type=bus_type,
            event_id=event_id,
        )
        return event_id

    def status(self) -> Dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "name": self.name,
            "connected": self._connected,
            "ingested_events": self._ingested,
            "supported_event_types": [t.value for t in BUS_EVENT_TYPE_MAP],
        }
