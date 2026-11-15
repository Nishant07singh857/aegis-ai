# backend/tests/unit/test_phase38_w1_c1_redis_fallback.py
"""Phase 38 (W1) — C-1 real in-memory EventBus fallback + M-3 scrub.

C-1 (CRITICAL): with Redis down, ``EventBus.connect()`` used to raise
after 5 retries while the lifespan logged "continuing with in-memory
broadcasting" — a lie: no in-memory mode existed and every spine
service (vision, LiveDirector, monitor, pilot, ...) stayed ``None``
while the app reported ready. The fix is a REAL in-memory fallback:
publish/subscribe/dispatch keep working in-process (delivery was
always the in-process dispatch — no Redis pub/sub consumer exists in
this codebase; Streams are persistence/replay only), health reports
``mode="memory" / status="degraded"``, the system status endpoint
reports ``live_spine="degraded"``, and the health loop probes for
Redis recovery and upgrades back when it returns.

M-3: the 4 raw ``exc_info=True`` sites in event_bus.py (connect /
publish / subscriber dispatch / queue processing) now log a scrubbed
message AND a scrubbed exc_info surrogate (Phase 37 pattern), so a DB
exception embedding DATABASE_URL can never reach logs via either
channel. A 5th site (``_persist_event``'s raw ``str(e)``) is covered
too — same credential channel, same file.

Run: python -m pytest tests/unit/test_phase38_w1_c1_redis_fallback.py \
         -q -o addopts="" -p no:cacheprovider
(from backend/; -o addopts="" because pytest-cov is not installed here)

The Redis-down lifespan test (Section D) does NOT shrink the connect
backoff — it measures the real ~15 s retry loop, so it takes ~20-30 s.
"""

from __future__ import annotations

import asyncio
import ast
import sys
import time
import traceback
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.event_bus import EventBus, EventPriority, BaseEvent, EventRecord

# NOTE: ``import app.core.event_bus as event_bus_module`` does NOT give
# the module — app/core/__init__.py binds the name ``event_bus`` to the
# global EventBus() INSTANCE, shadowing the submodule attribute. The
# real module object lives in sys.modules (the from-import above loads
# it there).
event_bus_module = sys.modules["app.core.event_bus"]


# ── Shared fixtures / helpers ──────────────────────────────────────

_SECRET_URL = "postgresql://dbuser:S3cret-Passw0rd@db.internal:5432/aegis"
_SECRET_USER = "dbuser"
_SECRET_PASS = "S3cret-Passw0rd"


def _raise_from_url(cls, url, **kwargs):
    """redis.asyncio.Redis.from_url replacement: Redis is DOWN."""
    raise RedisConnectionError("Connection refused (test: Redis down)")


class _FakeRedis:
    """Minimal async Redis client stand-in for the Redis-UP path."""

    def __init__(self):
        self.ping_calls = 0
        self.xadd_calls = []
        self.publish_calls = []
        self.closed = 0

    async def ping(self):
        self.ping_calls += 1
        return True

    async def xadd(self, stream, fields, maxlen=None, approximate=None):
        self.xadd_calls.append((stream, fields))
        return "fake-stream-id"

    async def publish(self, channel, message):
        self.publish_calls.append((channel, message))
        return 1

    async def close(self):
        self.closed += 1


class _CaptureLogger:
    """Records structlog-style log calls for scrub assertions.

    ``exc_info`` tuples are checked by formatting them exactly as a
    log handler would (``traceback.format_exception``) — with the old
    ``exc_info=True`` code that text contains the raw exception
    message, so a secret in the formatted traceback fails the test.
    """

    def __init__(self):
        self.records = []  # (level, msg, kwargs)

    def _record(self, level, msg, **kwargs):
        self.records.append((level, str(msg), kwargs))

    def debug(self, msg, **kwargs):
        self._record("debug", msg, **kwargs)

    def info(self, msg, **kwargs):
        self._record("info", msg, **kwargs)

    def warning(self, msg, **kwargs):
        self._record("warning", msg, **kwargs)

    def error(self, msg, **kwargs):
        self._record("error", msg, **kwargs)

    def formatted_text(self) -> str:
        """Everything a log handler could print from these records.

        structlog renders bound kwargs as key=value pairs on the log
        line, so they are included here (a scrubbed value carried in
        a kwarg — e.g. ``error=`` in _persist_event — must be checked
        exactly like the message text).
        """
        chunks = []
        for _level, msg, kwargs in self.records:
            chunks.append(msg)
            for key, value in kwargs.items():
                if key == "exc_info":
                    chunks.append(
                        "".join(traceback.format_exception(*value))
                    )
                else:
                    chunks.append(f"{key}={value}")
        return "\n".join(chunks)


