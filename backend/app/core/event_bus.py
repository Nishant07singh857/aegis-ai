# backend/app/core/event_bus.py
"""
AEGIS AI - Event Bus System
Central nervous system - all inter-agent communication flows through here.
Redis Streams + Pub/Sub implementation with:
- Persistent event storage (Streams are an append-only log used for
  persistence + replay; delivery does NOT use Redis consumer groups —
  no xreadgroup/xack anywhere in this build. Real-time delivery is
  Pub/Sub plus in-process dispatch to registered subscribers.)
- Real-time broadcasting
- Dead letter queues
- Event replay capability
- Backpressure handling
- Event versioning
- Canonical event-name aliases (Phase 22: app.core.event_names) —
  publishing under a legacy name reaches canonical subscribers and
  vice versa.
- Phase 38 (C-1): REAL in-memory fallback. When Redis is unreachable
  after all connect retries, the bus keeps running in-process
  (``_transport == "memory"``): publish/subscribe/dispatch, history
  and the processing queue all work without Redis, because delivery
  was always the in-process dispatch — no Redis pub/sub consumer
  exists in this codebase and Streams are persistence/replay only.
  What memory mode loses is Redis Stream persistence/replay-from-
  stream and cross-process broadcast; health_check() and the system
  status endpoint report mode="memory" / status="degraded" so the
  degradation is visible, and the health loop probes for Redis
  recovery (single attempt per 10 s tick) and upgrades back when it
  returns. The live spine (vision -> bus -> LiveDirector) therefore
  works with Redis down; it is never silently dead.
"""

import asyncio
import heapq
import itertools
import time
import uuid
import zlib
from typing import (
    AsyncIterator, Dict, List, Optional, Any, Callable, 
    Set, Tuple, Union
)
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

import redis.asyncio as redis
from redis.asyncio import Redis
from redis.exceptions import RedisError, ConnectionError, TimeoutError
import structlog
from pydantic import BaseModel

from ..config import settings

logger = structlog.get_logger("aegis.event_bus")

# Base delay (seconds) between Redis connect retries; doubled each
# attempt (1 s, 2 s, 4 s, 8 s — 15 s total before the Phase 38 in-memory
# fallback). Module-level so tests can shrink it without patching
# asyncio.sleep (which would also speed up the background tickers).
CONNECT_RETRY_DELAY_S = 1.0


def _scrubbed_exc_info(exc: BaseException):
    """``exc_info`` tuple whose formatted traceback prints ONLY the
    scrubbed message — the Phase 37 (``app.db.base``) surrogate
    pattern, delegated so there is exactly one implementation.

    Lazy import: keeps event_bus importable without the DB layer
    (the same reason ``_persist_event`` lazily imports its session
    factory). ``exc_info=True`` would print the ORIGINAL exception's
    message as the traceback's final line, so scrubbing only the
    log-message f-string is not enough — a DB error embedding
    DATABASE_URL would still reach the log via the traceback.
    """
    from ..db.base import _scrubbed_exc_info as _base_scrubbed_exc_info

    return _base_scrubbed_exc_info(exc)


def _scrubbed_text(exc: BaseException) -> str:
    """Scrubbed ``str(exc)`` for embedding in a log message.

    Same lazy-import reason as ``_scrubbed_exc_info``. The configured
    DATABASE_URL is passed as a known secret so even a driver message
    echoing it verbatim is redacted.
    """
    from ..db.error_scrub import scrub_db_error_text

    return scrub_db_error_text(exc, secrets=[settings.DATABASE_URL])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EVENT DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EventPriority(int, Enum):
    """Event priority levels"""
    CRITICAL = 1    # Process immediately (kill events, fight start)
    HIGH = 2        # Process quickly (knocks, zone changes)
    MEDIUM = 3      # Normal processing (position updates, utility)
    LOW = 4         # Deferred processing (analytics, stats)
    BACKGROUND = 5  # Batch processing (reports, exports)

class EventStatus(str, Enum):
    """Event processing status"""
    PUBLISHED = "published"
    ACKNOWLEDGED = "acknowledged"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    REPLAYED = "replayed"

class EventVersion(str, Enum):
    """Event schema versions for compatibility"""
    V1_0 = "1.0"
    V1_1 = "1.1"
    V2_0 = "2.0"

