# backend/tests/unit/test_integrity_operator_state.py
"""Integrity Fix Pack — Worker C1 (C6 backend + H4).

Contract under test (shared with the frontend worker):
  * DEMO_MODE OFF: fabricated operator state (DEVICES, agents_state,
    system_health, social_config, training_models, seeded render_jobs)
    is NEVER served — not in the WS connect payload, not via REST.
  * DEMO_MODE ON: those blocks may be served, but every such payload
    block carries "source": "demo".
  * WS action acks carry "status": "success" ONLY when the action
    actually happened; otherwise "status": "error" + a reason.
  * revoke_key NEVER broadcasts a fabricated "sk_live_…" key.

No network, no DB writes, no secrets. app.main is imported with the
same minimal fpdf stub as the other main-importing suites (fpdf2 is
not installed in this environment and PDF generation is not exercised).
"""

from __future__ import annotations

import json
import sys
import types
from contextlib import asynccontextmanager
from enum import Enum
from unittest.mock import AsyncMock, patch

import pytest

# --- fpdf stub (see module docstring) --------------------------------------
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

import app.main as main_module  # noqa: E402
from app.api.deps import ADMIN_PRINCIPAL  # noqa: E402

FABRICATED_FRAME_TYPES = {
    "system_health",
    "agents_state",
    "devices_state",
    "agent_assignments",
    "render_jobs",
    "social_config",
    "training_models",
}

FABRICATED_MARKERS = [
    "RTX 4090",
    "Node-GPU-1",
    "YOLOv8 Vision Agent",
    "events_per_sec",
    "24500",
]


def _body(resp) -> dict:
    return json.loads(resp.body.decode("utf-8"))


class _FakeWebSocket:
    def __init__(self):
        self.frames = []

    async def accept(self):
        pass

    async def send_json(self, payload):
        self.frames.append(payload)


class _FakeManager:
    """Captures manager.broadcast calls made by handle_action."""

    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, message):
        self.broadcasts.append(message)

    def acks(self):
        return [m["data"] for m in self.broadcasts if m.get("type") == "action_ack"]