def _assert_no_secret(captured: _CaptureLogger) -> None:
    text = captured.formatted_text()
    assert _SECRET_PASS not in text, "password leaked into logs"
    assert _SECRET_USER not in text, "db user leaked into logs"
    assert "<redacted>" in text, "expected a scrubbed marker in logs"


@pytest.fixture
def no_retry_delay(monkeypatch):
    """Shrink the connect backoff to 0 (unit tests only).

    The lifespan test deliberately does NOT use this — it measures
    the real 15 s backoff.
    """
    monkeypatch.setattr(event_bus_module, "CONNECT_RETRY_DELAY_S", 0.0)


@pytest.fixture
def redis_down(monkeypatch, no_retry_delay):
    """Every redis.asyncio.Redis.from_url raises ConnectionError."""
    import redis.asyncio as aioredis

    monkeypatch.setattr(
        aioredis.Redis, "from_url", classmethod(_raise_from_url)
    )


@pytest.fixture
def secret_db_url(monkeypatch):
    """DATABASE_URL carries a credential, so the scrubber's verbatim
    known-secret path is exercised (not just its URL patterns)."""
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", _SECRET_URL)


@pytest.fixture
def capture_logs(monkeypatch):
    captured = _CaptureLogger()
    monkeypatch.setattr(event_bus_module, "logger", captured)
    return captured


