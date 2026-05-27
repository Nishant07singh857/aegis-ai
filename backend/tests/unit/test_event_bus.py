# backend/tests/unit/test_event_bus.py
"""
Unit tests for the Event Bus system.
"""

import pytest
from unittest.mock import AsyncMock

from app.core.event_bus import EventRecord, EventPriority, BaseEvent


@pytest.mark.unit
class TestEventBus:
    """Test EventBus functionality"""
    
    @pytest.mark.asyncio
    async def test_connect(self, event_bus):
        """Test event bus connection"""
        event_bus._redis.ping = AsyncMock(return_value=True)
        
        await event_bus.connect()
        
        assert event_bus._running is True
    
    @pytest.mark.asyncio
    async def test_publish_event(self, event_bus, sample_event):
        """Test publishing an event"""
        event_bus._redis.xadd = AsyncMock(return_value="event-id")
        event_bus._pubsub_redis.publish = AsyncMock(return_value=1)
        
        event_id = await event_bus.publish(
            event_type="kill",
            data=sample_event["data"],
            priority=EventPriority.HIGH,
            match_id="match_001",
        )
        
        assert event_id is not None
        event_bus._redis.xadd.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_publish_without_connection(self, event_bus, sample_event):
        """Test publishing when not connected"""
        event_bus._running = False
        
        event_id = await event_bus.publish(
            event_type="kill",
            data=sample_event["data"],
        )
        
        assert event_id is None
    
    @pytest.mark.asyncio
    async def test_subscribe(self, event_bus):
        """Test subscribing to events"""
        handler_called = False
        
        async def test_handler(record):
            nonlocal handler_called
            handler_called = True
        
        subscriber_id = event_bus.subscribe(
            subscriber_id="test_subscriber",
            callback=test_handler,
            event_types=["kill"],
        )
        
        assert subscriber_id == "test_subscriber"
        assert "test_subscriber" in event_bus._subscribers
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        """Test unsubscribing from events"""
        event_bus.subscribe(
            subscriber_id="test_subscriber",
            callback=lambda r: None,
            event_types=["kill"],
        )
        
        result = event_bus.unsubscribe("test_subscriber")
        
        assert result is True
        assert "test_subscriber" not in event_bus._subscribers
    
    @pytest.mark.asyncio
    async def test_get_match_events(self, event_bus, sample_event):
        """Test getting match events from history"""
        event_bus._event_history["match_001"] = [
            EventRecord(event=BaseEvent(**sample_event))
        ]
        
        events = await event_bus.get_match_events("match_001", limit=10)
        
        assert len(events) > 0


@pytest.mark.unit
class TestEventPriority:
    """Test event priority ordering"""
    
    def test_priority_ordering(self):
        """Test that CRITICAL < HIGH < MEDIUM < LOW < BACKGROUND"""
        assert EventPriority.CRITICAL < EventPriority.HIGH
        assert EventPriority.HIGH < EventPriority.MEDIUM
        assert EventPriority.MEDIUM < EventPriority.LOW
        assert EventPriority.LOW < EventPriority.BACKGROUND
    
    def test_priority_values(self):
        """Test priority integer values"""
        assert EventPriority.CRITICAL.value == 1
        assert EventPriority.HIGH.value == 2
        assert EventPriority.MEDIUM.value == 3
        assert EventPriority.LOW.value == 4
        assert EventPriority.BACKGROUND.value == 5