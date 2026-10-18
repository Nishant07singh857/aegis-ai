# backend/tests/unit/test_main_reliability.py
"""Reliability regression tests for app.main (Phase 12, W1).

Covers:
  * Bounded in-memory buffers: kill_feed / commentary_log / agent_logs /
    render_jobs in MATCH_STATE are collections.deque with fixed maxlen, so a
    long-running server can never OOM on these buffers. Every reader
    serializes via list(...) so JSON encoding is unchanged.
  * Concurrent WS fan-out: ConnectionManager.broadcast sends to all clients
    concurrently with a 2s per-client timeout. One slow client can neither
    head-of-line block the rest nor linger as a zombie connection — it gets
    disconnected while the fast clients receive the message on time.

No network, no DB writes, no secrets.

Import note: app.main pulls in the dossiers router, which imports fpdf2.
fpdf2 is not installed in this test environment (and PDF generation is not
exercised here), so a minimal fpdf stub is installed in sys.modules before
the import. This stubs an optional dependency, not product behavior.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from collections import deque
from enum import Enum

import pytest

# --- fpdf stub (see module docstring) -------------------------------------
_fpdf_stub = types.ModuleType("fpdf")
_fpdf_stub.__path__ = []


class _FPDF:
    def __init__(self, *args, **kwargs):
        pass


class _MethodReturnValue(Enum):
    NONE = 0


class _XPos(Enum):
    LEFT = 0
    RIGHT = 1
    LMARGIN = 2


class _YPos(Enum):
    TOP = 0
    NEXT = 1


_fpdf_stub.FPDF = _FPDF
_enums_stub = types.ModuleType("fpdf.enums")
_enums_stub.MethodReturnValue = _MethodReturnValue
_enums_stub.XPos = _XPos
_enums_stub.YPos = _YPos
sys.modules.setdefault("fpdf", _fpdf_stub)
sys.modules.setdefault("fpdf.enums", _enums_stub)

from app.main import MATCH_STATE, ConnectionManager  # noqa: E402

BOUNDED_BUFFERS = {
    "kill_feed": 500,
    "commentary_log": 500,
    "agent_logs": 500,
    "render_jobs": 200,
}


@pytest.mark.unit
class TestBoundedBuffers:
    """MATCH_STATE hot buffers are bounded deques."""

    @pytest.mark.parametrize("key,maxlen", sorted(BOUNDED_BUFFERS.items()))
    def test_buffer_is_bounded_deque(self, key, maxlen):
        buf = MATCH_STATE[key]
        assert isinstance(buf, deque), f"{key} must be a collections.deque"
        assert buf.maxlen == maxlen, f"{key} maxlen must be {maxlen}"

    @pytest.mark.parametrize("key,maxlen", sorted(BOUNDED_BUFFERS.items()))
    def test_buffer_never_exceeds_maxlen(self, key, maxlen):
        buf = MATCH_STATE[key]
        try:
            for i in range(2000):
                buf.append({"seq": i})
            assert len(buf) <= maxlen
            assert len(buf) == maxlen
            # Oldest evicted, newest retained (ring-buffer semantics).
            assert buf[0] == {"seq": 2000 - maxlen}
            assert buf[-1] == {"seq": 1999}
        finally:
            buf.clear()

    @pytest.mark.parametrize("key", sorted(BOUNDED_BUFFERS))
    def test_buffer_serializes_to_json(self, key):
        buf = MATCH_STATE[key]
        try:
            buf.append({"ping": "pong"})
            # This is exactly what every WS/REST reader does with the deque.
            payload = json.dumps({"data": list(buf)})
            assert '"ping": "pong"' in payload
        finally:
            buf.clear()


class _FakeWebSocket:
    """Minimal stand-in for a FastAPI WebSocket.

    send_delay simulates a slow/stalled client; raise_on_send simulates a
    dead socket.
    """

    def __init__(self, send_delay: float = 0.0, raise_on_send: bool = False):
        self.send_delay = send_delay
        self.raise_on_send = raise_on_send
        self.received: list = []

    async def send_json(self, message: dict):
        if self.raise_on_send:
            raise ConnectionResetError("dead client")
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.received.append(message)


@pytest.mark.unit
class TestConcurrentFanout:
    """broadcast() is concurrent: a slow client can't stall the rest."""

    async def test_slow_client_does_not_block_fast_clients(self):
        manager = ConnectionManager()
        slow = _FakeWebSocket(send_delay=5.0)  # would stall a sequential loop
        fast1 = _FakeWebSocket()
        fast2 = _FakeWebSocket()
        manager.active_connections.extend([slow, fast1, fast2])

        message = {"type": "test", "data": {"n": 1}}
        started = time.monotonic()
        await manager.broadcast(message)
        elapsed = time.monotonic() - started

        # Well under the 5s a sequential send to the slow client would take.
        assert elapsed < 4.0, f"broadcast took {elapsed:.2f}s — head-of-line blocking?"
        # Both fast clients got the message, on time.
        assert fast1.received == [message]
        assert fast2.received == [message]
        # The slow client timed out (2s per-client timeout) and was dropped.
        assert slow not in manager.active_connections
        assert fast1 in manager.active_connections
        assert fast2 in manager.active_connections

    async def test_dead_client_is_removed_others_still_served(self):
        manager = ConnectionManager()
        dead = _FakeWebSocket(raise_on_send=True)
        healthy = _FakeWebSocket()
        manager.active_connections.extend([dead, healthy])

        await manager.broadcast({"type": "ping"})

        assert healthy.received == [{"type": "ping"}]
        assert dead not in manager.active_connections
        assert healthy in manager.active_connections

    async def test_broadcast_signature_unchanged(self):
        import inspect

        sig = inspect.signature(ConnectionManager.broadcast)
        assert list(sig.parameters) == ["self", "message"]
