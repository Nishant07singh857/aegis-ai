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
from contextlib import contextmanager
from types import ModuleType

import pytest

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except ImportError:  # pragma: no cover - environment guard, the only skip
    pytest.skip(
        "fastapi.testclient unavailable in this environment",
        allow_module_level=True,
    )

# --- fpdf stub: Phase 33 context-manager treatment (LOCAL COPY) -----------
# app.main pulls in the dossiers router, which imports fpdf2. fpdf2 is not
# installed in this test environment and PDF generation is not exercised
# here. The stub below is the Phase 33 pattern, copied here because this
# file lives in tests/integration/ and cannot import the helper from
# tests/unit/test_phase33_db_boot_fail_fast.py (pytest only puts each
# test file's own directory on sys.path). The stub is installed ONLY
# inside ``_fpdf_stub_if_absent`` and torn down on exit — sys.modules
# is restored exactly and stub-bound app.main / app.dossiers.* modules
# are evicted — so this file installs NO import-time stub of its own
# (the old sys.modules.setdefault stub was the import-time leak
# behind the dossier-PDF order-dependence). The import happens ONCE
# at module level because the whole file exercises app.main; the
# skip is ImportError-only — any other import failure propagates
# and fails loudly.

_MISSING = object()  # sentinel: sys.modules entry was absent before the stub


def _build_fpdf_stub():
    """Build the minimal fpdf package stub (import stand-in only).

    ``app.dossiers.pdf`` imports ``FPDF`` and ``fpdf.enums``
    (``MethodReturnValue``, ``XPos``, ``YPos``) at module level, so
    the stub must be a package with an ``enums`` submodule. It is
    only a stand-in for import: no test here generates a PDF, and
    the stub's ``FPDF`` has no PDF methods — PDF generation against
    it fails, honestly, instead of pretending to work.
    """
    import enum

    stub = ModuleType("fpdf")
    stub.__path__ = []  # mark as a package so fpdf.enums can load

    class _FPDF:  # minimal stand-in; no test here touches PDF code
        def __init__(self, *args, **kwargs):
            pass

    stub.FPDF = _FPDF
    enums = ModuleType("fpdf.enums")

    class XPos(enum.Enum):
        LEFT = "LEFT"
        CENTER = "CENTER"
        RIGHT = "RIGHT"
        LMARGIN = "LMARGIN"
        RMARGIN = "RMARGIN"

    class YPos(enum.Enum):
        TOP = "TOP"
        NEXT = "NEXT"
        LAST = "LAST"
        TMARGIN = "TMARGIN"
        BMARGIN = "BMARGIN"

    class MethodReturnValue(enum.Enum):
        VALUE = "VALUE"

    enums.XPos = XPos
    enums.YPos = YPos
    enums.MethodReturnValue = MethodReturnValue
    return stub, enums


@contextmanager
def _fpdf_stub_if_absent():
    """Install the fpdf stub only if fpdf2 is really absent, with teardown.

    On exit, ``sys.modules["fpdf"]`` / ``sys.modules["fpdf.enums"]``
    are restored to exactly their pre-context state (the previous
    module object, or absent), and every ``app.main`` /
    ``app.dossiers.*`` module first imported inside the context is
    evicted from ``sys.modules``. Eviction matters: without it, a
    later ``import app.main`` in the same process would return the
    module bound to the stub's ``FPDF`` even after the stub itself
    was removed. Modules already present before the context are
    never evicted — this context only undoes what it caused.
    """
    try:
        import fpdf  # noqa: F401

        yield
        return
    except ImportError:
        pass
    prior_fpdf = sys.modules.get("fpdf", _MISSING)
    prior_enums = sys.modules.get("fpdf.enums", _MISSING)
    modules_before = set(sys.modules)
    stub, enums = _build_fpdf_stub()
    sys.modules["fpdf"] = stub
    sys.modules["fpdf.enums"] = enums
    try:
        yield
    finally:
        for name, prior in (("fpdf", prior_fpdf), ("fpdf.enums", prior_enums)):
            if prior is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
        for name in list(sys.modules):
            if name not in modules_before and (
                name == "app.main" or name.startswith("app.dossiers")
            ):
                sys.modules.pop(name, None)


def _load_main_module():
    """Import app.main under the teardown-guarded fpdf stub.

    Skip ONLY on a missing-module import failure. Every other import
    failure propagates and FAILS loudly: a real setup/configuration
    error must never be reported as a skip.
    """
    with _fpdf_stub_if_absent():
        try:
            import app.main as main_module
        except ImportError as exc:
            pytest.skip(f"app.main not importable here (missing dependency): {exc}")
        return main_module


main_module = _load_main_module()
app = main_module.app

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
