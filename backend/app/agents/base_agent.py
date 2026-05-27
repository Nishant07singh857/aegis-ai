# backend/app/agents/base_agent.py
"""
AEGIS AI - Base Agent Class
Abstract base class for ALL 40+ AI agents in the system.
Provides lifecycle management, error recovery, health monitoring,
event bus integration, and inter-agent communication.
"""

import asyncio
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Any, Dict, List, Set
from dataclasses import dataclass, field
from collections import deque
import structlog

from ..core.event_bus import EventBus, EventRecord, EventPriority

logger = structlog.get_logger("aegis.agents")


class AgentState(str, Enum):
    """Agent lifecycle states"""
    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"
    RESTARTING = "restarting"


class AgentPriority(int, Enum):
    """Agent priority levels for execution ordering"""
    CRITICAL = 100    # Observer Director, Event Bus
    HIGH = 80         # Fight Detection, Commentary
    MEDIUM = 60       # Team Agents, Analytics
    LOW = 40          # Highlights, Narrative, Predictions
    BACKGROUND = 20   # Referee, Memory, Scouting


@dataclass
class AgentMetrics:
    """Comprehensive performance metrics for each agent"""
    # Ticks
    total_ticks: int = 0
    successful_ticks: int = 0
    failed_ticks: int = 0
    skipped_ticks: int = 0
    
    # Timing
    avg_tick_time_ms: float = 0.0
    max_tick_time_ms: float = 0.0
    min_tick_time_ms: float = float('inf')
    last_tick_time_ms: float = 0.0
    total_tick_time_ms: float = 0.0
    
    # Events
    total_events_processed: int = 0
    events_processed_last_minute: int = 0
    events_dropped: int = 0
    last_event_time: float = 0.0
    
    # Errors
    total_errors: int = 0
    total_recoveries: int = 0
    consecutive_errors: int = 0
    last_error_time: float = 0.0
    last_error_message: str = ""
    
    # Communication
    messages_sent: int = 0
    messages_received: int = 0
    broadcasts_sent: int = 0
    
    # Decisions
    decisions_made: int = 0
    decisions_overridden: int = 0
    
    # Uptime
    uptime_seconds: float = 0.0
    start_time: float = 0.0
    last_heartbeat: float = 0.0
    
    # Resource
    memory_usage_mb: float = 0.0
    cpu_usage_percent: float = 0.0
    
    def record_tick(self, duration_ms: float, success: bool = True) -> None:
        """Record a tick execution"""
        self.total_ticks += 1
        self.last_tick_time_ms = duration_ms
        
        if success:
            self.successful_ticks += 1
        else:
            self.failed_ticks += 1
        
        # Update timing stats
        self.total_tick_time_ms += duration_ms
        self.avg_tick_time_ms = self.total_tick_time_ms / max(1, self.total_ticks)
        self.max_tick_time_ms = max(self.max_tick_time_ms, duration_ms)
        self.min_tick_time_ms = min(self.min_tick_time_ms, duration_ms)
    
    def record_error(self, error_message: str) -> None:
        """Record an error"""
        self.total_errors += 1
        self.consecutive_errors += 1
        self.last_error_time = time.time()
        self.last_error_message = error_message
    
    def record_recovery(self) -> None:
        """Record a successful recovery"""
        self.total_recoveries += 1
        self.consecutive_errors = 0
    
    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary"""
        return {
            "ticks": {
                "total": self.total_ticks,
                "successful": self.successful_ticks,
                "failed": self.failed_ticks,
                "success_rate": round(
                    self.successful_ticks / max(1, self.total_ticks) * 100, 1
                ),
            },
            "timing": {
                "avg_ms": round(self.avg_tick_time_ms, 2),
                "max_ms": round(self.max_tick_time_ms, 2),
                "min_ms": round(self.min_tick_time_ms, 2) if self.min_tick_time_ms != float('inf') else 0,
                "last_ms": round(self.last_tick_time_ms, 2),
            },
            "events": {
                "total_processed": self.total_events_processed,
                "per_minute": self.events_processed_last_minute,
                "dropped": self.events_dropped,
            },
            "errors": {
                "total": self.total_errors,
                "recoveries": self.total_recoveries,
                "consecutive": self.consecutive_errors,
                "last_error": self.last_error_message[:100] if self.last_error_message else "",
            },
            "communication": {
                "sent": self.messages_sent,
                "received": self.messages_received,
            },
            "decisions": {
                "made": self.decisions_made,
                "overridden": self.decisions_overridden,
            },
            "uptime_seconds": round(self.uptime_seconds, 1),
        }


@dataclass
class AgentConfig:
    """Configuration for each agent instance"""
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent_type: str = "base_agent"
    priority: AgentPriority = AgentPriority.MEDIUM
    
    # Timing
    tick_interval_ms: int = 100          # Main loop interval
    health_check_interval_ms: int = 5000  # Health check frequency
    heartbeat_interval_ms: int = 3000     # Heartbeat frequency
    
    # Error handling
    max_consecutive_errors: int = 5      # Errors before recovery attempt
    max_total_errors_per_hour: int = 50  # Max errors before restart
    error_cooldown_ms: int = 3000        # Cooldown after error
    recovery_timeout_ms: int = 10000     # Max time for recovery
    
    # Queues
    event_queue_size: int = 1000         # Max events in queue
    message_queue_size: int = 500        # Max messages in queue
    
    # Features
    enable_metrics: bool = True
    enable_health_check: bool = True
    enable_heartbeat: bool = True
    enable_auto_recovery: bool = True
    enable_logging: bool = True
    
    # Match context
    match_id: str = ""
    tournament_id: str = ""


class BaseAgent(ABC):
    """
    Abstract base class for ALL AI agents in AEGIS.
    
    Features:
    - Full lifecycle management (create → init → run → pause → stop)
    - Automatic error recovery with circuit breaker
    - Health monitoring with heartbeats
    - Performance metrics collection
    - Event bus integration
    - Inter-agent messaging
    - Graceful degradation
    - Resource monitoring
    
    All 40+ agents inherit from this base class.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        self.state = AgentState.CREATED
        self.metrics = AgentMetrics()
        
        # Runtime components
        self._main_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._message_task: Optional[asyncio.Task] = None
        
        # Event queues
        self._event_queue: asyncio.Queue = asyncio.Queue(
            maxsize=config.event_queue_size
        )
        self._message_queue: asyncio.Queue = asyncio.Queue(
            maxsize=config.message_queue_size
        )
        
        # Control
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
        
        # Error tracking
        self._error_count = 0
        self._error_history: deque = deque(maxlen=20)
        self._circuit_open = False
        self._circuit_open_time = 0.0
        
        # Subscriptions
        self._subscribed_events: Set[str] = set()
        self._subscriber_ids: List[str] = []
        
        # Logging
        self._log = logger.bind(
            agent_id=config.agent_id,
            agent_type=config.agent_type,
        )
        
        self._log.debug("Agent instance created")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LIFECYCLE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start(self) -> None:
        """
        Start the agent lifecycle.
        
        Flow: CREATED → INITIALIZING → RUNNING
        """
        if self.state not in [AgentState.CREATED, AgentState.STOPPED, AgentState.ERROR]:
            self._log.warning(f"Cannot start from state: {self.state.value}")
            return
        
        try:
            self.state = AgentState.INITIALIZING
            self._log.info("Agent starting...")
            
            # Initialize agent-specific resources
            await self.setup()
            
            # Subscribe to events
            await self._register_event_subscriptions()
            
            # Start runtime loops
            self.state = AgentState.RUNNING
            self._stop_event.clear()
            self._pause_event.set()
            self.metrics.start_time = time.time()
            self.metrics.last_heartbeat = time.time()
            
            # Launch tasks
            self._main_task = asyncio.create_task(self._main_loop())
            
            if self.config.enable_health_check:
                self._health_task = asyncio.create_task(self._health_check_loop())
            
            if self.config.enable_heartbeat:
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            self._message_task = asyncio.create_task(self._message_processing_loop())
            
            self._log.info(
                "Agent started successfully",
                tick_interval_ms=self.config.tick_interval_ms,
            )
            
            # Send initial heartbeat
            await self._send_heartbeat()
            
        except Exception as e:
            self.state = AgentState.ERROR
            self._log.error(
                "Failed to start agent",
                error=str(e),
                traceback=traceback.format_exc(),
            )
            raise
    
    async def stop(self) -> None:
        """
        Gracefully stop the agent.
        
        Flow: RUNNING → STOPPING → STOPPED
        """
        self._log.info("Agent stopping...")
        self.state = AgentState.STOPPING
        self._stop_event.set()
        
        # Cancel all tasks
        tasks = [self._main_task, self._health_task, self._heartbeat_task, self._message_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Unsubscribe from events
        await self._unregister_event_subscriptions()
        
        # Cleanup agent resources
        try:
            await self.cleanup()
        except Exception as e:
            self._log.error(f"Cleanup error: {e}")
        
        self.state = AgentState.STOPPED
        self._log.info("Agent stopped")
    
    async def pause(self) -> None:
        """Pause agent execution"""
        if self.state == AgentState.RUNNING:
            self.state = AgentState.PAUSED
            self._pause_event.clear()
            self._log.info("Agent paused")
    
    async def resume(self) -> None:
        """Resume agent execution"""
        if self.state == AgentState.PAUSED:
            self.state = AgentState.RUNNING
            self._pause_event.set()
            self._log.info("Agent resumed")
    
    async def restart(self) -> None:
        """Restart the agent"""
        self._log.info("Agent restarting...")
        self.state = AgentState.RESTARTING
        await self.stop()
        await asyncio.sleep(1)
        await self.start()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ABSTRACT METHODS (Must be implemented by subclasses)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @abstractmethod
    async def setup(self) -> None:
        """
        Initialize agent-specific resources.
        Called once during agent startup.
        
        Examples:
        - Load ML models
        - Initialize data structures
        - Connect to external services
        """
        pass
    
    @abstractmethod
    async def tick(self) -> None:
        """
        Main agent logic executed every tick_interval_ms.
        This is where the agent does its core work.
        
        Examples:
        - TeamAgent: Update player positions, check HP
        - FightDetector: Process signals, detect fights
        - DirectorAI: Decide which feed to show
        """
        pass
    
    @abstractmethod
    async def handle_event(self, event: EventRecord) -> None:
        """
        Handle an incoming event from the event bus.
        
        Args:
            event: The event record to process
            
        Examples:
        - TeamAgent: Handle HP change events
        - CommentaryAgent: Generate commentary for kill events
        """
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """
        Cleanup resources during agent shutdown.
        
        Examples:
        - Close connections
        - Save state
        - Free memory
        """
        pass
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAIN LOOPS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _main_loop(self) -> None:
        """Main agent execution loop with error handling"""
        self._log.debug("Main loop started")
        
        while not self._stop_event.is_set():
            # Wait if paused
            await self._pause_event.wait()
            
            if self.state not in [AgentState.RUNNING, AgentState.DEGRADED]:
                await asyncio.sleep(0.1)
                continue
            
            try:
                tick_start = time.time()
                
                # Process pending events first (priority)
                await self._process_pending_events()
                
                # Execute agent logic
                await self.tick()
                
                # Record metrics
                tick_duration_ms = (time.time() - tick_start) * 1000
                self.metrics.record_tick(tick_duration_ms, success=True)
                
                # Reset error count on success
                if self._error_count > 0:
                    self._error_count = max(0, self._error_count - 0.5)
                
                # Check circuit breaker
                if self._circuit_open:
                    if time.time() - self._circuit_open_time > self.config.error_cooldown_ms / 1000:
                        self._circuit_open = False
                        self._log.info("Circuit breaker closed")
                
                # Calculate sleep time
                elapsed_ms = (time.time() - tick_start) * 1000
                sleep_ms = max(0, self.config.tick_interval_ms - elapsed_ms)
                await asyncio.sleep(sleep_ms / 1000)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._handle_tick_error(e)
    
    async def _process_pending_events(self) -> None:
        """Process events from the event queue"""
        processed = 0
        max_per_tick = 10  # Limit events per tick to prevent blocking
        
        while processed < max_per_tick:
            try:
                event = self._event_queue.get_nowait()
                await self.handle_event(event)
                self.metrics.total_events_processed += 1
                self.metrics.last_event_time = time.time()
                self._event_queue.task_done()
                processed += 1
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                self._log.error(f"Event processing error: {e}")
                self.metrics.events_dropped += 1
    
    async def _health_check_loop(self) -> None:
        """Background health monitoring loop"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.config.health_check_interval_ms / 1000)
                
                if self.state == AgentState.RUNNING:
                    self.metrics.uptime_seconds = time.time() - self.metrics.start_time
                    
                    # Check for stale agent
                    if self.metrics.last_heartbeat > 0:
                        heartbeat_age = time.time() - self.metrics.last_heartbeat
                        if heartbeat_age > self.config.heartbeat_interval_ms * 3 / 1000:
                            self._log.warning(f"No heartbeat for {heartbeat_age:.1f}s")
                    
                    # Check error rate
                    if self.metrics.consecutive_errors >= self.config.max_consecutive_errors:
                        self._log.error("Too many consecutive errors - triggering recovery")
                        await self._attempt_recovery()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Health check error: {e}")
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.config.heartbeat_interval_ms / 1000)
                
                if self.state == AgentState.RUNNING:
                    await self._send_heartbeat()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Heartbeat error: {e}")
    
    async def _message_processing_loop(self) -> None:
        """Process inter-agent messages"""
        while not self._stop_event.is_set():
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0
                )
                
                await self._handle_message(message)
                self.metrics.messages_received += 1
                self._message_queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Message processing error: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def subscribe_to_event(self, event_type: str) -> None:
        """Subscribe to an event type"""
        self._subscribed_events.add(event_type)
    
    async def _register_event_subscriptions(self) -> None:
        """Register all event subscriptions with the event bus"""
        for event_type in self._subscribed_events:
            subscriber_id = f"{self.config.agent_id}_{event_type}"
            
            self.event_bus.subscribe(
                subscriber_id=subscriber_id,
                callback=self._on_event_received,
                event_types=[event_type],
            )
            
            self._subscriber_ids.append(subscriber_id)
        
        self._log.debug(f"Subscribed to {len(self._subscribed_events)} event types")
    
    async def _unregister_event_subscriptions(self) -> None:
        """Unregister all event subscriptions"""
        for subscriber_id in self._subscriber_ids:
            self.event_bus.unsubscribe(subscriber_id)
        
        self._subscriber_ids.clear()
    
    async def _on_event_received(self, record: EventRecord) -> None:
        """Callback when an event is received from the event bus"""
        try:
            # Add to queue for processing
            await self._event_queue.put(record)
        except asyncio.QueueFull:
            self.metrics.events_dropped += 1
            self._log.warning("Event queue full - dropping event")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INTER-AGENT COMMUNICATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def send_message(
        self,
        to_agent: str,
        message_type: str,
        data: Dict[str, Any],
        priority: EventPriority = EventPriority.MEDIUM,
    ) -> None:
        """
        Send a direct message to another agent.
        
        Args:
            to_agent: Target agent ID
            message_type: Type of message
            data: Message payload
            priority: Message priority
        """
        await self.event_bus.publish(
            event_type=f"agent_message:{to_agent}",
            data={
                "from": self.config.agent_id,
                "to": to_agent,
                "type": message_type,
                "data": data,
                "timestamp": time.time(),
            },
            priority=priority,
            source=self.config.agent_id,
            match_id=self.config.match_id,
        )
        self.metrics.messages_sent += 1
    
    async def broadcast_message(
        self,
        message_type: str,
        data: Dict[str, Any],
        agent_type: Optional[str] = None,
        priority: EventPriority = EventPriority.LOW,
    ) -> None:
        """
        Broadcast a message to all agents or a specific type.
        
        Args:
            message_type: Type of message
            data: Message payload
            agent_type: Optional filter for specific agent types
            priority: Message priority
        """
        target = f"agent_broadcast:{agent_type}" if agent_type else "agent_broadcast:all"
        
        await self.event_bus.publish(
            event_type=target,
            data={
                "from": self.config.agent_id,
                "type": message_type,
                "data": data,
                "timestamp": time.time(),
            },
            priority=priority,
            source=self.config.agent_id,
            match_id=self.config.match_id,
        )
        self.metrics.broadcasts_sent += 1
    
    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """
        Handle an incoming inter-agent message.
        Override in subclass for custom message handling.
        """
        pass  # Default: no-op
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ERROR HANDLING & RECOVERY
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _handle_tick_error(self, error: Exception) -> None:
        """Handle errors during tick execution"""
        
        self.metrics.record_error(str(error))
        self._error_count += 1
        self._error_history.append({
            "time": time.time(),
            "error": str(error),
            "traceback": traceback.format_exc()[-200:],
        })
        
        self._log.error(
            f"Tick error (count: {self._error_count:.1f}/{self.config.max_consecutive_errors}): {error}",
            traceback=traceback.format_exc()[-300:],
        )
        
        # Check if circuit breaker should open
        if self._error_count >= self.config.max_consecutive_errors:
            self._circuit_open = True
            self._circuit_open_time = time.time()
            self._log.warning("Circuit breaker OPEN - attempting recovery")
            
            if self.config.enable_auto_recovery:
                await self._attempt_recovery()
        
        # Check if agent should restart
        if len(self._error_history) >= self.config.max_total_errors_per_hour:
            recent_errors = [
                e for e in self._error_history
                if time.time() - e["time"] < 3600
            ]
            if len(recent_errors) >= self.config.max_total_errors_per_hour:
                self._log.error("Too many errors in last hour - restarting agent")
                await self.restart()
                return
        
        # Cooldown
        self.state = AgentState.DEGRADED
        await asyncio.sleep(self.config.error_cooldown_ms / 1000)
        if self.state == AgentState.DEGRADED:
            self.state = AgentState.RUNNING
    
    async def _attempt_recovery(self) -> None:
        """Attempt to recover from errors"""
        self.state = AgentState.RECOVERING
        self._log.info("Attempting recovery...")
        
        try:
            # Cleanup and reinitialize
            try:
                await self.cleanup()
            except Exception:
                pass
            
            await asyncio.sleep(1)
            await self.setup()
            
            self.metrics.record_recovery()
            self._error_count = 0
            self.state = AgentState.DEGRADED
            
            self._log.info("Recovery successful - running in degraded mode")
            
        except Exception as recovery_error:
            self.state = AgentState.ERROR
            self._log.error(
                f"Recovery failed: {recovery_error}",
                traceback=traceback.format_exc(),
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEARTBEAT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _send_heartbeat(self) -> None:
        """Send heartbeat to indicate agent is alive"""
        self.metrics.last_heartbeat = time.time()
        
        await self.event_bus.publish(
            event_type="agent_heartbeat",
            data={
                "agent_id": self.config.agent_id,
                "agent_type": self.config.agent_type,
                "state": self.state.value,
                "priority": self.config.priority.value,
                "metrics": self.metrics.get_summary(),
                "timestamp": time.time(),
            },
            priority=EventPriority.LOW,
            source=self.config.agent_id,
        )
    
    def is_alive(self) -> bool:
        """Check if agent is alive based on heartbeat"""
        if self.metrics.last_heartbeat == 0:
            return self.state == AgentState.RUNNING
        
        heartbeat_age = time.time() - self.metrics.last_heartbeat
        max_age = self.config.heartbeat_interval_ms * 4 / 1000
        
        return (
            self.state in [AgentState.RUNNING, AgentState.DEGRADED]
            and heartbeat_age < max_age
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATUS & DIAGNOSTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status"""
        return {
            "identity": {
                "agent_id": self.config.agent_id,
                "agent_type": self.config.agent_type,
                "priority": self.config.priority.name,
            },
            "state": {
                "current": self.state.value,
                "is_alive": self.is_alive(),
                "uptime_seconds": round(self.metrics.uptime_seconds, 1),
                "circuit_open": self._circuit_open,
            },
            "metrics": self.metrics.get_summary(),
            "configuration": {
                "tick_interval_ms": self.config.tick_interval_ms,
                "match_id": self.config.match_id,
            },
            "subscriptions": {
                "event_types": list(self._subscribed_events),
                "count": len(self._subscribed_events),
            },
        }
    
    def get_health_report(self) -> Dict[str, Any]:
        """Get health report for monitoring"""
        return {
            "agent_id": self.config.agent_id,
            "agent_type": self.config.agent_type,
            "state": self.state.value,
            "is_alive": self.is_alive(),
            "error_count": self._error_count,
            "circuit_open": self._circuit_open,
            "uptime_seconds": round(self.metrics.uptime_seconds, 1),
            "events_queued": self._event_queue.qsize(),
            "messages_queued": self._message_queue.qsize(),
            "last_heartbeat_ago": round(time.time() - self.metrics.last_heartbeat, 1),
            "healthy": (
                self.is_alive()
                and not self._circuit_open
                and self._error_count < self.config.max_consecutive_errors
            ),
        }