@pytest.fixture
def fake_manager(monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(main_module, "manager", fake)
    return fake


async def _connect_frames(demo: bool):
    ws = _FakeWebSocket()
    manager = main_module.ConnectionManager()
    with patch.object(main_module, "DEMO_MODE", demo):
        await manager.connect(ws)
    return ws.frames


# ── DEMO_MODE OFF: nothing fabricated is served ──────────────────────


@pytest.mark.unit
class TestDemoOffServesNothingFabricated:
    async def test_system_status_has_no_fabricated_health(self):
        with patch.object(main_module, "DEMO_MODE", False):
            resp = await main_module.get_system_status()
        assert resp.status_code == 200
        payload = _body(resp)
        assert payload["status"] == "online"
        assert payload["demo_mode"] is False
        assert "modes" in payload  # real operator state, unchanged
        assert "health" not in payload  # fabricated block absent
        text = resp.body.decode()
        for marker in FABRICATED_MARKERS:
            assert marker not in text

    async def test_admin_system_matches_honest_status(self):
        with patch.object(main_module, "DEMO_MODE", False):
            resp = await main_module.get_admin_system(_user=ADMIN_PRINCIPAL)
        payload = _body(resp)
        assert "health" not in payload
        assert payload["demo_mode"] is False

    async def test_agents_devices_assignments_are_empty(self):
        with patch.object(main_module, "DEMO_MODE", False):
            agents = _body(await main_module.get_admin_agents(_user=ADMIN_PRINCIPAL))
            devices = _body(await main_module.get_devices(_user=ADMIN_PRINCIPAL))
            assignments = _body(
                await main_module.get_agent_assignments(_user=ADMIN_PRINCIPAL)
            )
        assert agents["data"] == []
        assert devices["data"] == []
        assert assignments["data"] == {}
        for payload in (agents, devices, assignments):
            assert payload["source"] != "demo"
            assert "detail" in payload  # the emptiness is explained

    async def test_ws_connect_payload_has_no_fabricated_blocks(self):
        frames = await _connect_frames(demo=False)
        types_seen = {f.get("type") for f in frames}
        assert not (types_seen & FABRICATED_FRAME_TYPES), types_seen
        text = json.dumps(frames)
        for marker in FABRICATED_MARKERS:
            assert marker not in text


# ── DEMO_MODE ON: blocks served, every one marked "source": "demo" ──


@pytest.mark.unit
class TestDemoOnMarksEveryBlock:
    async def test_system_status_health_marked_demo(self):
        with patch.object(main_module, "DEMO_MODE", True):
            resp = await main_module.get_system_status()
        payload = _body(resp)
        assert payload["demo_mode"] is True
        assert payload["health"]["source"] == "demo"
        assert "cpu_usage" in payload["health"]

    async def test_agents_and_devices_marked_demo(self):
        with patch.object(main_module, "DEMO_MODE", True):
            agents = _body(await main_module.get_admin_agents(_user=ADMIN_PRINCIPAL))
            devices = _body(await main_module.get_devices(_user=ADMIN_PRINCIPAL))
        assert agents["source"] == "demo"
        assert devices["source"] == "demo"
        assert agents["data"], "demo agents expected in DEMO_MODE"
        assert all(item["source"] == "demo" for item in agents["data"])
        assert all(item["source"] == "demo" for item in devices["data"])

    async def test_ws_connect_frames_marked_demo(self):
        frames = await _connect_frames(demo=True)
        by_type = {f["type"]: f for f in frames}
        for frame_type in FABRICATED_FRAME_TYPES:
            assert frame_type in by_type, frame_type
            frame = by_type[frame_type]
            assert frame["source"] == "demo", frame_type
        # Dict payloads carry the marker inside the block too.
        assert by_type["system_health"]["data"]["source"] == "demo"
        assert by_type["social_config"]["data"]["source"] == "demo"
        # List payloads mark every item.
        assert all(
            item["source"] == "demo" for item in by_type["devices_state"]["data"]
        )
        assert all(
            item["source"] == "demo" for item in by_type["agents_state"]["data"]
        )
        assert all(
            item["source"] == "demo" for item in by_type["training_models"]["data"]
        )


# ── H4: fake-success WS actions are honest now ────────────────────────


@pytest.mark.unit
class TestHonestActionAcks:
    async def test_revoke_key_never_broadcasts_a_fabricated_key(
        self, fake_manager
    ):
        with patch.object(main_module, "DEMO_MODE", False):
            ack = await main_module.handle_action(
                {"action": "revoke_key"}, user=ADMIN_PRINCIPAL
            )
        assert ack["status"] == "error"
        assert ack["message"]  # a reason is given
        text = json.dumps(fake_manager.broadcasts)
        assert "sk_live_" not in text
        assert '"success"' not in text

    @pytest.mark.parametrize(
        "action_data",
        [
            {"action": "test_post", "platform": "YouTube Shorts"},
            {"action": "post_now", "platform": "TikTok"},
            {"action": "upload_sponsor"},
            {"action": "test_webhook"},
            {"action": "toggle_broadcaster_seat", "seat": "1"},
            {"action": "toggle_autopost", "platform": "TikTok"},
            {"action": "stop_training", "model": "x"},
            {"action": "restart_node", "node": "gpu1"},
            {"action": "toggle_node", "node_id": "gpu1"},
            {"action": "assign_agent", "agent_id": "vision", "device_id": "gpu1"},
            {"action": "generate_recap"},
            {"action": "create_tournament"},  # no name -> validation error
            {"action": "totally_unknown_action"},
        ],
    )
    async def test_unavailable_actions_error_instead_of_fake_success(
        self, fake_manager, action_data
    ):
        with patch.object(main_module, "DEMO_MODE", False):
            ack = await main_module.handle_action(
                action_data, user=ADMIN_PRINCIPAL
            )
        assert ack is not None, action_data
        assert ack["status"] == "error", action_data
        assert ack["message"], action_data
        assert fake_manager.acks()[-1]["status"] == "error"

    async def test_clip_highlight_unknown_moment_is_error(self, fake_manager):
        with patch.object(
            main_module.replay_service,
            "request_render",
            side_effect=KeyError("nope"),
        ):
            ack = await main_module.handle_action(
                {"action": "clip_highlight", "clip_id": "missing"},
                user=ADMIN_PRINCIPAL,
            )
        assert ack["status"] == "error"
        assert "missing" in ack["message"]

    async def test_generate_all_highlights_empty_buffer_is_error(
        self, fake_manager
    ):
        with patch.object(
            main_module.replay_service, "get_highlights", return_value=[]
        ):
            ack = await main_module.handle_action(
                {"action": "generate_all_highlights"}, user=ADMIN_PRINCIPAL
            )
        assert ack["status"] == "error"
        assert "No highlights" in ack["message"]

    async def test_generate_all_highlights_real_reel_is_success(
        self, fake_manager
    ):
        fake_highlight = types.SimpleNamespace(moment_id="m1")
        with patch.object(
            main_module.replay_service,
            "get_highlights",
            return_value=[fake_highlight],
        ):
            ack = await main_module.handle_action(
                {"action": "generate_all_highlights"}, user=ADMIN_PRINCIPAL
            )
        assert ack["status"] == "success"
        assert ack["highlight_count"] == 1

    async def test_create_tournament_db_failure_is_honest_error(
        self, fake_manager
    ):
        @asynccontextmanager
        async def _broken_db():
            raise RuntimeError("db unavailable")
            yield  # pragma: no cover

        with patch.object(main_module, "get_db_context", _broken_db):
            ack = await main_module.handle_action(
                {"action": "create_tournament", "name": "Honesty Cup"},
                user=ADMIN_PRINCIPAL,
            )
        assert ack["status"] == "error"
        assert "not created" in ack["message"]

    async def test_create_tournament_wired_to_real_service(self, fake_manager):
        created = types.SimpleNamespace(
            name="Honesty Cup",
            model_dump=lambda mode=None: {"name": "Honesty Cup", "id": "t1"},
        )

        @asynccontextmanager
        async def _fake_db():
            yield object()

        from app.api.v1.tournament import service as tournament_service_module

        with patch.object(main_module, "get_db_context", _fake_db), patch.object(
            tournament_service_module.TournamentService,
            "create_tournament",
            new=AsyncMock(return_value=created),
        ):
            ack = await main_module.handle_action(
                {"action": "create_tournament", "name": "Honesty Cup"},
                user=ADMIN_PRINCIPAL,
            )
        assert ack["status"] == "success"
        assert ack["tournament"]["name"] == "Honesty Cup"

    async def test_demo_toggle_autopost_success_is_real_and_marked(
        self, fake_manager
    ):
        platform = "TikTok"
        original = main_module.MATCH_STATE["social_config"][platform]["auto_post"]
        try:
            with patch.object(main_module, "DEMO_MODE", True):
                ack = await main_module.handle_action(
                    {"action": "toggle_autopost", "platform": platform},
                    user=ADMIN_PRINCIPAL,
                )
            assert ack["status"] == "success"
            assert ack["source"] == "demo"
            assert ack["auto_post"] is (not original)
            assert (
                main_module.MATCH_STATE["social_config"][platform]["auto_post"]
                is (not original)
            )
            social_frames = [
                m for m in fake_manager.broadcasts if m.get("type") == "social_config"
            ]
            assert social_frames and social_frames[-1]["source"] == "demo"
        finally:
            main_module.MATCH_STATE["social_config"][platform][
                "auto_post"
            ] = original


# ── H4 REST: broadcast/replay + highlights ───────────────────────────


class _StubRequest:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.mark.unit
class TestRestReplayAndHighlights:
    async def test_replay_without_marks_is_honest_409(self):
        with patch.object(main_module.replay_service, "list_marks", return_value=[]):
            resp = await main_module.trigger_replay(
                _StubRequest(), _user=ADMIN_PRINCIPAL
            )
        assert resp.status_code == 409
        payload = _body(resp)
        assert payload["status"] == "error"
        assert "No replay available" in payload["message"]

    async def test_replay_plays_latest_real_mark(self):
        clip = types.SimpleNamespace(
            model_dump=lambda: {"event_count": 3, "events": []}
        )
        mark = types.SimpleNamespace(id="mark-1")
        with patch.object(
            main_module.replay_service, "list_marks", return_value=[mark]
        ), patch.object(
            main_module.replay_service,
            "play_mark",
            new=AsyncMock(return_value=clip),
        ) as play_mark:
            resp = await main_module.trigger_replay(
                _StubRequest(), _user=ADMIN_PRINCIPAL
            )
        assert resp.status_code == 200
        payload = _body(resp)
        assert payload["status"] == "success"
        assert payload["data"]["event_count"] == 3
        play_mark.assert_awaited_once_with("mark-1")

    async def test_replay_unknown_mark_is_404(self):
        with patch.object(
            main_module.replay_service,
            "play_mark",
            new=AsyncMock(side_effect=KeyError("x")),
        ):
            resp = await main_module.trigger_replay(
                _StubRequest({"mark_id": "x"}), _user=ADMIN_PRINCIPAL
            )
        assert resp.status_code == 404
        assert _body(resp)["status"] == "error"

    async def test_highlights_empty_explains_itself(self):
        with patch.object(main_module, "DEMO_MODE", False), patch.object(
            main_module.replay_service, "get_highlights", return_value=[]
        ):
            resp = await main_module.get_highlights()
        payload = _body(resp)
        assert payload["data"] == []
        assert payload["source"] == "replay_buffer"
        assert "No highlights yet" in payload["detail"]

    async def test_highlights_serves_real_reel(self):
        highlight = types.SimpleNamespace(
            model_dump=lambda: {"moment_id": "m1", "score": 90}
        )
        with patch.object(main_module, "DEMO_MODE", False), patch.object(
            main_module.replay_service,
            "get_highlights",
            return_value=[highlight],
        ):
            resp = await main_module.get_highlights()
        payload = _body(resp)
        assert payload["data"] == [{"moment_id": "m1", "score": 90}]
        assert "detail" not in payload