class BaseEvent(BaseModel):
    """Base event model - all events must inherit from this"""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: EventVersion = EventVersion.V1_0
    priority: EventPriority = EventPriority.MEDIUM
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    timestamp_iso: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    source: str = "system"  # agent_id, service_name, etc.
    match_id: Optional[str] = None
    tournament_id: Optional[str] = None
    correlation_id: Optional[str] = None  # For tracing related events
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class EventRecord:
    """Complete event record with processing info"""
    event: BaseEvent
    status: EventStatus = EventStatus.PUBLISHED
    published_at: float = field(default_factory=time.time)
    acknowledged_by: List[str] = field(default_factory=list)
    processed_by: List[str] = field(default_factory=list)
    error_count: int = 0
    last_error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3

@dataclass
class Subscriber:
    """Event subscriber information"""
    subscriber_id: str
    callback: Callable
    event_types: Set[str]
    priority: int = 0
    filter_func: Optional[Callable] = None
    is_active: bool = True
    subscribed_at: float = field(default_factory=time.time)
    events_processed: int = 0
    last_event_time: Optional[float] = None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EVENT BUS IMPLEMENTATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EventBus:
    """
    Central Event Bus for AEGIS AI.
    
    Architecture:
    - Redis Streams: Persistent event log (persistence/replay only;
      no consumer groups — delivery is the two mechanisms below)
    - Redis Pub/Sub: Real-time event broadcasting
    - In-memory subscribers: Low-latency local dispatch
    - Dead letter queue: Failed event storage
    - Event replay: Rewind and replay events

    Features:
    - Event prioritization
    - Backpressure handling
    - Automatic retry with exponential backoff
    - Event compression for large payloads
    - Circuit breaker pattern
    - Health monitoring
    """
    
    def __init__(self, max_processing_queue: int = 10000):
        # Redis connections
        self._redis: Optional[Redis] = None
        self._pubsub_redis: Optional[Redis] = None
        self._consumer_redis: Optional[Redis] = None
        
        # Subscriber management
        self._subscribers: Dict[str, Subscriber] = {}  # subscriber_id -> Subscriber
        self._type_subscribers: Dict[str, List[str]] = {}  # event_type -> [subscriber_ids]
        
        # Event processing
        self._event_history: Dict[str, List[EventRecord]] = {}  # match_id -> recent events
        # Bounded queue: producers never block forever. When full, the
        # lowest-priority item is evicted (see _enqueue_for_processing).
        # The monotonic tiebreaker keeps tuple comparison total even when
        # two events share a priority (EventRecord itself is not orderable).
        self.max_processing_queue = max_processing_queue
        self._processing_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(
            maxsize=max_processing_queue
        )
        self._queue_tiebreaker = itertools.count()
        self._dead_letter_queue: List[EventRecord] = []
        
        # Circuit breaker
        self._failure_count: Dict[str, int] = {}
        self._circuit_open: Dict[str, bool] = {}
        self._circuit_open_time: Dict[str, float] = {}
        
        # State
        self._running: bool = False
        # Phase 38 (C-1): transport mode — "redis" (full: streams +
        # cross-process broadcast) or "memory" (in-process only; the
        # live spine keeps working, persistence/broadcast are off).
        self._transport: str = "redis"
        self._listener_tasks: List[asyncio.Task] = []
        self._health_check_task: Optional[asyncio.Task] = None
        # Guard so a re-connect (health-loop reconnect, Redis recovery
        # from memory mode) never duplicates the background tasks.
        self._background_tasks_started: bool = False
        
        # Statistics
        self._stats = {
            "events_published": 0,
            "events_processed": 0,
            "events_failed": 0,
            "events_dropped": 0,
            "active_subscribers": 0,
            "queue_size": 0,
            "dead_letter_size": 0,
        }
        
        # Configuration
        self.max_history_per_match = 10000
        self.max_dead_letter_queue = 1000
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_reset_seconds = 60
        self.compression_threshold_bytes = 1024  # Compress events > 1KB

        # DB session factory for event persistence. Defaults to the app's
        # shared AsyncSessionLocal (resolved lazily); tests may inject a
        # factory bound to their own engine.
        self._session_factory: Optional[Callable[[], Any]] = None
        
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONNECTION MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def connect(self) -> None:
        """Establish Redis connections with retry logic.

        Phase 38 (C-1): when Redis is unreachable after all retries
        the bus falls back to REAL in-memory mode instead of raising
        (see ``_enter_memory_mode``) — the live spine is entirely
        in-process, so publish/subscribe keep working and the
        lifespan services gated on ``_running`` still wire up. The
        fallback is logged loudly and health reports it, so nothing
        silently pretends Redis is there. ``connect()`` now raises
        only when even the in-process bus cannot start (e.g. no
        running event loop for the background tasks).
        """
        max_retries = 5
        retry_delay = CONNECT_RETRY_DELAY_S

        for attempt in range(max_retries):
            try:
                # Main connection
                self._redis = redis.Redis.from_url(
                    settings.REDIS_URL,
                    max_connections=settings.REDIS_MAX_CONNECTIONS,
                    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                    retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
                    decode_responses=True,
                )

                # Pub/Sub connection (separate connection for blocking operations)
                self._pubsub_redis = redis.Redis.from_url(
                    settings.REDIS_URL,
                    max_connections=10,
                    socket_timeout=None,  # No timeout for blocking
                    decode_responses=True,
                )

                # Consumer connection
                self._consumer_redis = redis.Redis.from_url(
                    settings.REDIS_URL,
                    max_connections=20,
                    decode_responses=True,
                )

                # Test connections
                await self._redis.ping()
                await self._pubsub_redis.ping()
                await self._consumer_redis.ping()

                self._transport = "redis"
                self._running = True

                # Start background tasks
                self._start_background_tasks()

                logger.info(
                    "EventBus connected successfully",
                    attempt=attempt + 1,
                    max_connections=settings.REDIS_MAX_CONNECTIONS
                )
                return

            except (ConnectionError, TimeoutError) as e:
                logger.warning(
                    f"Redis connection attempt {attempt + 1}/{max_retries} failed: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))
                else:
                    logger.error("All Redis connection attempts failed")
                    await self._enter_memory_mode()
                    return

            except Exception as e:
                # Phase 38 (M-3): scrub BOTH channels — the message
                # f-string AND the traceback (see _scrubbed_exc_info).
                # A DB error whose message embeds DATABASE_URL must
                # never reach the log via either one. The bus still
                # falls back to in-memory mode: Redis-independence is
                # the point of the fallback, whatever the failure was.
                logger.error(
                    "Unexpected Redis connection error: "
                    f"{_scrubbed_text(e)}; continuing in-memory",
                    exc_info=_scrubbed_exc_info(e),
                )
                await self._enter_memory_mode()
                return

    def _start_background_tasks(self) -> None:
        """Start the queue / dead-letter / health background tasks once.

        Guarded: connect() can run again (health-loop reconnect, or a
        Redis recovery from memory mode) and must never duplicate the
        tasks — the old code appended a fresh set on every connect().
        disconnect() resets the guard.
        """
        if self._background_tasks_started:
            return
        self._background_tasks_started = True
        self._listener_tasks.append(
            asyncio.create_task(self._process_event_queue())
        )
        self._listener_tasks.append(
            asyncio.create_task(self._process_dead_letter_queue())
        )
        self._health_check_task = asyncio.create_task(
            self._health_check_loop()
        )

    async def _discard_redis_clients(self) -> None:
        """Best-effort close + drop of the three Redis clients.

        Used when entering memory mode: the clients are dead or
        never-connected, and the memory path must never touch them.
        A failure here is swallowed — a half-dead client must not
        block the fallback.
        """
        for conn in (self._redis, self._pubsub_redis, self._consumer_redis):
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass
        self._redis = None
        self._pubsub_redis = None
        self._consumer_redis = None

    async def _enter_memory_mode(self) -> None:
        """Enter REAL in-memory mode (Phase 38, C-1).

        The bus keeps serving in-process: publish() dispatches to
        local subscribers, keeps history and the processing queue —
        the live spine's actual path, which never needed Redis (no
        Redis pub/sub consumer exists in this codebase; Streams are
        persistence/replay only). Lost in this mode: Redis Stream
        persistence/replay-from-stream and cross-process broadcast.
        Idempotent: re-entering from an already-memory bus is a no-op
        (the background tasks are already running).
        """
        if self._transport == "memory" and self._running:
            return
        await self._discard_redis_clients()
        self._transport = "memory"
        self._running = True
        self._start_background_tasks()
        logger.warning(
            "EventBus running IN-MEMORY (Redis unreachable): the live "
            "spine works in-process (publish/subscribe/dispatch), but "
            "Redis Stream persistence and cross-process broadcast are "
            "OFF. Health reports mode=memory / status=degraded until "
            "Redis returns (auto-recovery probes every 10s)."
        )

    async def _try_redis_reconnect(self) -> bool:
        """Single-attempt Redis recovery probe from memory mode.

        Creates ONE throwaway client and pings it; on success hands
        over to connect() (which rebuilds the three real clients and
        flips the transport back to Redis). On failure the bus stays
        in memory mode — nothing is torn down and no retry storm is
        started (the health loop calls this at most once per 10 s
        tick). Returns True when the bus is back on Redis.
        """
        probe = None
        try:
            probe = redis.Redis.from_url(
                settings.REDIS_URL,
                max_connections=1,
                socket_timeout=2,
                decode_responses=True,
            )
            await probe.ping()
        except Exception:
            return False
        finally:
            if probe is not None:
                try:
                    await probe.close()
                except Exception:
                    pass
        try:
            await self.connect()
        except Exception:
            return False
        return self._transport == "redis"
    
    async def disconnect(self) -> None:
        """Gracefully disconnect from Redis"""
        self._running = False
        
        # Cancel all background tasks
        for task in self._listener_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Phase 38 (C-1): a disconnect is a clean slate — drop the
        # (cancelled) task handles and the task-start guard, and reset
        # the transport so a later connect() retries Redis first
        # instead of assuming the previous mode.
        self._listener_tasks = []
        self._health_check_task = None
        self._background_tasks_started = False
        self._transport = "redis"
        
        # Close connections
        for conn, name in [
            (self._redis, "main"),
            (self._pubsub_redis, "pubsub"),
            (self._consumer_redis, "consumer"),
        ]:
            if conn:
                try:
                    await conn.close()
                    logger.debug(f"Closed {name} Redis connection")
                except Exception as e:
                    logger.error(f"Error closing {name} connection: {e}")
        
        logger.info("EventBus disconnected")
    
    async def _check_connection(self) -> bool:
        """Check if Redis connection is alive"""
        try:
            if self._redis:
                await self._redis.ping()
                return True
        except Exception:
            pass
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT PUBLISHING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def publish(
        self,
        event_type: str,
        data: Dict[str, Any],
        priority: EventPriority = EventPriority.MEDIUM,
        match_id: Optional[str] = None,
        tournament_id: Optional[str] = None,
        source: str = "system",
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Publish an event to the bus.
        
        Args:
            event_type: Type of event (e.g., 'kill', 'fight_detected')
            data: Event payload data
            priority: Event priority level
            match_id: Associated match ID
            tournament_id: Associated tournament ID
            source: Source of the event (agent_id, service name)
            correlation_id: For linking related events
            metadata: Additional metadata
            
        Returns:
            event_id if successful, None if failed
        """
        if not self._running:
            logger.warning("EventBus not running, dropping event")
            self._stats["events_dropped"] += 1
            return None
        
        # Create event
        event = BaseEvent(
            event_type=event_type,
            priority=priority,
            source=source,
            match_id=match_id,
            tournament_id=tournament_id,
            correlation_id=correlation_id,
            data=data,
            metadata=metadata or {},
        )
        
        # Create event record
        record = EventRecord(event=event)
        
        try:
            # Serialize event
            event_json = event.model_dump_json()
            
            # Compress large events
            if len(event_json) > self.compression_threshold_bytes:
                event_json = zlib.compress(event_json.encode()).hex()

            # Phase 38 (C-1): Redis transport only — Stream
            # persistence + cross-process Pub/Sub. In memory mode
            # these are skipped: delivery is the in-process dispatch
            # below, which is the live spine's actual path (no Redis
            # pub/sub consumer exists in this codebase).
            if self._transport == "redis":
                # Store in Redis Stream for persistence
                stream_name = self._get_stream_name(event)

                await self._redis.xadd(
                    stream_name,
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "priority": str(event.priority.value),
                        "timestamp_ms": str(event.timestamp_ms),
                        "source": event.source,
                        "match_id": event.match_id or "",
                        "data": event_json,
                    },
                    maxlen=settings.REDIS_STREAM_MAX_LEN,
                    approximate=True,
                )

                # Publish for real-time subscribers
                channel = f"events:{event.event_type}"
                if match_id:
                    channel = f"match:{match_id}:{event.event_type}"

                await self._pubsub_redis.publish(
                    channel,
                    event.model_dump_json()
                )
            
            # Dispatch to local subscribers
            await self._dispatch_to_subscribers(record)
            
            # Store in local history
            if match_id:
                self._add_to_history(match_id, record)
            
            # Add to processing queue for high priority events
            # (bounded: never blocks the producer; drops are counted)
            if priority <= EventPriority.HIGH:
                self._enqueue_for_processing(priority.value, record)
            
            # Update stats
            self._stats["events_published"] += 1
            
            logger.debug(
                f"Event published: {event.event_type} ({event.event_id})",
                priority=priority.name,
                match_id=match_id
            )
            
            return event.event_id
            
        except RedisError as e:
            logger.error(f"Redis error publishing event {event_type}: {e}")
            self._stats["events_failed"] += 1
            self._add_to_dead_letter(record, str(e))
            return None

        except Exception as e:
            # Phase 38 (M-3): scrub BOTH channels — the message
            # f-string AND the traceback (see _scrubbed_exc_info). A
            # subscriber/DB error whose message embeds DATABASE_URL
            # must never reach the log via either one.
            logger.error(
                f"Unexpected error publishing event {event_type}: "
                f"{_scrubbed_text(e)}",
                exc_info=_scrubbed_exc_info(e),
            )
            self._stats["events_failed"] += 1
            return None
    
    async def publish_batch(
        self,
        events: List[Tuple[str, Dict[str, Any]]],
        priority: EventPriority = EventPriority.MEDIUM,
        match_id: Optional[str] = None,
    ) -> int:
        """
        Publish multiple events in batch (more efficient).
        Returns number of successfully published events.
        """
        if not self._running:
            return 0

        # Phase 38 (C-1): no Redis pipeline in memory mode — publish
        # each event through the normal (in-process) path instead.
        if self._transport == "memory":
            success_count = 0
            for event_type, data in events:
                if (
                    await self.publish(
                        event_type,
                        data,
                        priority=priority,
                        match_id=match_id,
                    )
                    is not None
                ):
                    success_count += 1
            return success_count

        success_count = 0
        
        # Use pipeline for batch Redis operations
        async with self._redis.pipeline() as pipe:
            for event_type, data in events:
                event = BaseEvent(
                    event_type=event_type,
                    priority=priority,
                    match_id=match_id,
                    data=data,
                )
                
                stream_name = self._get_stream_name(event)
                event_json = event.model_dump_json()
                
                pipe.xadd(
                    stream_name,
                    {
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "data": event_json,
                    },
                    maxlen=settings.REDIS_STREAM_MAX_LEN,
                    approximate=True,
                )
                
                success_count += 1
            
            await pipe.execute()
        
        self._stats["events_published"] += success_count
        logger.debug(f"Batch published {success_count} events")
        
        return success_count
    
    def _get_stream_name(self, event: BaseEvent) -> str:
        """Get Redis stream name for event"""
        if event.match_id:
            return f"stream:match:{event.match_id}"
        elif event.tournament_id:
            return f"stream:tournament:{event.tournament_id}"
        return "stream:global"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SUBSCRIBER MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def subscribe(
        self,
        subscriber_id: str,
        callback: Callable,
        event_types: Union[str, List[str]],
        priority: int = 0,
        filter_func: Optional[Callable] = None,
    ) -> str:
        """
        Subscribe to events.
        
        Args:
            subscriber_id: Unique subscriber identifier
            callback: Async or sync function to call with event
            event_types: Single event type or list of types to subscribe to
            priority: Higher priority subscribers get events first
            filter_func: Optional function to filter events
            
        Returns:
            subscriber_id
        """
        if isinstance(event_types, str):
            event_types = [event_types]
        
        # Create subscriber
        subscriber = Subscriber(
            subscriber_id=subscriber_id,
            callback=callback,
            event_types=set(event_types),
            priority=priority,
            filter_func=filter_func,
        )
        
        # Register subscriber
        self._subscribers[subscriber_id] = subscriber
        
        # Register for each event type
        for event_type in event_types:
            if event_type not in self._type_subscribers:
                self._type_subscribers[event_type] = []
            self._type_subscribers[event_type].append(subscriber_id)
        
        self._stats["active_subscribers"] = len(self._subscribers)
        
        logger.info(
            f"Subscriber registered: {subscriber_id}",
            event_types=event_types,
            total_subscribers=self._stats["active_subscribers"]
        )
        
        return subscriber_id
    
    def unsubscribe(self, subscriber_id: str) -> bool:
        """Unsubscribe from events"""
        if subscriber_id not in self._subscribers:
            return False
        
        subscriber = self._subscribers[subscriber_id]
        
        # Remove from type subscribers
        for event_type in subscriber.event_types:
            if event_type in self._type_subscribers:
                self._type_subscribers[event_type].remove(subscriber_id)
                if not self._type_subscribers[event_type]:
                    del self._type_subscribers[event_type]
        
        # Remove subscriber
        del self._subscribers[subscriber_id]
        self._stats["active_subscribers"] = len(self._subscribers)
        
        logger.info(f"Subscriber removed: {subscriber_id}")
        return True
    
    async def _dispatch_to_subscribers(self, record: EventRecord) -> None:
        """Dispatch event to all matching subscribers"""
        event = record.event
        event_type = event.event_type

        # Get subscribers for this event type. Phase 22 (M12): also
        # resolve canonical/alias equivalents (app.core.event_names)
        # so a publisher using a legacy name (kill_event) reaches a
        # subscriber registered under the canonical name
        # (kill.occurred) and vice versa. Deduped, order-stable.
        subscriber_ids: List[str] = []
        seen_ids: Set[str] = set()
        lookup_types = [event_type]
        try:
            from .event_names import equivalent_names

            lookup_types = sorted(equivalent_names(event_type))
        except Exception:  # pragma: no cover - alias table unavailable
            lookup_types = [event_type]
        for lookup in lookup_types:
            for sid in self._type_subscribers.get(lookup, []):
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    subscriber_ids.append(sid)

        # Also dispatch to wildcard subscribers
        for sid in self._type_subscribers.get("*", []):
            if sid not in seen_ids:
                seen_ids.add(sid)
                subscriber_ids.append(sid)
        
        for subscriber_id in subscriber_ids:
            if subscriber_id not in self._subscribers:
                continue
            
            subscriber = self._subscribers[subscriber_id]
            
            if not subscriber.is_active:
                continue
            
            # Apply filter
            if subscriber.filter_func and not subscriber.filter_func(event):
                continue
            
            # Call subscriber
            try:
                callback = subscriber.callback
                
                if asyncio.iscoroutinefunction(callback):
                    await callback(record)
                else:
                    # Run sync callbacks in executor
                    await asyncio.get_event_loop().run_in_executor(
                        None, callback, record
                    )
                
                subscriber.events_processed += 1
                subscriber.last_event_time = time.time()
                
            except Exception as e:
                # Phase 38 (M-3): scrub BOTH channels — the message
                # f-string AND the traceback (see _scrubbed_exc_info).
                # A subscriber callback can raise a DB error whose
                # message embeds DATABASE_URL; it must never reach
                # the log via either one.
                logger.error(
                    f"Subscriber {subscriber_id} error: {_scrubbed_text(e)}",
                    event_type=event_type,
                    exc_info=_scrubbed_exc_info(e)
                )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT PROCESSING QUEUE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _enqueue_for_processing(self, priority_value: int, record: EventRecord) -> bool:
        """Enqueue a record for background processing without ever blocking.

        Policy when the queue is full: evict the single lowest-priority
        (largest priority value) queued item if the newcomer is more
        important; otherwise drop the newcomer. Every drop increments
        ``events_dropped``. Returns True when the record was enqueued.
        """
        item = (priority_value, next(self._queue_tiebreaker), record)
        try:
            self._processing_queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            pass

        heap = self._processing_queue._queue  # heap of (priority, seq, record)
        worst = max(heap, key=lambda it: (it[0], it[1])) if heap else None

        if worst is not None and priority_value < worst[0]:
            try:
                heap.remove(worst)
                heapq.heapify(heap)
            except ValueError:
                pass
            else:
                try:
                    self._processing_queue.put_nowait(item)
                except asyncio.QueueFull:
                    pass
                else:
                    self._stats["events_dropped"] += 1
                    logger.warning(
                        "Processing queue full; evicted lowest-priority event "
                        "for higher-priority event",
                        evicted_type=worst[2].event.event_type,
                        new_type=record.event.event_type,
                    )
                    return True

        self._stats["events_dropped"] += 1
        logger.warning(
            "Processing queue full; dropping event",
            event_type=record.event.event_type,
            event_id=record.event.event_id,
            priority=priority_value,
        )
        return False

    async def _process_event_queue(self) -> None:
        """Background task: process high-priority events"""
        while self._running:
            try:
                priority, _seq, record = await asyncio.wait_for(
                    self._processing_queue.get(),
                    timeout=1.0
                )
                
                await self._process_event(record)
                self._processing_queue.task_done()
                self._stats["events_processed"] += 1
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Phase 38 (M-3): scrub BOTH channels — the message
                # f-string AND the traceback (see _scrubbed_exc_info).
                logger.error(
                    f"Event processing error: {_scrubbed_text(e)}",
                    exc_info=_scrubbed_exc_info(e),
                )
    
    async def _process_event(self, record: EventRecord) -> None:
        """Process a single event"""
        event = record.event
        
        try:
            # Update event status
            record.status = EventStatus.PROCESSING
            
            # Store event in database (async)
            await self._persist_event(event)
            
            # Mark as processed
            record.status = EventStatus.PROCESSED
            record.processed_by.append("event_bus")
            
        except Exception as e:
            record.status = EventStatus.FAILED
            record.error_count += 1
            record.last_error = str(e)
            
            # Retry or move to dead letter
            if record.retry_count < record.max_retries:
                record.retry_count += 1
                # Re-queue with exponential backoff
                delay = 2 ** record.retry_count
                await asyncio.sleep(delay)
                self._enqueue_for_processing(record.event.priority.value, record)
            else:
                self._add_to_dead_letter(record, str(e))
    
    async def _persist_event(self, event: BaseEvent) -> None:
        """Persist event to the ``event_store`` table for long-term storage.

        Failure-safe: any DB error (unavailable DB, constraint conflict,
        serialization issue) is logged and swallowed — the event flow
        must never break because persistence failed.
        """
        try:
            session_factory = self._session_factory
            if session_factory is None:
                # Lazy import: keeps event_bus importable without a DB.
                from app.db.session import AsyncSessionLocal

                session_factory = AsyncSessionLocal
            from app.db.models.event_store import EventStore

            row = EventStore(
                event_id=event.event_id,
                event_type=event.event_type,
                event_version=getattr(event.event_version, "value", str(event.event_version)),
                priority=getattr(event.priority, "value", int(event.priority)),
                source=event.source or "system",
                match_id=event.match_id,
                tournament_id=event.tournament_id,
                correlation_id=event.correlation_id,
                timestamp_ms=event.timestamp_ms,
                data=dict(event.data) if event.data else {},
                meta=dict(event.metadata) if event.metadata else {},
            )

            async with session_factory() as session:
                async with session.begin():
                    session.add(row)
        except Exception as e:
            # Never break the event flow on a persistence failure.
            # Phase 38 (M-3, 5th site — beyond the brief's 4
            # exc_info=True sites): this is a DB error by
            # construction, so its raw str() can embed DATABASE_URL;
            # scrub it like every other DB-error log line.
            logger.warning(
                "Event persistence failed; continuing without DB write",
                error=_scrubbed_text(e),
                event_id=getattr(event, "event_id", None),
                event_type=getattr(event, "event_type", None),
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEAD LETTER QUEUE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _add_to_dead_letter(self, record: EventRecord, error: str) -> None:
        """Add failed event to dead letter queue"""
        record.status = EventStatus.DEAD_LETTER
        record.last_error = error
        
        self._dead_letter_queue.append(record)
        
        # Trim if too large
        if len(self._dead_letter_queue) > self.max_dead_letter_queue:
            self._dead_letter_queue = self._dead_letter_queue[-self.max_dead_letter_queue:]
        
        self._stats["dead_letter_size"] = len(self._dead_letter_queue)
        
        logger.error(
            f"Event moved to dead letter: {record.event.event_type} ({record.event.event_id})",
            error=error,
            dead_letter_count=len(self._dead_letter_queue)
        )
    
    async def _process_dead_letter_queue(self) -> None:
        """Background task: periodically retry dead letter events"""
        while self._running:
            try:
                await asyncio.sleep(30)  # Retry every 30 seconds
                
                if not self._dead_letter_queue:
                    continue
                
                # Try to reprocess dead letter events
                to_retry = self._dead_letter_queue[:10]  # Process 10 at a time
                
                for record in to_retry:
                    try:
                        record.retry_count += 1
                        await self._process_event(record)
                        self._dead_letter_queue.remove(record)
                        logger.info(f"Dead letter event recovered: {record.event.event_id}")
                    except Exception as e:
                        logger.warning(f"Dead letter retry failed: {e}")
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Dead letter processing error: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HISTORY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _add_to_history(self, match_id: str, record: EventRecord) -> None:
        """Add event to in-memory history"""
        if match_id not in self._event_history:
            self._event_history[match_id] = []
        
        self._event_history[match_id].append(record)
        
        # Trim history
        if len(self._event_history[match_id]) > self.max_history_per_match:
            self._event_history[match_id] = \
                self._event_history[match_id][-self.max_history_per_match:]
    
    async def get_match_events(
        self,
        match_id: str,
        event_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get events for a specific match"""
        events = self._event_history.get(match_id, [])
        
        if event_type:
            events = [e for e in events if e.event.event_type == event_type]
        
        events = events[offset:offset + limit]
        
        return [e.event.model_dump() for e in events]
    
    async def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific event by ID"""
        # Search in history
        for match_events in self._event_history.values():
            for record in match_events:
                if record.event.event_id == event_id:
                    return record.event.model_dump()
        
        # Search in Redis stream
        try:
            # This would search all streams - simplified for now
            pass
        except Exception:
            pass
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT REPLAY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def replay_events(
        self,
        match_id: str,
        from_timestamp_ms: Optional[int] = None,
        to_timestamp_ms: Optional[int] = None,
        event_types: Optional[List[str]] = None,
        speed: float = 1.0,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Replay events from a match.
        
        Args:
            match_id: Match to replay
            from_timestamp_ms: Start time
            to_timestamp_ms: End time
            event_types: Filter by event types
            speed: Replay speed multiplier
            
        Yields:
            Events in chronological order
        """
        events = self._event_history.get(match_id, [])
        
        if from_timestamp_ms:
            events = [e for e in events if e.event.timestamp_ms >= from_timestamp_ms]
        
        if to_timestamp_ms:
            events = [e for e in events if e.event.timestamp_ms <= to_timestamp_ms]
        
        if event_types:
            events = [e for e in events if e.event.event_type in event_types]
        
        # Sort by timestamp
        events.sort(key=lambda e: e.event.timestamp_ms)
        
        # Replay with timing
        last_timestamp = None
        for record in events:
            if last_timestamp and speed > 0:
                delay = (record.event.timestamp_ms - last_timestamp) / 1000.0 / speed
                if delay > 0:
                    await asyncio.sleep(min(delay, 5.0))  # Cap at 5 seconds
            
            record.event.status = EventStatus.REPLAYED
            yield record.event.model_dump()
            
            last_timestamp = record.event.timestamp_ms
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CIRCUIT BREAKER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _check_circuit_breaker(self, service_name: str) -> bool:
        """Check if circuit is open for a service"""
        if service_name not in self._circuit_open:
            return False
        
        if self._circuit_open[service_name]:
            # Check if reset time has passed
            elapsed = time.time() - self._circuit_open_time.get(service_name, 0)
            if elapsed > self.circuit_breaker_reset_seconds:
                # Try half-open
                self._circuit_open[service_name] = False
                self._failure_count[service_name] = 0
                logger.info(f"Circuit breaker half-open for {service_name}")
                return False
            return True
        
        return False
    
    def _record_failure(self, service_name: str) -> None:
        """Record a service failure"""
        if service_name not in self._failure_count:
            self._failure_count[service_name] = 0
        
        self._failure_count[service_name] += 1
        
        if self._failure_count[service_name] >= self.circuit_breaker_threshold:
            self._circuit_open[service_name] = True
            self._circuit_open_time[service_name] = time.time()
            logger.warning(
                f"Circuit breaker OPEN for {service_name}",
                failures=self._failure_count[service_name]
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEALTH CHECK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _health_check_loop(self) -> None:
        """Background health check loop"""
        while self._running:
            try:
                await asyncio.sleep(10)

                if self._transport == "memory":
                    # Phase 38 (C-1): probe for Redis recovery — a
                    # single attempt per tick, no retry storm. On
                    # success the bus upgrades to Redis mode; on
                    # failure it stays in memory mode and keeps
                    # serving in-process.
                    await self._try_redis_reconnect()
                elif not await self._check_connection():
                    logger.error("Redis connection lost, attempting reconnect...")
                    try:
                        await self.connect()
                    except Exception:
                        pass

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def health_check(self) -> Dict[str, Any]:
        """Get event bus health status"""
        redis_healthy = await self._check_connection()

        # Phase 38 (C-1): "healthy" ONLY when the bus is running on
        # Redis. Memory mode keeps the live spine working in-process
        # but has no stream persistence / cross-process broadcast, so
        # it is "degraded" — and a stopped bus is "degraded", never
        # "healthy".
        if self._running and self._transport == "redis" and redis_healthy:
            status = "healthy"
        else:
            status = "degraded"

        return {
            "status": status,
            "mode": self._transport,  # "redis" | "memory"
            "redis_connected": redis_healthy,
            "running": self._running,
            "subscribers": self._stats["active_subscribers"],
            "queue_size": self._processing_queue.qsize(),
            "dead_letter_count": len(self._dead_letter_queue),
            "circuits_open": [
                name for name, is_open in self._circuit_open.items() if is_open
            ],
            "stats": self._stats,
            "timestamp": time.time(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get event bus statistics"""
        return {
            **self._stats,
            "mode": self._transport,  # Phase 38 (C-1): "redis" | "memory"
            "queue_size": self._processing_queue.qsize(),
            "dead_letter_size": len(self._dead_letter_queue),
            "history_size": sum(len(v) for v in self._event_history.values()),
            "timestamp": time.time(),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GLOBAL EVENT BUS INSTANCE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

event_bus = EventBus()