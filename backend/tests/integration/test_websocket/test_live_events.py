# backend/tests/integration/test_websocket/test_live_events.py
"""
Integration tests for the REAL WebSocket endpoints (H29 rewrite).

History: the previous version wrapped all 9 test bodies in
``try/except Exception -> pytest.skip``, so a failure could never go red —
the suite reported "skipped" no matter how broken the socket layer was.
It also imported ``httpx_ws`` (not installed in this environment, which is
why the file died at collection) and dialled routes that do not exist
(``/ws/live/{match_id}``, ``/ws/admin/monitor``).

What actually exists in app.main (verified by reading the routes and by
connecting): exactly two sockets, ``/ws/live`` and ``/ws/monitor``, both
served by the same ConnectionManager. On connect the server sends a
``{"type": "connected", "data": {"status": "ok"}}`` handshake followed by
state frames (standings, kill_feed_init, director_state, ..., scrim_state);
a literal ``ping`` text frame is answered with ``pong``. There are no
per-match rooms.

These tests use Starlette's TestClient (no extra dependency, no lifespan —
the socket handlers do not need it), assert the real frames, and contain
NO except-skip anywhere: if the socket layer breaks, they fail. The only
skip in the file is the module-level guard below, for the one genuinely
environmental case (TestClient unavailable).
"""

import sys
import types
from enum import Enum

import pytest

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except ImportError:  # pragma: no cover - environment guard, the only skip
    pytest.skip(
        "fastapi.testclient unavailable in this environment",
        allow_module_level=True,
    )

# --- fpdf stub (same pattern as tests/unit/test_main_reliability.py) ------
# app.main pulls in the dossiers router, which imports fpdf2. fpdf2 is not
# installed in this test environment and PDF generation is not exercised
# here, so a minimal stub is installed before importing app.main. This
# stubs an optional dependency, not product behaviour.
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

from app.main import app  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.websocket]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _connect_and_handshake(client, path):
    """Open a socket and assert the server's real first frame."""
    ws_ctx = client.websocket_connect(path)
    ws = ws_ctx.__enter__()
    first = ws.receive_json()
    assert first.get("type") == "connected", f"unexpected first frame: {first}"
    assert first.get("data", {}).get("status") == "ok"
    return ws_ctx, ws


def _drain_until(ws, last_type, cap=40):
    """Read state frames until `last_type` arrives; return the type list."""
    seen = []
    for _ in range(cap):
        frame = ws.receive_json()
        seen.append(frame.get("type"))
        if frame.get("type") == last_type:
            return seen
    raise AssertionError(f"{last_type!r} not seen within {cap} frames: {seen}")


class TestLiveEventsWebSocket:
    """Real /ws/live + /ws/monitor behaviour — failures are failures."""

    def test_websocket_connection(self, client):
        ws_ctx, _ws = _connect_and_handshake(client, "/ws/live")
        ws_ctx.__exit__(None, None, None)

    def test_websocket_connection_with_client_type(self, client):
        # The server accepts (and ignores) a client_type query param; the
        # old test's /ws/live/{match_id} path does not exist — see
        # test_match_room_route_does_not_exist below.
        ws_ctx, _ws = _connect_and_handshake(
            client, "/ws/live?client_type=dashboard"
        )
        ws_ctx.__exit__(None, None, None)

    def test_websocket_multiple_clients(self, client):
        ctxs = []
        try:
            for _ in range(3):
                ws_ctx, _ws = _connect_and_handshake(client, "/ws/live")
                ctxs.append(ws_ctx)
            assert len(ctxs) == 3  # all three handshakes asserted above
        finally:
            for ws_ctx in ctxs:
                ws_ctx.__exit__(None, None, None)

    def test_receive_initial_state(self, client):
        ws_ctx, ws = _connect_and_handshake(client, "/ws/live")
        try:
            # The frame right after the handshake is the standings frame;
            # with DEMO_MODE off (the default) it is an honest empty list.
            standings = ws.receive_json()
            assert standings.get("type") == "standings"
            assert standings.get("data") == []
            rest = _drain_until(ws, "scrim_state")
            assert "kill_feed_init" in rest
            assert "director_state" in rest
        finally:
            ws_ctx.__exit__(None, None, None)

    def test_ping_pong(self, client):
        # There is no periodic heartbeat frame; the real liveness contract
        # is: text "ping" -> text "pong" (after the connect frames).
        ws_ctx, ws = _connect_and_handshake(client, "/ws/live")
        try:
            _drain_until(ws, "scrim_state")
            ws.send_text("ping")
            assert ws.receive_text() == "pong"
        finally:
            ws_ctx.__exit__(None, None, None)

    def test_graceful_disconnect(self, client):
        ws_ctx, ws = _connect_and_handshake(client, "/ws/live")
        ws.close(code=1000, reason="Test complete")
        ws_ctx.__exit__(None, None, None)
        # The manager must accept a fresh connection afterwards.
        ws_ctx2, _ws2 = _connect_and_handshake(client, "/ws/live")
        ws_ctx2.__exit__(None, None, None)

    def test_reconnect(self, client):
        ws_ctx, ws = _connect_and_handshake(client, "/ws/live")
        ws.close()
        ws_ctx.__exit__(None, None, None)

        ws_ctx2, _ws2 = _connect_and_handshake(client, "/ws/live")
        ws_ctx2.__exit__(None, None, None)

    def test_match_room_route_does_not_exist(self, client):
        # The old suite's premise — per-match rooms at /ws/live/{id} — is
        # not a route. Connecting must be refused, never silently pass.
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/live/match_001") as ws:
                ws.receive_json()

    def test_admin_monitor_route_does_not_exist(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/admin/monitor") as ws:
                ws.receive_json()

    def test_monitor_socket_handshake(self, client):
        # /ws/monitor is the second (and only other) real socket.
        ws_ctx, _ws = _connect_and_handshake(client, "/ws/monitor")
        ws_ctx.__exit__(None, None, None)