async def _wait_until(predicate, timeout_s: float = 5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


# ── Section A — C-1: the real in-memory fallback ───────────────────


@pytest.mark.unit
class TestMemoryFallback:
    async def test_connect_redis_down_enters_memory_mode(self, redis_down):
        bus = EventBus()
        try:
            # OLD behaviour: raised ConnectionError after 5 retries.
            # NEW behaviour: returns, bus running in-memory.
            await bus.connect()
            assert bus._running is True
            assert bus._transport == "memory"
            # Dead clients are discarded, never half-used.
            assert bus._redis is None
            assert bus._pubsub_redis is None
            assert bus._consumer_redis is None

            health = await bus.health_check()
            assert health["status"] == "degraded"
            assert health["mode"] == "memory"
            assert health["redis_connected"] is False
            assert health["running"] is True
            assert bus.get_stats()["mode"] == "memory"
        finally:
            await bus.disconnect()

    async def test_memory_mode_publish_reaches_subscribers(
        self, redis_down
    ):
        bus = EventBus()
        try:
            await bus.connect()
            received = []

            async def _handler(record):
                received.append(record)

            bus.subscribe(
                subscriber_id="test_sub",
                callback=_handler,
                event_types=["kill"],
            )

            event_id = await bus.publish(
                event_type="kill",
                data={"killer_id": "p1", "victim_id": "p2"},
                priority=EventPriority.MEDIUM,
                match_id="m1",
            )
            assert event_id is not None
            assert len(received) == 1
            assert received[0].event.event_type == "kill"
            assert received[0].event.data["killer_id"] == "p1"

            # History + stats work without Redis too.
            history = await bus.get_match_events("m1")
            assert len(history) == 1
            assert history[0]["event_type"] == "kill"
            assert bus._stats["events_published"] == 1
            assert bus._stats["events_dropped"] == 0

            # A HIGH-priority event also flows through the background
            # processing queue in memory mode (same loop, same DB
            # persistence path as on Redis).
            await bus.publish(
                event_type="kill",
                data={"killer_id": "p3", "victim_id": "p4"},
                priority=EventPriority.HIGH,
                match_id="m1",
            )
            processed = await _wait_until(
                lambda: bus._stats["events_processed"] >= 1
            )
            assert processed, "processing queue never ran in memory mode"
            assert len(received) == 2
        finally:
            await bus.disconnect()

    async def test_memory_mode_publish_batch(self, redis_down):
        bus = EventBus()
        try:
            await bus.connect()
            received = []

            async def _handler(record):
                received.append(record.event.event_type)

            bus.subscribe(
                subscriber_id="batch_sub",
                callback=_handler,
                event_types=["kill", "knock"],
            )
            count = await bus.publish_batch(
                [("kill", {"a": 1}), ("knock", {"b": 2}), ("kill", {"c": 3})],
                match_id="m1",
            )
            assert count == 3
            assert received == ["kill", "knock", "kill"]
        finally:
            await bus.disconnect()

    async def test_publish_batch_when_not_running_returns_zero(self):
        bus = EventBus()
        assert bus._running is False
        assert await bus.publish_batch([("kill", {})]) == 0

    async def test_redis_recovery_upgrades_memory_mode(
        self, redis_down, monkeypatch
    ):
        bus = EventBus()
        try:
            await bus.connect()
            assert bus._transport == "memory"

            fake = _FakeRedis()
            import redis.asyncio as aioredis

            monkeypatch.setattr(
                aioredis.Redis,
                "from_url",
                classmethod(lambda cls, url, **kw: fake),
            )
            upgraded = await bus._try_redis_reconnect()
            assert upgraded is True
            assert bus._transport == "redis"

            # Publishing now goes through Redis again.
            event_id = await bus.publish(
                event_type="kill", data={}, match_id="m1"
            )
            assert event_id is not None
            assert len(fake.xadd_calls) == 1
            assert len(fake.publish_calls) == 1
        finally:
            await bus.disconnect()

    async def test_reconnect_does_not_duplicate_background_tasks(
        self, redis_down
    ):
        bus = EventBus()
        try:
            await bus.connect()
            assert len(bus._listener_tasks) == 2
            assert bus._health_check_task is not None
            # Second connect (health-loop reconnect shape): tasks
            # must NOT be duplicated — the old code appended a fresh
            # set on every connect().
            await bus.connect()
            assert len(bus._listener_tasks) == 2
            assert bus._background_tasks_started is True
        finally:
            await bus.disconnect()

    async def test_disconnect_resets_for_clean_reconnect(
        self, redis_down, monkeypatch
    ):
        bus = EventBus()
        await bus.connect()
        assert bus._transport == "memory"
        await bus.disconnect()
        assert bus._running is False
        assert bus._transport == "redis"
        assert bus._listener_tasks == []
        assert bus._health_check_task is None

        fake = _FakeRedis()
        import redis.asyncio as aioredis

        monkeypatch.setattr(
            aioredis.Redis,
            "from_url",
            classmethod(lambda cls, url, **kw: fake),
        )
        await bus.connect()
        try:
            assert bus._running is True
            assert bus._transport == "redis"
            assert len(bus._listener_tasks) == 2
        finally:
            await bus.disconnect()


# ── Section B — M-3: scrubbed error logging ────────────────────────


@pytest.mark.unit
class TestScrubbedErrorLogging:
    async def test_connect_unexpected_error_is_scrubbed(
        self, monkeypatch, no_retry_delay, secret_db_url, capture_logs
    ):
        import redis.asyncio as aioredis

        def _boom(cls, url, **kwargs):
            raise RuntimeError(
                f"could not parse DATABASE_URL={_SECRET_URL} (bad DSN)"
            )

        monkeypatch.setattr(aioredis.Redis, "from_url", classmethod(_boom))
        bus = EventBus()
        try:
            # Even an unexpected connect error falls back to memory
            # mode — and the log must not carry the raw URL.
            await bus.connect()
            assert bus._transport == "memory"
            _assert_no_secret(capture_logs)
        finally:
            await bus.disconnect()

    async def test_publish_unexpected_error_is_scrubbed(
        self, secret_db_url, capture_logs
    ):
        from unittest.mock import AsyncMock

        bus = EventBus()
        bus._running = True
        bus._transport = "redis"
        bus._redis = AsyncMock()
        bus._redis.xadd = AsyncMock(
            side_effect=RuntimeError(
                f"driver blew up on DATABASE_URL={_SECRET_URL}"
            )
        )
        bus._pubsub_redis = AsyncMock()
        try:
            result = await bus.publish(
                event_type="kill", data={}, match_id="m1"
            )
            assert result is None
            assert bus._stats["events_failed"] == 1
            _assert_no_secret(capture_logs)
        finally:
            await bus.disconnect()

    async def test_subscriber_db_error_is_scrubbed(
        self, redis_down, secret_db_url, capture_logs
    ):
        bus = EventBus()
        try:
            await bus.connect()

            async def _boom_subscriber(record):
                raise RuntimeError(
                    f"asyncpg failed for DATABASE_URL={_SECRET_URL}"
                )

            bus.subscribe(
                subscriber_id="boom_sub",
                callback=_boom_subscriber,
                event_types=["kill"],
            )
            # A failing subscriber must not break publishing, and its
            # DB error must not reach the log raw.
            event_id = await bus.publish(
                event_type="kill", data={}, match_id="m1"
            )
            assert event_id is not None
            _assert_no_secret(capture_logs)
        finally:
            await bus.disconnect()

    async def test_event_processing_error_is_scrubbed(
        self, redis_down, secret_db_url, capture_logs
    ):
        from unittest.mock import AsyncMock

        bus = EventBus()
        try:
            await bus.connect()
            bus._process_event = AsyncMock(
                side_effect=RuntimeError(
                    f"persist failed: DATABASE_URL={_SECRET_URL}"
                )
            )
            record = EventRecord(
                event=BaseEvent(event_type="kill", data={})
            )
            bus._enqueue_for_processing(EventPriority.HIGH.value, record)
            # The background loop picks the record up and hits its
            # own except clause (the :699 site).
            seen = await _wait_until(
                lambda: any(
                    level == "error" and "processing error" in msg
                    for level, msg, _kw in capture_logs.records
                )
            )
            assert seen, "queue loop never logged the processing error"
            _assert_no_secret(capture_logs)
        finally:
            await bus.disconnect()

    async def test_persist_event_error_is_scrubbed(
        self, secret_db_url, capture_logs
    ):
        """5th site (beyond the brief's 4): _persist_event logged
        the raw DB error string — a DB error by construction, so it
        can embed DATABASE_URL. Now scrubbed."""
        bus = EventBus()

        def _raising_factory():
            raise RuntimeError(
                f"connection failed: DATABASE_URL={_SECRET_URL}"
            )

        bus._session_factory = _raising_factory
        await bus._persist_event(BaseEvent(event_type="kill", data={}))
        _assert_no_secret(capture_logs)


# ── Section C — the false log line + status payload ────────────────


@pytest.mark.unit
class TestStatusReflectsSpine:
    def test_false_in_memory_log_line_is_gone(self):
        backend = Path(__file__).resolve().parents[2]
        main_src = (backend / "app" / "main.py").read_text()
        assert "continuing with in-memory broadcasting" not in main_src
        assert "EventBus failed to start" in main_src
        # AST-level check (docstrings legitimately MENTION exc_info):
        # no CALL anywhere in event_bus.py passes a raw exc_info=True.
        bus_src = (backend / "app" / "core" / "event_bus.py").read_text()
        tree = ast.parse(bus_src)
        raw_sites = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "exc_info"
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ]
        assert raw_sites == [], f"raw exc_info=True calls remain: {raw_sites}"
        scrubbed_sites = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "exc_info"
        ]
        assert len(scrubbed_sites) == 4, scrubbed_sites

    def test_system_status_payload_reflects_bus_mode(self, monkeypatch):
        # app.main needs fpdf2 (absent here) — same teardown-guarded
        # stub context as test_main_reliability.py.
        from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent

        with _fpdf_stub_if_absent():
            try:
                import app.main as main_module
            except ImportError as exc:
                pytest.skip(f"app.main not importable here: {exc}")
            bus = main_module.event_bus
            cases = [
                # (running, transport, want event_bus, want live_spine)
                (True, "redis", "connected", "ready"),
                (True, "memory", "memory", "degraded"),
                (False, "redis", "disconnected", "down"),
            ]
            for running, transport, want_bus, want_spine in cases:
                monkeypatch.setattr(bus, "_running", running)
                monkeypatch.setattr(bus, "_transport", transport)
                payload = main_module._system_status_payload()
                assert payload["event_bus"] == want_bus
                assert payload["live_spine"] == want_spine
                # Process liveness stays "online" — it is NOT the
                # spine claim; live_spine carries that.
                assert payload["status"] == "online"


# ── Section D — Redis-down boot of the REAL app ────────────────────


@pytest.mark.unit
class TestRedisDownLifespanBoot:
    def test_redis_down_boot_spine_works_and_status_degrades(
        self, monkeypatch
    ):
        """The C-1 proof, end to end, with the REAL retry loop
        (NOT shrunk — the ~15 s backoff is part of the evidence).

        Boots the full app with Redis down and proves:
        * the app boots (no raise) and the bus is running IN-MEMORY;
        * every spine service the old code left None is now built
          (live director, vision intelligence, monitor, pilot,
          outcome labeler, rotation bridge);
        * GET /api/v1/system/status reports live_spine "degraded"
          and event_bus "memory" (never "ready"/"connected");
        * publish -> subscriber delivery works in the booted app.
        """
        import redis.asyncio as aioredis

        monkeypatch.setattr(
            aioredis.Redis, "from_url", classmethod(_raise_from_url)
        )

        from test_phase33_db_boot_fail_fast import _fpdf_stub_if_absent

        with _fpdf_stub_if_absent():
            try:
                import app.main as main_module
            except ImportError as exc:
                pytest.skip(f"app.main not importable here: {exc}")

            from fastapi.testclient import TestClient

            app = main_module.app
            t0 = time.monotonic()
            with TestClient(app) as client:
                boot_s = time.monotonic() - t0
                print(f"\nREDIS-DOWN BOOT: {boot_s:.1f}s")
                assert boot_s < 90, f"boot took {boot_s:.1f}s"

                bus = main_module.event_bus
                assert bus._running is True
                assert bus._transport == "memory"

                # The old code left ALL of these None when Redis was
                # down; the fallback wires them up.
                assert getattr(app.state, "live_director", None) is not None
                assert (
                    getattr(app.state, "vision_intelligence", None)
                    is not None
                )
                assert (
                    getattr(app.state, "multi_feed_monitor", None)
                    is not None
                )
                assert getattr(app.state, "observer_pilot", None) is not None
                assert getattr(app.state, "outcome_labeler", None) is not None
                assert (
                    getattr(app.state, "rotation_intake_bridge", None)
                    is not None
                )

                resp = client.get("/api/v1/system/status")
                assert resp.status_code == 200
                payload = resp.json()
                assert payload["event_bus"] == "memory"
                assert payload["live_spine"] == "degraded"

                health = asyncio.run(bus.health_check())
                assert health["status"] == "degraded"
                assert health["mode"] == "memory"

                # Spine end to end in the booted app: publish a kill
                # and watch a subscriber receive it (MEDIUM priority
                # stays out of the processing queue — pure dispatch).
                received = []

                async def _handler(record):
                    received.append(record.event.event_type)

                bus.subscribe(
                    subscriber_id="phase38_c1_probe",
                    callback=_handler,
                    event_types=["kill"],
                )
                try:
                    event_id = asyncio.run(
                        bus.publish(
                            event_type="kill",
                            data={"killer_id": "p1", "victim_id": "p2"},
                            match_id="phase38_probe",
                        )
                    )
                    assert event_id is not None
                    assert received == ["kill"]
                finally:
                    bus.unsubscribe("phase38_c1_probe")

            # Shutdown disconnects the bus and resets the transport.
            assert bus._running is False
            assert bus._transport == "redis"
