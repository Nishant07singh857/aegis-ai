import asyncio
import json
import os
import random
import time
import uuid
import structlog
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.rate_limit_middleware import RateLimitMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from contextlib import asynccontextmanager
from sqlalchemy import func, or_, select

from app.config import settings
from app.core.event_bus import EventBus, EventPriority
from app.core.security import VALID_ROLES, security_manager
from app.api.deps import (
    BROADCAST_CONTROL_ROLES,
    TokenPayload,
    authenticate_websocket,
    principal_role,
    require_admin,
    verify_admin_login,
)
from app.api.v1.teams.demo_data import DEMO_TEAMS
from app.api.v1.teams.router import router as teams_router
# Phase 13 / W1: master-data expansion — players CRUD + tournament config.
from app.api.v1.players.router import router as players_router
# Phase 16: tournament-scoped user management (admin-only CRUD + bulk observers).
from app.api.v1.users.router import router as users_router
from app.api.v1.tournament.router import router as tournament_router
from app.api.v1.stream.router import router as stream_router
from app.api.v1.director.router import router as director_router
# Phase 27 (W4): director learning loop API (journal stats, proposals,
# adjustments, learning mode; director/admin writes).
from app.api.v1.director_learning.router import (
    router as director_learning_router,
)
# Phase 29 (W4): observer auto-pilot API (status, kill-switch,
# suggestions, override, POV journal; operator-gated).
from app.api.v1.observer_pilot.router import (
    router as observer_pilot_router,
)
from app.api.v1.cluster.router import router as cluster_router
# Phase 10: replay/highlights, feed adapter, broadcast flow, observer visuals.
from app.api.v1.replay.router import router as replay_router
from app.api.v1.feed.router import router as feed_router
from app.api.v1.preshow import router as preshow_router
from app.api.v1.ceremony import router as ceremony_router
from app.api.v1.match_point import router as match_point_router
from app.api.v1.zone import router as zone_router
from app.api.v1.rundown import router as rundown_router
from app.api.v1.pip import router as pip_router
# Phase 12: observer-team slot mapping (operator-gated writes; authenticated reads).
from app.api.v1.observers import router as observers_router
# Phase 17 (W4): video feed status / on-air selection (observer ingest).
from app.api.v1.audio.router import router as audio_router
# Phase 19: AI storyteller (director-gated generation, broadcast trigger).
from app.api.v1.stories.router import router as stories_router
from app.api.v1.anti_cheat.router import router as anti_cheat_router
from app.api.v1.vision_intelligence.router import (
    router as vision_intelligence_router,
)
# Phase 20: Global Predictive — live WWCD% prediction cards.
from app.api.v1.predictions.router import router as predictions_router
from app.api.v1.analytics_extra.router import router as analytics_extra_router
# Phase 24 (W3): flight path / match clock / phase + POI read APIs.
from app.api.v1.flight import router as flight_router
from app.api.v1.poi import router as poi_router
# Phase 24 (W6): live standings from the real event stream.
from app.api.v1.live_standings import router as live_standings_router
from app.services.flight_path import flight_path_service
from app.services.match_clock import match_clock
from app.services.phase_tracker import phase_tracker
from app.api.v1.video.router import (
    router as video_router,
    multi_device_manager,
)
# Phase 11: team analysis product (rotations/drops/zones + dossiers/PDF reports).
from app.analysis.router import router as analysis_router
from app.dossiers.router import router as dossiers_router
from app.cluster.registry import node_registry
from app.services.director_service import director_service
# Phase 10: new domain services (singletons; listeners wired in lifespan).
from app.services.replay.service import replay_service
from app.services.feed_adapter.service import feed_service
from app.services.preshow import preshow_service
from app.services.ceremony import ceremony_service
from app.services.match_point import match_point_service
from app.services.zone import zone_service
from app.services.rundown import rundown_service
from app.services.pip import pip_service
from app.intelligence.director_ai import DirectorAIEngine, BroadcastEvent, EventPriority as DirPriority
from app.intelligence.commentary_engine import get_commentary_engine
from app.db.session import init_db
from app.db.base import get_db_context
from app.db.models.enums import UserRole
from app.db.models.user import User

logger = structlog.get_logger("aegis.main")

# ==========================================
# CENTRALIZED STATE (Single Source of Truth)
# ==========================================
# Demo dataset lives in app/api/v1/teams/demo_data.py (DEMO_TEAMS). TEAMS is
# kept as a module-level alias because match_simulator and other inline demo
# endpoints mutate these dicts in place.
TEAMS = DEMO_TEAMS

# ── Phase 13 (W3 dynamic binding): explicit demo-mode gate ──────────────
# The match simulator, the agent-chatter simulator, and every inline endpoint
# below that serves the hardcoded demo dataset are ONLY active when
# DEMO_MODE=1/true/yes/on. Default OFF: with no live game feed connected,
# the honest state is empty data — demo squad names must never be presented
# as a live match, and no template may render them.
DEMO_MODE = os.environ.get("DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

_DEMO_DISABLED_DETAIL = (
    "Demo data is disabled (DEMO_MODE is off). Connect a live game feed via "
    "/api/v1/feed/ingest, or set DEMO_MODE=1 for simulated data."
)


def _demo_unavailable() -> JSONResponse:
    """Honest 503 for demo-serving endpoints when DEMO_MODE is off."""
    return JSONResponse(status_code=503, content={"detail": _DEMO_DISABLED_DETAIL})


def _live_teams() -> list:
    """Teams visible to live render paths.

    The demo dataset only when DEMO_MODE is on; otherwise an honest empty
    list. Renaming a team in the DB (or via /api/v1/teams) is picked up by
    the real read paths (teams API, 60s cache TTL) — nothing here caches a
    name, so no literal can go stale.
    """
    return MATCH_STATE["teams"] if DEMO_MODE else []


# ── Integrity Fix Pack (C6/H4): fabricated operator state ─────────────
# DEVICES, MATCH_STATE["agents_state"], MATCH_STATE["system_health"],
# MATCH_STATE["social_config"], MATCH_STATE["training_models"] and the
# seeded MATCH_STATE["render_jobs"] entries are a hardcoded DEMO dataset:
# no process measures them. Shared contract with the frontend (Worker C2):
#   * DEMO_MODE OFF — these blocks are NEVER served: no WS connect frame,
#     no broadcast and no REST response may carry them.
#   * DEMO_MODE ON — they may be served, but every such payload block
#     carries the marker "source": "demo" (on the frame envelope, on the
#     REST envelope, and inside dict payloads / dict list items).
#   * WS action acks carry "status": "success" ONLY when the action
#     actually happened; otherwise "status": "error" plus a reason.

def _demo_marked(data):
    """Copy of a demo payload block carrying the contract marker."""
    if isinstance(data, dict):
        return {**data, "source": "demo"}
    if isinstance(data, (list, deque)):
        return [
            _demo_marked(item) if isinstance(item, dict) else item
            for item in data
        ]
    return data


def _demo_frame(frame_type: str, data) -> dict:
    """A WS frame whose payload block is demo data, marked as such."""
    return {"type": frame_type, "data": _demo_marked(data), "source": "demo"}


def _demo_operator_frames() -> Dict[str, dict]:
    """Connect frames for the fabricated operator blocks (DEMO_MODE only).

    Keyed by frame type. Empty when DEMO_MODE is off — the blocks are
    absent from the connect payload, never sent with fake numbers.
    """
    if not DEMO_MODE:
        return {}
    return {
        "system_health": _demo_frame(
            "system_health", MATCH_STATE["system_health"]
        ),
        "agents_state": _demo_frame(
            "agents_state", MATCH_STATE["agents_state"]
        ),
        "devices_state": _demo_frame(
            "devices_state", MATCH_STATE["devices"]
        ),
        "agent_assignments": _demo_frame(
            "agent_assignments", MATCH_STATE["agent_assignments"]
        ),
        "render_jobs": _demo_frame(
            "render_jobs", list(MATCH_STATE["render_jobs"])
        ),
        "social_config": _demo_frame(
            "social_config", MATCH_STATE["social_config"]
        ),
        "training_models": _demo_frame(
            "training_models", MATCH_STATE["training_models"]
        ),
    }


def _system_status_payload() -> dict:
    """Real minimal system status (C6).

    Only facts this process actually knows: it is up, its version, the
    DEMO_MODE flag, the operator-set production modes and whether the
    event bus is running. The fabricated health numbers are included
    ONLY in DEMO_MODE, marked "source": "demo".
    """
    payload: dict = {
        "status": "online",
        "version": "2.0.0",
        "demo_mode": DEMO_MODE,
        "modes": MATCH_STATE["modes_state"],
        "event_bus": "connected" if event_bus._running else "disconnected",
    }
    if DEMO_MODE:
        payload["health"] = _demo_marked(MATCH_STATE["system_health"])
    return payload


DEVICES = [
    {"id": "gpu1", "name": "Node-GPU-1", "type": "RTX 4090", "load": 92, "status": "online", "task": "Vision CV Pipeline", "assigned_agent": "vision"},
    {"id": "gpu2", "name": "Node-GPU-2", "type": "RTX 3090", "load": 45, "status": "online", "task": "Highlight Rendering", "assigned_agent": "commentary"},
    {"id": "cpu1", "name": "Node-CPU-1", "type": "EPYC 7742", "load": 12, "status": "online", "task": "WebSocket Router", "assigned_agent": "director"},
]

AGENT_CHATTER_TEMPLATES = [
    ("{from_agent} → {to_agent}: Fight detected at Pochinki, confidence {conf:.2f}", "fight"),
    ("{from_agent} → {to_agent}: Zone closing in 45s, recommend POV switch to {team}", "zone"),
    ("{from_agent} → {to_agent}: Squad wipe incoming, emotion score {score}/100", "wipe"),
    ("{from_agent} → {to_agent}: Player {player} knocked, switching highlight buffer", "knock"),
    ("{from_agent} → {to_agent}: Commentary queued: '{comment}'", "commentary"),
    ("{from_agent} → {to_agent}: GPU load spike detected, throttling inference to 30fps", "perf"),
    ("{from_agent} → {to_agent}: New clutch situation, switching to {team} POV", "clutch"),
    ("{from_agent} → {to_agent}: Kill feed update processed, {n} events in queue", "kill"),
    ("{from_agent} → {to_agent}: Heatmap updated — high activity sector G7", "heatmap"),
    ("{from_agent} → {to_agent}: Model confidence dropped below threshold — recalibrating", "warn"),
]

MATCH_STATE = {
    "teams": TEAMS,
    "players": [p for team in TEAMS for p in team["players"]],
    # Phase 13: the demo match card exists only in DEMO_MODE; otherwise an
    # honest empty list (served only by the gated demo endpoints anyway).
    "matches": [{"id": "m1", "title": "BGIS Semifinals - Match 4", "map": "Erangel", "status": "LIVE"}] if DEMO_MODE else [],
    # Bounded ring buffers (Phase 12 reliability): these used to be plain lists
    # appended forever (OOM risk on a long-running server). deque(maxlen=N)
    # keeps only the most recent N entries; every reader below serializes via
    # list(...) so JSON encoding is unchanged.
    "kill_feed": deque(maxlen=500),
    "director_state": {"current_pov": "t1", "reason": "Initial", "action": "HOLD"},
    "agents_state": [
        {"id": "fight", "name": "Fight Detection Agent", "status": "active", "cpu": "12%", "mem": "145MB", "latency": "8ms", "tasks": 1204},
        {"id": "director", "name": "Master Director AI", "status": "active", "cpu": "24%", "mem": "310MB", "latency": "15ms", "tasks": 450},
        {"id": "commentary", "name": "AI Commentary Gen", "status": "active", "cpu": "45%", "mem": "2.1GB", "latency": "450ms", "tasks": 89},
        {"id": "vision", "name": "YOLOv8 Vision Agent", "status": "active", "cpu": "85%", "mem": "4.5GB", "latency": "32ms", "tasks": 24500},
        {"id": "economy", "name": "Utility Tracking", "status": "active", "cpu": "5%", "mem": "90MB", "latency": "4ms", "tasks": 800},
    ],
    "modes_state": {
        "auto_director": True,
        "ai_commentary": True,
        "fight_detection": True,
        "player_comms": False,
        "pip_mode": True,
    },
    "system_health": {
        "cpu_usage": 45,
        "memory_usage": 62,
        "gpu_usage": 78,
        "event_bus_latency": 2,
        "active_agents": 42,
        "events_per_sec": 124,
    },
    "commentary_log": deque(maxlen=500),
    # --- NEW STATE ---
    "devices": DEVICES,
    "agent_assignments": {
        # Phase 13: no pre-assigned team. A hardcoded squad name here would
        # leak into the live agent-assignments frame; assignments are set by
        # the operator via the assign_agent WS action.
        "vision": {"device_id": "gpu1", "device_name": "Node-GPU-1", "team_id": None, "team_name": None},
        "commentary": {"device_id": "gpu2", "device_name": "Node-GPU-2", "team_id": None, "team_name": None},
        "director": {"device_id": "cpu1", "device_name": "Node-CPU-1", "team_id": None, "team_name": None},
        "fight": {"device_id": "gpu1", "device_name": "Node-GPU-1", "team_id": None, "team_name": None},
        "economy": {"device_id": "cpu1", "device_name": "Node-CPU-1", "team_id": None, "team_name": None},
    },
    "agent_logs": deque(maxlen=500),
    "scrim_state": {"active": False, "lobby_id": None, "teams": [], "map": "Erangel", "status": "idle"},
    "social_config": {
        "YouTube Shorts": {"connected": True, "auto_post": True},
        "Instagram Reels": {"connected": True, "auto_post": True},
        "TikTok": {"connected": True, "auto_post": False},
        "Discord Webhook": {"connected": True, "auto_post": True},
    },
    "training_models": [
        {"name": "Director AI v2.4 (Reinforcement Learning)", "loss": "0.012", "status": "Training (Epoch 42/100)", "active": True},
        {"name": "Fight Detection (YOLOv8 + Tracker)", "loss": "0.004", "status": "Deployed", "active": True},
        {"name": "LLM Commentary Generator (Hindi Base)", "loss": "1.24", "status": "Fine-tuning", "active": True},
    ],
    "render_jobs": deque([
        {"id": "j1", "title": "Squad Wipe - YouTube Shorts Format", "status": "RENDERING", "progress": 72, "format": "YouTube Shorts"},
        {"id": "j2", "title": "Clutch Play - TikTok Format", "status": "PENDING", "progress": 0, "format": "TikTok"},
        {"id": "j3", "title": "Match 4 - Cinematic Recap", "status": "COMPLETED", "progress": 100, "format": "Full Broadcast"},
    ], maxlen=200),
}

# ==========================================
# WEBSOCKET MANAGER
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Send initial state
        await websocket.send_json({"type": "connected", "data": {"status": "ok"}})
        # Phase 13: the standings frame carries demo squads only in DEMO_MODE;
        # otherwise an honest empty list (live names come from /api/v1/teams).
        await websocket.send_json({"type": "standings", "data": _live_teams()})
        await websocket.send_json({"type": "kill_feed_init", "data": list(MATCH_STATE["kill_feed"])[-100:]})
        await websocket.send_json({"type": "director_state", "data": MATCH_STATE["director_state"]})
        # Phase 6: current director output state on connect.
        await websocket.send_json({"type": "director.state", "data": director_service.snapshot()})
        # Phase 10: seed new domain states on connect.
        await websocket.send_json({"type": "preshow.state", "data": preshow_service.snapshot()})
        await websocket.send_json({"type": "ceremony.step", "data": ceremony_service.snapshot()})
        await websocket.send_json({"type": "matchpoint.state", "data": match_point_service.snapshot()})
        await websocket.send_json({"type": "zone.tick", "data": zone_service.snapshot()})
        await websocket.send_json({"type": "rundown.state", "data": rundown_service.snapshot()})
        await websocket.send_json({"type": "pip.state", "data": pip_service.snapshot()})
        # C6: the fabricated operator blocks (system_health, agents_state,
        # devices, agent_assignments, render_jobs, social_config,
        # training_models) are sent ONLY in DEMO_MODE, each marked
        # "source": "demo". With DEMO_MODE off the frames are absent —
        # no invented device loads, agent stats or health numbers.
        _demo_frames = _demo_operator_frames()
        if "system_health" in _demo_frames:
            await websocket.send_json(_demo_frames["system_health"])
        if "agents_state" in _demo_frames:
            await websocket.send_json(_demo_frames["agents_state"])
        await websocket.send_json({"type": "modes_state", "data": MATCH_STATE["modes_state"]})
        # New state
        if "devices_state" in _demo_frames:
            await websocket.send_json(_demo_frames["devices_state"])
        if "agent_assignments" in _demo_frames:
            await websocket.send_json(_demo_frames["agent_assignments"])
        # Agent logs are written only by the DEMO_MODE chatter simulator,
        # so in DEMO_MODE the frame carries the demo marker too.
        if DEMO_MODE:
            await websocket.send_json(
                _demo_frame("agent_logs", list(MATCH_STATE["agent_logs"])[-100:])
            )
        else:
            await websocket.send_json({"type": "agent_logs", "data": list(MATCH_STATE["agent_logs"])[-100:]})
        await websocket.send_json({"type": "scrim_state", "data": MATCH_STATE["scrim_state"]})
        if "render_jobs" in _demo_frames:
            await websocket.send_json(_demo_frames["render_jobs"])
        if "social_config" in _demo_frames:
            await websocket.send_json(_demo_frames["social_config"])
        if "training_models" in _demo_frames:
            await websocket.send_json(_demo_frames["training_models"])

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def _send_one(self, websocket: WebSocket, message: dict):
        """Send to one client with a timeout; drop it on any failure.

        A slow or dead client must never stall the fan-out to everyone else,
        so every send is time-bounded. Any failure (timeout, disconnect,
        socket error) disconnects the client — the same dead-client policy
        the sequential loop had before.
        """
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=2.0)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: dict):
        # Concurrent fan-out (Phase 12 reliability): the old sequential loop
        # let one slow client head-of-line block every other client. Sends now
        # run concurrently with a 2s per-client timeout; dead clients are
        # dropped while the rest always receive the message on time.
        # Signature unchanged — all existing call sites keep working.
        conns = list(self.active_connections)
        await asyncio.gather(
            *(self._send_one(ws, message) for ws in conns),
            return_exceptions=True,
        )

manager = ConnectionManager()

# ==========================================
# GLOBAL SERVICES
# ==========================================
event_bus = EventBus()
# DEMO_MODE ONLY: DirectorAIEngine is the DEMO simulator's engine (fed
# exclusively by match_simulator below when DEMO_MODE=1). It is NOT the
# live director — the live decision service is
# app/services/live_director (Phase 25, P0-0), wired in the lifespan.
director_engine = DirectorAIEngine()
commentary_engine = get_commentary_engine()
simulation_task = None
chatter_task = None
director_hold_task = None


def _active_agent_count() -> int:
    """Agents/services THIS process reports as running. Pushed into the
    Redis node hash on every cluster heartbeat so the cluster view shows
    per-node load.

    C6: MATCH_STATE["agents_state"] is hardcoded demo data, so counting
    it would publish fabricated load. In DEMO_MODE it is the demo count;
    otherwise count the real lifespan services actually running on
    app.state (live director, vision intelligence, multi-feed monitor,
    observer pilot, live stats, replay queue, ISO recorder).
    """
    if DEMO_MODE:
        try:
            return sum(1 for a in MATCH_STATE.get("agents_state", []) if a.get("status") == "active")
        except Exception:
            return 0
    try:
        state = getattr(globals().get("app"), "state", None)
        if state is None:
            return 0
        keys = (
            "live_director",
            "vision_intelligence",
            "multi_feed_monitor",
            "observer_pilot",
            "live_stats",
            "replay_queue",
            "iso_recorder",
            "director_learning",
            "outcome_labeler",
        )
        return sum(1 for k in keys if getattr(state, k, None) is not None)
    except Exception:
        return 0


# ==========================================
# ACTION HANDLERS
# ==========================================
# Every action below mutates server state or triggers a side effect, so the
# whole control channel requires an authenticated operator-or-admin principal
# (JWT or API key passed as ?token= on the WebSocket connect). Viewers'
# tokens unlock only the read-only feeds. Unauthenticated or insufficient-role
# frames are dropped with an "unauthorized" ack — this closes revoke_key /
# restart_node / stop_training / etc. to anonymous callers AND to viewers.
# Passive subscriptions (state pushes, ping/pong) intentionally stay open.
async def _action_ack(action, status: str, **extra) -> dict:
    """Broadcast one action_ack frame and return its payload.

    Contract (C6/H4): ``status`` is "success" ONLY when the action
    actually happened; failures use "error" (or the pre-existing
    "unauthorized") plus a human-readable ``message``/``reason``.
    """
    payload = {"action": action, "status": status, **extra}
    await manager.broadcast({"type": "action_ack", "data": payload})
    return payload


async def _action_error(action, message: str, **extra) -> dict:
    """Honest failure ack: the action did NOT happen, and here is why."""
    return await _action_ack(
        action, "error", message=message, reason=message, **extra
    )


_NO_SOCIAL_SERVICE = (
    "No social publishing service is connected on this server, so nothing "
    "was posted. Publishing integrations are not configured; this action "
    "is not implemented."
)

_DEMO_ONLY_DEVICES = (
    "No real device/node registry is connected on this server. The device "
    "list is demo data and is only available with DEMO_MODE=1; nothing "
    "was changed."
)


async def handle_action(data: dict, user: Optional[TokenPayload] = None):
    action = data.get("action")

    if user is None:
        await manager.broadcast({
            "type": "action_ack",
            "data": {
                "action": action,
                "status": "unauthorized",
                "message": "Authentication required: reconnect with ?token=<jwt-or-api-key>.",
            },
        })
        logger.warning(f"Rejected unauthenticated WS action: {action}")
        return

    # Every action below mutates server state (mirrors the REST require_admin /
    # require_operator gates). Viewers may hold a valid JWT for the read-only
    # WS feeds, but only operator/admin principals may dispatch control
    # actions.
    if principal_role(user) not in BROADCAST_CONTROL_ROLES:
        await manager.broadcast({
            "type": "action_ack",
            "data": {
                "action": action,
                "status": "unauthorized",
                "message": "Insufficient role: this control action requires a broadcast-control role (admin, operator, director or spotter).",
            },
        })
        logger.warning(f"Rejected WS action from insufficient role: {action} user={user.username}")
        return

    if action == "assign_agent":
        agent_id = data.get("agent_id")
        device_id = data.get("device_id")
        team_id = data.get("team_id")

        # C6: assignments target the demo device list. With DEMO_MODE off
        # there is no real device registry, so there is nothing honest to
        # assign to — refuse instead of mutating fabricated state.
        if not DEMO_MODE:
            return await _action_error("assign_agent", _DEMO_ONLY_DEVICES)
        if not agent_id or not device_id:
            return await _action_error(
                "assign_agent", "Missing agent_id or device_id; nothing was assigned."
            )

        device = next((d for d in MATCH_STATE["devices"] if d["id"] == device_id), None)
        if device is None:
            return await _action_error(
                "assign_agent",
                f"No device with id {device_id!r}; nothing was assigned.",
            )
        # Phase 13: resolve the display name from the demo-gated team list —
        # None (honest unassigned) when DEMO_MODE is off.
        team = next((t for t in _live_teams() if t["id"] == team_id), None)

        MATCH_STATE["agent_assignments"][agent_id] = {
            "device_id": device_id,
            "device_name": device["name"],
            "team_id": team_id,
            "team_name": team["name"] if team else None,
        }
        # Update device's assigned agent
        for d in MATCH_STATE["devices"]:
            if d["id"] == device_id:
                d["assigned_agent"] = agent_id

        await manager.broadcast(_demo_frame("agent_assignments", MATCH_STATE["agent_assignments"]))
        await manager.broadcast(_demo_frame("devices_state", MATCH_STATE["devices"]))
        return await _action_ack(
            "assign_agent", "success",
            agent_id=agent_id, device=device["name"], source="demo",
        )

    elif action == "lock_pov":
        team_id = data.get("team_id")
        team = next((t for t in _live_teams() if t["id"] == team_id), None)
        if not team:
            return await _action_error(
                "lock_pov",
                f"Team {team_id!r} is not in the live team list "
                "(DEMO_MODE is off or the id is unknown); POV was not locked.",
            )
        decision = {
            "action": "LOCK_POV",
            "target_team": team_id,
            "current_pov": team_id,
            "reason": {"event_type": "manual_lock", "description": f"Director locked to {team['name']}"},
            "timestamp": time.time()
        }
        MATCH_STATE["director_state"] = decision
        await manager.broadcast({"type": "director_state", "data": decision})
        return await _action_ack(
            "lock_pov", "success", team=team["name"]
        )

    elif action == "start_scrim":
        lobby_id = f"LOBBY-{random.randint(1000, 9999)}"
        map_name = data.get("map", "Erangel")
        MATCH_STATE["scrim_state"] = {
            "active": True,
            "lobby_id": lobby_id,
            # Phase 13: demo squads only in DEMO_MODE; otherwise an honest
            # empty roster (the operator picks real teams from /api/v1/teams).
            "teams": [{"name": t["name"], "tag": t["tag"], "kills": 0, "placement": 0, "pts": 0} for t in _live_teams()[:8]],
            "map": map_name,
            "status": "lobby_open"
        }
        await manager.broadcast({"type": "scrim_state", "data": MATCH_STATE["scrim_state"]})
        return await _action_ack(
            "start_scrim", "success", lobby_id=lobby_id
        )

    elif action == "generate_recap":
        # The recap "render job" is a simulation over demo state — there
        # is no recap renderer wired to this action. DEMO_MODE only, and
        # marked demo; otherwise an honest error.
        if not DEMO_MODE:
            return await _action_error(
                "generate_recap",
                "No recap render service is wired to this action. Recap "
                "jobs are a DEMO_MODE simulation only; nothing was rendered.",
            )
        fmt = data.get("format", "YouTube Shorts")
        job_id = str(uuid.uuid4())[:6]
        new_job = {"id": job_id, "title": f"Match 4 Recap — {fmt}", "status": "RENDERING", "progress": 0, "format": fmt}
        MATCH_STATE["render_jobs"].append(new_job)
        await manager.broadcast(_demo_frame("render_jobs", list(MATCH_STATE["render_jobs"])))
        ack = await _action_ack(
            "generate_recap", "success", job_id=job_id, source="demo"
        )
        # Simulate progress (demo only — see gate above)
        asyncio.create_task(simulate_render_progress(job_id))
        return ack

    elif action == "restart_node":
        if not DEMO_MODE:
            return await _action_error("restart_node", _DEMO_ONLY_DEVICES)
        node_name = data.get("node")
        node_id = data.get("node_id")
        matched = None
        for d in MATCH_STATE["devices"]:
            if d["name"] == node_name or d["id"] == node_id:
                d["status"] = "restarting"
                matched = d
        if matched is None:
            return await _action_error(
                "restart_node",
                f"No device matching {node_name or node_id!r}; nothing was restarted.",
            )
        await manager.broadcast(_demo_frame("devices_state", MATCH_STATE["devices"]))
        asyncio.create_task(simulate_node_restart(node_name or node_id))
        return None

    elif action == "toggle_node":
        if not DEMO_MODE:
            return await _action_error("toggle_node", _DEMO_ONLY_DEVICES)
        node_id = data.get("node_id")
        for d in MATCH_STATE["devices"]:
            if d["id"] == node_id:
                d["status"] = "offline" if d["status"] == "online" else "online"
                await manager.broadcast(_demo_frame("devices_state", MATCH_STATE["devices"]))
                return await _action_ack(
                    "toggle_node", "success",
                    node=d["name"], new_status=d["status"], source="demo",
                )
        return await _action_error(
            "toggle_node",
            f"No device with id {node_id!r}; nothing was toggled.",
        )

    elif action == "test_post":
        platform = data.get("plat", data.get("platform", "Unknown"))
        return await _action_error(
            "test_post", _NO_SOCIAL_SERVICE, platform=platform
        )

    elif action == "toggle_autopost":
        if not DEMO_MODE:
            return await _action_error(
                "toggle_autopost",
                "No social accounts are connected on this server. The "
                "social config is demo data and is only available with "
                "DEMO_MODE=1; nothing was changed.",
            )
        platform = data.get("platform")
        if not platform or platform not in MATCH_STATE["social_config"]:
            return await _action_error(
                "toggle_autopost",
                f"Unknown social platform {platform!r}; nothing was changed.",
            )
        current = MATCH_STATE["social_config"][platform]["auto_post"]
        MATCH_STATE["social_config"][platform]["auto_post"] = not current
        await manager.broadcast(_demo_frame("social_config", MATCH_STATE["social_config"]))
        return await _action_ack(
            "toggle_autopost", "success",
            platform=platform, auto_post=not current, source="demo",
        )

    elif action == "upload_sponsor":
        # No sponsor-asset upload service exists and this WS frame carries
        # no file data — never claim an upload happened.
        return await _action_error(
            "upload_sponsor",
            "No sponsor upload service is wired to this action and no "
            "file was received over this channel; nothing was uploaded.",
        )

    elif action == "revoke_key":
        # H4 (critical): this handler used to broadcast a freshly invented
        # "sk_live_…" key and claim success while revoking nothing. API
        # keys here are a static allowlist (settings.ADMIN_API_KEYS) plus
        # JWTs — there is no runtime revocation service. Honest error,
        # and NEVER a fabricated key in any frame.
        return await _action_error(
            "revoke_key",
            "API keys cannot be revoked at runtime on this server: keys "
            "are the static ADMIN_API_KEYS allowlist (and JWTs expire on "
            "their own). Remove the key from ADMIN_API_KEYS and restart "
            "the server to revoke it. No key was revoked and no new key "
            "was issued.",
        )

    elif action == "stop_training":
        if not DEMO_MODE:
            return await _action_error(
                "stop_training",
                "No training jobs are managed by this server. The "
                "training model list is demo data and is only available "
                "with DEMO_MODE=1; nothing was stopped.",
            )
        model_name = data.get("model")
        matched = None
        for m in MATCH_STATE["training_models"]:
            if m["name"] == model_name:
                m["status"] = "Stopped"
                m["active"] = False
                matched = m
        if matched is None:
            return await _action_error(
                "stop_training",
                f"No training model named {model_name!r}; nothing was stopped.",
            )
        await manager.broadcast(_demo_frame("training_models", MATCH_STATE["training_models"]))
        return await _action_ack(
            "stop_training", "success", model=model_name, source="demo"
        )

    elif action == "post_now":
        platform = data.get("platform")
        return await _action_error(
            "post_now", _NO_SOCIAL_SERVICE, platform=platform
        )

    elif action == "create_tournament":
        # Wired to the real TournamentService (DB-backed, same service as
        # POST /api/v1/tournament). Validation or DB failures are honest
        # errors — a tournament is claimed only when a row was created.
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            return await _action_error(
                "create_tournament",
                "A non-empty tournament 'name' is required; nothing was created.",
            )
        try:
            from fastapi import HTTPException

            from app.api.v1.tournament.schemas import TournamentCreate
            from app.api.v1.tournament.service import TournamentService

            _allowed = {
                "slug", "description", "status", "start_date", "end_date",
                "total_matches", "points_per_kill", "placement_points",
                "rules", "prize_pool", "organizer", "is_public",
            }
            payload_data = {k: data[k] for k in _allowed if k in data}
            payload_data["name"] = name.strip()
            payload = TournamentCreate(**payload_data)
            async with get_db_context() as db:
                created = await TournamentService(db).create_tournament(payload)
        except HTTPException as e:
            return await _action_error(
                "create_tournament",
                f"Tournament was not created: {e.detail}",
            )
        except Exception as e:
            return await _action_error(
                "create_tournament",
                f"Tournament was not created: {e}",
            )
        return await _action_ack(
            "create_tournament", "success",
            name=created.name,
            tournament=created.model_dump(mode="json"),
        )

    elif action == "clip_highlight":
        # Wired to the real replay stack: render a video cut of a scored
        # highlight moment from the replay buffer. Unknown moments and
        # moments with no recorded source video are honest errors.
        clip_id = data.get("clip_id") or data.get("moment_id")
        if not clip_id:
            return await _action_error(
                "clip_highlight",
                "Missing clip_id (a highlight moment_id); nothing was clipped.",
            )
        try:
            record = await asyncio.to_thread(
                replay_service.request_render, clip_id
            )
        except KeyError:
            return await _action_error(
                "clip_highlight",
                f"No highlight moment with id {clip_id!r} in the current "
                "replay buffer; nothing was clipped.",
            )
        except Exception as e:
            return await _action_error(
                "clip_highlight", f"Highlight clip failed: {e}"
            )
        if getattr(record, "status", None) in ("no_source", "failed", "error"):
            return await _action_error(
                "clip_highlight",
                "No clip was produced: "
                + (getattr(record, "error", None) or f"render status is {record.status!r} (no recorded source video for this moment)."),
                clip_id=clip_id,
            )
        return await _action_ack(
            "clip_highlight", "success",
            clip_id=clip_id, render_status=getattr(record, "status", None),
        )

    elif action == "generate_all_highlights":
        # Real computation: the replay service scores the current replay
        # buffer into a highlight reel. An empty buffer is an honest
        # error (no qualifying events yet), never a fake success.
        try:
            highlights = replay_service.get_highlights(limit=50)
        except Exception as e:
            return await _action_error(
                "generate_all_highlights", f"Highlight generation failed: {e}"
            )
        if not highlights:
            return await _action_error(
                "generate_all_highlights",
                "No highlights were generated: the replay buffer has no "
                "qualifying events yet. Highlights are computed from "
                "recorded match events only and are never synthesised.",
            )
        extra = {"highlight_count": len(highlights)}
        if DEMO_MODE:
            extra["source"] = "demo"
        return await _action_ack(
            "generate_all_highlights", "success", **extra
        )

    elif action == "toggle_broadcaster_seat":
        seat = data.get("seat")
        return await _action_error(
            "toggle_broadcaster_seat",
            "No broadcaster-seat service exists on this server; seat "
            "state is not managed here and nothing was toggled.",
            seat=seat,
        )

    elif action == "test_webhook":
        return await _action_error(
            "test_webhook",
            "No webhook is configured on this server and no webhook "
            "delivery service is wired to this action; nothing was sent.",
        )

    else:
        return await _action_error(
            action,
            f"Unknown action {action!r}; nothing was done.",
        )

async def simulate_render_progress(job_id: str):
    """Simulate a render job progressing from 0 to 100% (DEMO_MODE only —
    the sole caller, the generate_recap action, is demo-gated)."""
    for progress in range(5, 101, 5):
        await asyncio.sleep(0.5)
        for job in MATCH_STATE["render_jobs"]:
            if job["id"] == job_id:
                job["progress"] = progress
                if progress >= 100:
                    job["status"] = "COMPLETED"
        await manager.broadcast(_demo_frame("render_jobs", list(MATCH_STATE["render_jobs"])))

async def simulate_node_restart(node_identifier: str):
    """Simulate a node restarting and coming back online (DEMO_MODE only —
    the sole caller, the restart_node action, is demo-gated)."""
    await asyncio.sleep(3)
    for d in MATCH_STATE["devices"]:
        if d["name"] == node_identifier or d["id"] == node_identifier:
            d["status"] = "online"
            d["load"] = random.randint(10, 30)
    await manager.broadcast(_demo_frame("devices_state", MATCH_STATE["devices"]))
    await manager.broadcast({"type": "action_ack", "data": {"action": "restart_node", "status": "success", "node": node_identifier, "source": "demo"}})

# ==========================================
# SIMULATOR TASK — Match Events
# ==========================================
async def match_simulator():
    """DEMO_MODE ONLY simulator: generates fake match events and feeds
    them to the DEMO DirectorAIEngine (app/intelligence/director_ai.py).
    This is not the live path — real spectator events are directed by
    app/services/live_director (Phase 25, P0-0)."""
    logger.info("Match simulator started.")
    
    while True:
        await asyncio.sleep(random.uniform(3, 8))
        
        killer_team = random.choice(MATCH_STATE["teams"])
        victim_team = random.choice([t for t in MATCH_STATE["teams"] if t["id"] != killer_team["id"] and t["alive"]])
        
        if not victim_team:
            continue
            
        killer = random.choice(killer_team["players"])
        victim = random.choice(victim_team["players"])
        weapon = random.choice(["M416", "AKM", "Beryl M762", "UMP45", "Kar98k", "AWM", "Grenade"])
        is_knock = random.random() > 0.3
        
        event_type = "knock" if is_knock else "kill"
        
        fight_score = random.randint(30, 95)
        priority = DirPriority.CRITICAL if fight_score > 85 else DirPriority.HIGH if fight_score > 60 else DirPriority.MEDIUM
        
        b_event = BroadcastEvent(
            id=str(uuid.uuid4())[:8],
            team_id=killer_team["id"],
            type="fight",
            priority=priority,
            confidence=0.95,
            emotion_score=fight_score,
            timestamp=time.time(),
            description=f"{killer} {event_type}s {victim}"
        )
        
        decision = director_engine.process_tick([b_event])
        if decision:
            MATCH_STATE["director_state"] = decision
            await manager.broadcast({"type": "director_state", "data": decision})
            await manager.broadcast({"type": "scene_switch", "data": decision})
            # Phase 6: publish the AI director's decision into the director
            # service. system_publish applies ONLY in FULL_AUTO mode — in
            # SEMI_AUTO and MANUAL it is a no-op, so the simulator can
            # never override the operator. A POV switch keeps the program
            # feed on the "main" scene; the legacy director_state/
            # scene_switch messages above still carry the POV detail for
            # existing clients.
            if decision.get("action") == "SWITCH_POV":
                await director_service.system_publish(
                    scene="main",
                    updated_by=f"director_ai:{decision.get('target_team')}",
                )
                # Phase 7 (W1): in SEMI_AUTO mode the AI never writes — it
                # proposes. A real fight event is a genuine suggestion
                # source: the killfeed-focus scene shows the action
                # context. propose_suggestion is a silent no-op outside
                # SEMI_AUTO, dedupes against identical pending
                # suggestions, and skips no-op suggestions, so the queue
                # stays honestly empty when there is nothing to propose.
                confidence = (
                    0.9
                    if priority == DirPriority.CRITICAL
                    else 0.75
                    if priority == DirPriority.HIGH
                    else 0.6
                )
                await director_service.propose_suggestion(
                    suggestion_type="scene",
                    payload={"scene": "killfeed_focus"},
                    reason=(
                        f"Fight detected — {killer} {event_type}s {victim}; "
                        "killfeed focus shows the action."
                    ),
                    confidence=confidence,
                    created_by=f"director_ai:{decision.get('target_team')}",
                )
        
        ctx = {
            "killer_name": killer,
            "victim_name": victim,
            "weapon": weapon,
            "team_id": killer_team["id"]
        }
        commentary = await commentary_engine.generate(event_type, ctx)
        if commentary:
            comm_obj = {"timestamp": time.time(), "text": commentary, "type": event_type}
            MATCH_STATE["commentary_log"].append(comm_obj)
            await manager.broadcast({"type": "commentary", "data": comm_obj})

        kill_event = {
            "id": str(uuid.uuid4()),
            "killer": killer,
            "killerTeam": killer_team["name"],
            "killerTeamId": killer_team["id"],
            "victim": victim,
            "victimTeam": victim_team["name"],
            "weapon": weapon,
            "isKnock": is_knock,
            "timestamp": time.time(),
        }
        
        if not is_knock:
            for t in MATCH_STATE["teams"]:
                if t["id"] == killer_team["id"]:
                    t["kills"] += 1
                    t["total_pts"] += 1
        
        MATCH_STATE["kill_feed"].append(kill_event)
            
        MATCH_STATE["system_health"]["events_per_sec"] = random.randint(100, 150)
        MATCH_STATE["system_health"]["cpu_usage"] = random.randint(40, 60)
        
        # Update device loads dynamically
        for d in MATCH_STATE["devices"]:
            if d["status"] == "online":
                d["load"] = min(99, max(5, d["load"] + random.randint(-5, 8)))
            
        await manager.broadcast({"type": "kill", "data": kill_event})
        await manager.broadcast({"type": "standings", "data": MATCH_STATE["teams"]})
        # C6: simulator-only frames (this task runs only in DEMO_MODE)
        # carry the demo marker.
        await manager.broadcast(_demo_frame("system_health", MATCH_STATE["system_health"]))
        await manager.broadcast(_demo_frame("devices_state", MATCH_STATE["devices"]))
        
        if event_bus._running:
            await event_bus.publish(
                event_type="kill_event" if not is_knock else "knock_event",
                data=kill_event,
                priority=EventPriority.HIGH,
                source="simulator"
            )

# ==========================================
# AGENT CHATTER SIMULATOR
# ==========================================
async def agent_chatter_simulator():
    """Generates realistic inter-agent communication messages."""
    logger.info("Agent chatter simulator started.")
    
    agent_names = {
        "fight": "FightDetect",
        "director": "Director",
        "commentary": "CommentaryGen",
        "vision": "VisionAgent",
        "economy": "EconTracker",
    }
    
    comments = [
        "Clutch situation imminent!",
        "Zone damage is critical for zone 4.",
        "SouL executing a compound rush.",
        "GodLike rotating east via Pochinki.",
    ]
    
    while True:
        await asyncio.sleep(random.uniform(2, 5))
        
        agents = list(agent_names.items())
        from_id, from_name = random.choice(agents)
        to_id, to_name = random.choice([a for a in agents if a[0] != from_id])
        
        template, log_type = random.choice(AGENT_CHATTER_TEMPLATES)
        team = random.choice(MATCH_STATE["teams"])
        player = random.choice(team["players"])
        
        message = template.format(
            from_agent=from_name,
            to_agent=to_name,
            conf=random.uniform(0.72, 0.99),
            score=random.randint(60, 98),
            team=team["name"],
            player=player,
            comment=random.choice(comments),
            n=random.randint(1, 8),
        )
        
        log_entry = {
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "from": from_name,
            "from_id": from_id,
            "to": to_name,
            "to_id": to_id,
            "message": message,
            "level": log_type,
            # Chatter is generated only by this DEMO_MODE simulator.
            "source": "demo",
        }
        
        MATCH_STATE["agent_logs"].append(log_entry)
        
        await manager.broadcast({"type": "agent_log", "data": log_entry})

# ==========================================
# PHASE 6 — FIRST ADMIN SEEDING + DB AUTH
# ==========================================
async def _seed_first_admin() -> None:
    """Seed the first admin user from ADMIN_EMAIL/ADMIN_PASSWORD.

    Runs once at startup. Creates the user only when BOTH env vars are set
    and no admin (role='admin' or is_superuser) exists yet. The password is
    bcrypt-hashed via SecurityManager.hash_password — never stored
    plaintext. Only the fact that seeding happened is logged, never the
    credentials. Never crashes startup: an unreachable DB or a schema that
    hasn't run 005_add_user_role yet just skips seeding with a warning.
    """
    email = (settings.ADMIN_EMAIL or "").strip().lower()
    password = settings.ADMIN_PASSWORD.get_secret_value() if settings.ADMIN_PASSWORD else ""
    if not email or not password:
        return
    try:
        async with get_db_context() as db:
            existing = (
                await db.execute(
                    select(User.id)
                    .where(or_(User.role == UserRole.ADMIN.value, User.is_superuser.is_(True)))
                    .limit(1)
                )
            ).first()
            if existing is not None:
                return
            base = "".join(
                c for c in email.split("@")[0] if c.isalnum() or c in "-_"
            )[:100] or "admin"
            username = base
            suffix = 2
            while (
                await db.execute(
                    select(User.id).where(User.username == username).limit(1)
                )
            ).first() is not None:
                username = f"{base}-{suffix}"
                suffix += 1
            db.add(
                User(
                    email=email,
                    username=username,
                    hashed_password=security_manager.hash_password(password),
                    full_name="Administrator",
                    role=UserRole.ADMIN.value,
                    is_superuser=True,
                    is_active=True,
                    is_verified=True,
                )
            )
        logger.info("Seeded the first admin user from ADMIN_EMAIL/ADMIN_PASSWORD.")
    except Exception as e:
        logger.warning(f"First-admin seeding skipped: {e}")


async def _authenticate_db_user(username: str, password: str) -> Optional[User]:
    """Validate credentials against DB users (Phase 6 roles).

    Matches on email or username (case-insensitive), requires an active,
    unlocked account and a bcrypt password match. Returns the User on
    success, None otherwise. Never raises: an unreachable DB or a schema
    missing the 005_add_user_role migration simply means "no DB user
    matched". Successful logins refresh last_login_at and reset the failed
    attempt counter (failed attempts increment it).
    """
    ident = (username or "").strip().lower()
    if not ident or not password:
        return None
    try:
        async with get_db_context() as db:
            stmt = (
                select(User)
                .where(or_(func.lower(User.email) == ident, func.lower(User.username) == ident))
                .limit(1)
            )
            user = (await db.execute(stmt)).scalars().first()
            if user is None or not user.is_active:
                return None
            locked_until = user.locked_until
            if locked_until is not None:
                if locked_until.tzinfo is None:
                    locked_until = locked_until.replace(tzinfo=timezone.utc)
                if locked_until > datetime.now(timezone.utc):
                    logger.warning("DB login rejected: account locked", username=user.username)
                    return None
            if not security_manager.verify_password(password, user.hashed_password):
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                return None
            user.last_login_at = datetime.now(timezone.utc)
            user.failed_login_attempts = 0
            return user
    except Exception as e:
        logger.warning(f"DB credential check unavailable, skipping: {e}")
        return None


async def _is_deactivated_db_user(username: str) -> bool:
    """Phase 16: is there a DB user matching this identity whose account is
    deactivated? Used by the login route to return an explicit 401
    "account deactivated" instead of the generic "Invalid credentials."

    NOTE: distinguishing deactivated accounts leaks account existence to
    anyone probing usernames. This is a deliberate product decision for
    Phase 16 (tournament staff must know WHY their login fails); the
    usernames involved (obs01..obsNN etc.) are not sensitive.
    Never raises: on any DB failure it returns False and the normal flow
    continues.
    """
    ident = (username or "").strip().lower()
    if not ident:
        return False
    try:
        async with get_db_context() as db:
            stmt = (
                select(User)
                .where(or_(func.lower(User.email) == ident, func.lower(User.username) == ident))
                .limit(1)
            )
            user = (await db.execute(stmt)).scalars().first()
            return user is not None and not user.is_active
    except Exception as e:
        logger.warning(f"Deactivation check unavailable, skipping: {e}")
        return False


# ==========================================
# PHASE 33 (K-H6) — DB BOOT FAIL-FAST
# ==========================================
async def _init_database_or_fail_fast() -> None:
    """Boot-time database gate: the lifespan calls this and NOTHING
    else wraps it, so a failure here aborts startup.

    The distinction is explicit and lives in exactly one place
    (``settings.database_configured`` — see ``app/config.py``):

    * DATABASE_URL UNSET/blank (non-production) — intentional
      dev/test no-DB mode. ``init_db`` is NOT called (there is no
      database to initialise; ``app.db.base`` only built an
      in-memory sqlite placeholder so the process can import).
      Boot continues WITHOUT persistence, and the log says so in
      plain language. DB-backed features then fail per-request
      under their own error handling — nothing is silently
      "persisted" to a store that does not exist. This is the
      documented ``.env.development`` "empty = in-memory mode"
      path, and the path the test suite's sqlite fixtures do NOT
      take (they set a real DATABASE_URL, below).
    * DATABASE_URL SET — a database is REQUIRED. ``init_db`` runs,
      and ANY failure (dead Postgres, refused connection, bad
      credentials, uncreatable schema) raises RuntimeError and
      ABORTS boot. The app must never silently boot in-memory
      against a configured-but-dead database: an operator who
      configured a DB and got a silent in-memory server would
      lose data and never know. This mirrors the existing
      production fail-fast for unset SECRET_KEY/DATABASE_URL,
      extended here to "set but dead" in every environment.
    * Production + unset is already refused earlier, at Settings
      validation (``config._fail_fast_in_production``), so this
      function never has to guess about production.

    The configured URL itself is NEVER logged: it embeds the DB
    password. Only the fact that a URL was configured, and the
    driver error, are logged.
    """
    if not settings.database_configured:
        logger.info(
            "No DATABASE_URL configured — booting without a database "
            "(dev/test no-DB mode). init_db skipped: DB-backed "
            "features will fail per-request and nothing is persisted. "
            "Set DATABASE_URL to run with a real database."
        )
        return
    try:
        await init_db()
    except Exception as e:
        logger.critical(
            "Refusing to boot: DATABASE_URL is configured but the "
            f"database is unreachable (init_db failed: {e}). The app "
            "will NOT fall back to in-memory state."
        )
        raise RuntimeError(
            "Refusing to boot: DATABASE_URL is configured but the "
            "database is unreachable (init_db failed). The app will "
            "NOT silently fall back to in-memory state. Fix "
            "DATABASE_URL or start the database, or unset "
            "DATABASE_URL for intentional no-DB dev/test mode. "
            f"Original error: {e}"
        ) from e


# ==========================================
# FASTAPI APP & LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("AEGIS AI Starting...")

    # Phase 33 (K-H6): intentionally UNWRAPPED. A configured-but-dead
    # database must abort startup; wrapping this in try/warn (the old
    # code) is exactly the silent in-memory boot this fix removes.
    await _init_database_or_fail_fast()

    # Phase 6: fan director.state changes out over /ws/live, and seed the
    # first admin user when ADMIN_EMAIL/ADMIN_PASSWORD are configured.
    director_service.add_listener(manager.broadcast)
    # Phase 10: fan new domain events out over /ws/live.
    replay_service.add_listener(manager.broadcast)
    preshow_service.add_listener(manager.broadcast)
    ceremony_service.add_listener(manager.broadcast)
    match_point_service.add_listener(manager.broadcast)
    zone_service.add_listener(manager.broadcast)
    rundown_service.add_listener(manager.broadcast)
    pip_service.add_listener(manager.broadcast)
    # Phase 17 (W4): fan video.feeds / video.on_air over /ws/live.
    multi_device_manager.add_listener(manager.broadcast)
    await _seed_first_admin()
        
    try:
        await event_bus.connect()
    except Exception as e:
        logger.warning(f"EventBus connection failed, continuing with in-memory broadcasting: {e}")

    # Phase 10: replay buffer fills from the bus; feed adapter activates
    # (after connect so attach() sees a running bus).
    replay_service.buffer.subscribe_to(event_bus)
    await feed_service.attach(event_bus)

    # Phase 22: close the vision -> intelligence loop. The subscriber
    # consumes the vision bus events (hp/utility/position/zone/healing/
    # loot + alive/team status) and feeds the fight detector, team
    # trackers, zone analytics, the story auto-flagger and the
    # vision-derivable anti-cheat checks. Built only when the bus is
    # actually running; a failure here never blocks startup — the
    # status endpoint then honestly reports the loop as not running.
    if event_bus._running:
        try:
            from app.services.vision_intelligence import (
                build_vision_intelligence_service,
            )

            vision_intelligence = build_vision_intelligence_service(event_bus)
            vision_intelligence.start()
            app.state.vision_intelligence = vision_intelligence
            logger.info("Vision intelligence subscriber started (Phase 22)")
        except Exception as e:
            app.state.vision_intelligence = None
            logger.warning(f"Vision intelligence subscriber unavailable: {e}")
    else:
        app.state.vision_intelligence = None

    # Phase 25 (W1, P0-0): THE live director — the unified decision
    # service (app/services/live_director). Subscribes to the real bus
    # (fight.detected promotions, kill/knock events, Phase 24
    # intel_alerts — their first consumer) and writes camera switches
    # through DirectorService (scene AND active_camera), honouring the
    # mode contract. Wired INDEPENDENT of DEMO_MODE: the demo
    # DirectorAIEngine below is a separate, DEMO_MODE-only path and is
    # NOT the live director. If the bus/vision is absent the director
    # is simply not built and its status reports not-running — it never
    # fabricates events.
    if event_bus._running:
        try:
            from app.services.live_director import build_live_director

            live_director = build_live_director(event_bus, director_service)
            live_director.start()
            app.state.live_director = live_director
            logger.info("Live director started (Phase 25, P0-0)")
        except Exception as e:
            app.state.live_director = None
            logger.warning(f"Live director unavailable: {e}")
    else:
        app.state.live_director = None

    # Phase 31 (Worker A): rotation / potential-fight intake bridge.
    # Vision intelligence publishes rotation_event (enriched with
    # mode / bearing / speed px/s / area POI / shared-area occupancy)
    # and potential_fight (not-yet-promoted detector readings), but
    # the live director subscribes to neither topic — so this bridge
    # feeds both straight into LiveDirector.note_rotation() /
    # note_potential_fight(). PVW pre-position only: the director's
    # own evaluation decides cuts under normal margins; this bridge
    # never cuts PGM itself. Failure-isolated: with it down, the
    # director simply never hears rotations (its prior behaviour).
    app.state.rotation_intake_bridge = None
    _ld_for_intake = getattr(app.state, "live_director", None)
    if event_bus._running and _ld_for_intake is not None:
        try:
            from app.services.live_director.rotation_intake import (
                BRIDGE_EVENT_TYPES,
                BRIDGE_SUBSCRIBER_ID,
                build_rotation_bridge,
            )

            _rotation_bridge = build_rotation_bridge(_ld_for_intake)
            event_bus.subscribe(
                subscriber_id=BRIDGE_SUBSCRIBER_ID,
                callback=_rotation_bridge,
                event_types=list(BRIDGE_EVENT_TYPES),
            )
            app.state.rotation_intake_bridge = _rotation_bridge
            logger.info("Rotation intake bridge started (Phase 31)")
        except Exception as e:
            logger.warning(f"Rotation intake bridge unavailable: {e}")

    # Phase 27 (W1): director learning loop foundation — decision
    # journal + suggestion-fate capture + manual-mode shadow
    # reconciliation (app/services/director_learning). Registers the
    # journal as the decision/fate sink for the hooks in
    # LiveDirector._log_decision and DirectorService, hooks the
    # DirectorService output-write path, and starts the periodic
    # reconciler. Needs no bus: decisions flow only when the live
    # director runs, fates flow whenever the API is used. Fully
    # failure-isolated — with it down, directing is unchanged and the
    # sinks stay no-ops; a dead DB degrades to drop counters.
    app.state.director_learning = None
    try:
        from app.services.director_learning import build_director_learning

        app.state.director_learning = build_director_learning(director_service)
        logger.info("Director learning journal started (Phase 27, W1)")
    except Exception as e:
        logger.warning(f"Director learning unavailable: {e}")

    # Phase 27 (W2 + W3 integration): outcome labeler + periodic
    # learning analysis. The labeler subscribes to the SAME bus topics
    # as the live director and persists evidence-backed outcome labels
    # (fizzle / missed_fight / premature_exit / good_call) for past
    # decisions. The analysis task runs the learning engine's rule pass
    # every 10 minutes (proposals in SUGGEST mode, bounded auto-apply
    # in AUTO mode; OFF creates nothing). Both failure-isolated — with
    # them down, directing and journaling are unchanged.
    app.state.outcome_labeler = None
    if event_bus._running:
        try:
            from app.services.director_learning.labeler import (
                build_outcome_labeler,
            )

            app.state.outcome_labeler = build_outcome_labeler(event_bus)
            logger.info("Outcome labeler started (Phase 27, W2)")
        except Exception as e:
            logger.warning(f"Outcome labeler unavailable: {e}")

    async def _phase27_analysis_loop() -> None:
        while True:
            try:
                await asyncio.sleep(600)
                try:
                    from app.services.director_learning import engine as _eng

                    _run = _eng.periodic_analysis_callable()
                    await _run()
                except Exception:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    app.state.phase27_analysis_task = asyncio.create_task(
        _phase27_analysis_loop()
    )

    # Phase 25 (integration): replay stack (W3) + housekeeping ticker.
    # The ISO recorder taps the multi-device manager's real frame flow
    # and the offer/take queue subscribes to the live director's
    # replay.candidate hand-offs. The ticker closes two loops that are
    # otherwise event-starved on a quiet feed: the live director's
    # context-beat completion (W5) and the replay queue's highlight
    # scan + full-auto take (W3). Every tick is failure-isolated; a
    # failure here never blocks startup — statuses then honestly
    # report the pieces as not running.
    app.state.iso_recorder = None
    app.state.replay_queue = None
    try:
        from app.services.replay.service import build_replay_stack_from_manager

        _recorder, _queue = build_replay_stack_from_manager(
            multi_device_manager
        )
        await _recorder.start_async()
        if event_bus._running:
            _queue.subscribe_to(event_bus)
        app.state.iso_recorder = _recorder
        app.state.replay_queue = _queue
        logger.info("Replay stack started (Phase 25: ISO recorder + queue)")
    except Exception as e:
        logger.warning(f"Replay stack unavailable: {e}")

    async def _phase25_ticker() -> None:
        while True:
            try:
                await asyncio.sleep(1.5)
                _ld = getattr(app.state, "live_director", None)
                if _ld is not None:
                    try:
                        await _ld.evaluate_now(trigger="ticker")
                    except Exception:
                        pass
                _q = getattr(app.state, "replay_queue", None)
                if _q is not None:
                    try:
                        _q.poll_highlights()
                    except Exception:
                        pass
                    try:
                        await _q.maybe_take_auto()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    app.state.phase25_ticker_task = asyncio.create_task(_phase25_ticker())

    # Phase 23 (W4): grenade trajectory estimator. Consumes kill/knock/
    # utility/position events and stores an ESTIMATED thrower->explosion
    # arc ONLY when both endpoints were actually reported in the same
    # coordinate space (see docs/GRENADE_TRAJECTORY.md). Served at
    # GET /api/v1/analysis/grenade-trajectories. A failure here never
    # blocks startup; the endpoint then honestly returns an empty list.
    if event_bus._running:
        try:
            from app.services.grenade_trajectory import (
                build_grenade_trajectory_service,
            )

            grenade_trajectory = build_grenade_trajectory_service(event_bus)
            grenade_trajectory.start()
            app.state.grenade_trajectory = grenade_trajectory
            logger.info("Grenade trajectory subscriber started (Phase 23)")
        except Exception as e:
            app.state.grenade_trajectory = None
            logger.warning(f"Grenade trajectory subscriber unavailable: {e}")
    else:
        app.state.grenade_trajectory = None

    # Phase 24 (W6): live stats & standings writer. Subscribes to
    # kill/knock/revive/match_start/match_end/team_wipe on the bus,
    # accumulates per-player stats, persists them provisionally through
    # MatchService.record_player_stats (post-match manual entry wins —
    # see services/live_stats.py), and serves the provisional live
    # table at GET /api/v1/standings/live. A failure here never blocks
    # startup; the endpoint then honestly reports the service as down.
    if event_bus._running:
        try:
            from app.services.live_stats import build_live_stats_service

            live_stats = build_live_stats_service(event_bus)
            live_stats.start()
            app.state.live_stats = live_stats
            logger.info("Live stats writer started (Phase 24)")
        except Exception as e:
            app.state.live_stats = None
            logger.warning(f"Live stats writer unavailable: {e}")
    else:
        app.state.live_stats = None

    # Phase 24 (W3): match clock + phase tracker + flight-path bus wiring.
    # The clock subscribes to match_start/match_end itself; the phase
    # tracker feeds the (previously caller-less) ParachutePhaseDetector
    # from alive/kill events + the clock; the flight service emits
    # flight_path_set when the director sets a path. Failures never
    # block startup — the flight endpoints then report honest empties.
    if event_bus._running:
        try:
            match_clock.start(event_bus)
            phase_tracker.start(event_bus)

            async def _flight_publish(event_type: str, data: dict) -> None:
                await event_bus.publish(
                    event_type=event_type, data=data,
                    source="flight_path", match_id=data.get("match_id"),
                )

            flight_path_service.set_publisher(_flight_publish)
            app.state.match_clock = match_clock
            app.state.phase_tracker = phase_tracker
            logger.info("Match clock + phase tracker started (Phase 24)")
        except Exception as e:
            app.state.match_clock = None
            app.state.phase_tracker = None
            logger.warning(f"Match clock / phase tracker unavailable: {e}")
    else:
        app.state.match_clock = None
        app.state.phase_tracker = None

    # Phase 14: register the vision-pipeline feed adapter so
    # GET /api/v1/feed/status lists "vision" as a source. It is NOT
    # started automatically -- capture begins only when an operator calls
    # POST /api/v1/feed/vision/start. A machine without the vision stack
    # (no cv2) simply skips registration; the backend keeps working.
    vision_adapter = None
    try:
        from app.services.feed_adapter.vision_adapter import VisionFeedAdapter
        vision_adapter = VisionFeedAdapter(
            event_bus, multi_device_manager=multi_device_manager
        )
        feed_service.register(vision_adapter)
        logger.info("Vision feed adapter registered (idle until operator starts it)")
    except ImportError as e:
        logger.warning(f"Vision feed adapter unavailable, skipping: {e}")

    # Phase 26: THE multi-feed monitor as a lifespan-owned service —
    # "har feed ~2 sec, isko live karo". Phase 25 built the monitor but
    # it only existed as a side effect of the operator-gated vision
    # start, attached to the primary pipeline's lifecycle. Now the
    # monitor is built and started HERE, with the app: it ingests
    # through the vision adapter (the identical NormalizedEvent -> bus
    # path the primary pipeline uses, so VisionIntelligence and the
    # LiveDirector consume monitor events exactly like single-feed
    # events), watches the multi-device manager's shared FrameGrabber,
    # and auto-enrolls every observer feed the manager maps — feeds
    # joining/leaving mid-match are adopted/dropped on the next tick
    # (source ids encode their slot; nothing is ever guessed). The
    # monitor starts no capture itself: it reads frames only from
    # sources the capture manager already runs. If the bus is down the
    # monitor is not built and app.state reports it as not running —
    # honestly, like every other lifespan service.
    app.state.multi_feed_monitor = None
    # Phase 29 (W4): ONE cached roster provider, shared by the monitor
    # (its POV trackers bind spectated identity from observer_slots +
    # players) and the pilot service (which drives its 30 s refresh).
    # Built before the monitor so the provider is threaded in at
    # construction; the initial refresh is failure-isolated — an
    # unreachable DB simply leaves POV identity honestly UNKNOWN.
    _pov_roster_provider = None
    try:
        from app.services.observer_pilot.roster import CachedRosterProvider

        _pov_roster_provider = CachedRosterProvider()
        await _pov_roster_provider.refresh()
    except Exception as e:
        _pov_roster_provider = None
        logger.warning(f"POV roster provider unavailable: {e}")
    if vision_adapter is not None and event_bus._running:
        try:
            from app.vision.multi_feed_monitor import build_multi_feed_monitor

            multi_feed_monitor = build_multi_feed_monitor(
                ingest=vision_adapter.ingest,
                multi_device_manager=multi_device_manager,
                roster_provider=_pov_roster_provider,
            )
            vision_adapter.set_shared_monitor(multi_feed_monitor)
            await multi_feed_monitor.start()
            app.state.multi_feed_monitor = multi_feed_monitor
            logger.info(
                "Multi-feed monitor started in lifespan (Phase 26): "
                "cheap readers on every connected observer feed"
            )
        except Exception as e:
            app.state.multi_feed_monitor = None
            logger.warning(f"Multi-feed monitor unavailable: {e}")

    # Phase 29 (W4): the observer auto-pilot service — per-feed POV
    # decisions through the mode policy (docs/OBSERVER_AUTO_PILOT.md
    # §6), executed by W3's tap executor, journaled durably. The
    # LiveDirector gets the monitor's POV states as a read-only
    # provider (status + decision-log colour; scoring untouched).
    # Failure-isolated like every lifespan service: with it down,
    # directing and monitoring are unchanged and the pilot API
    # honestly reports not-running.
    app.state.observer_pilot = None
    _mfm_for_pilot = getattr(app.state, "multi_feed_monitor", None)
    if _mfm_for_pilot is not None:
        _ld_for_pov = getattr(app.state, "live_director", None)
        if _ld_for_pov is not None:
            try:
                _ld_for_pov.set_pov_state_provider(_mfm_for_pilot.pov_state)
            except Exception:
                pass
        try:
            from app.services.observer_pilot import build_observer_pilot

            observer_pilot = build_observer_pilot(
                _mfm_for_pilot,
                bus=event_bus if event_bus._running else None,
                roster_provider=_pov_roster_provider,
                multi_device_manager=multi_device_manager,
            )
            observer_pilot.start()
            app.state.observer_pilot = observer_pilot
            logger.info("Observer auto-pilot started (Phase 29, W4)")
        except Exception as e:
            app.state.observer_pilot = None
            logger.warning(f"Observer auto-pilot unavailable: {e}")

    # Phase 7 / W3: register this process in the distributed cluster registry
    # (Redis-backed, standalone fallback when Redis is unreachable).
    await node_registry.start(agent_count_provider=_active_agent_count)

    # Phase 22: warm the Piper voices in the background so the first live
    # commentary line does not pay the cold model-load cost (measured in
    # docs/AUDIO.md). Fire-and-forget: a slow/failed warm-up never blocks
    # startup and never fakes readiness — synthesis reports honestly.
    async def _warm_commentary_voices() -> None:
        try:
            from app.audio.commentary_feed import get_commentary_feed

            warmed = await get_commentary_feed()._get_synth().warm_models()
            logger.info("Commentary voice warm-up finished", warmed=warmed)
        except Exception as e:
            logger.warning(f"Commentary voice warm-up skipped: {e}")

    asyncio.create_task(_warm_commentary_voices())
        
    global simulation_task, chatter_task, director_hold_task
    # Phase 18 (W3): background ticker enforcing the map-cam 30s maximum
    # hold (Visual Broadcast Bible §2) in every director mode. The ticker
    # calls director_service.enforce_scene_holds() every 5s; on expiry the
    # service hard-cuts back to "main" and broadcasts director.state, so
    # all WS clients (incl. the OBS capture page) cut within ~5s of the
    # deadline even when nobody is polling.
    async def _director_scene_hold_enforcer() -> None:
        try:
            while True:
                await asyncio.sleep(5)
                await director_service.enforce_scene_holds()
        except asyncio.CancelledError:
            pass

    director_hold_task = asyncio.create_task(_director_scene_hold_enforcer())
    # Phase 31 (Worker A): production callers for the dormant
    # producers — phase suggestions (edge-triggered on phase entry)
    # and drop-clash checks (predicted over map history, observed
    # over this match's drops). 30 s cadence: phases last minutes
    # and drop rows land once per team per match, so polling is
    # timely enough without hammering the DB; both calls dedupe
    # internally (phase edge state / clash signature set), so most
    # ticks are cheap no-ops. Skips entirely when no flight path is
    # set or the bus is down — alerts cannot reach the director
    # then, and marking them emitted would lose them. Failure-
    # isolated: any tick error is swallowed, never fatal.
    async def _phase31_drop_clash_check() -> None:
        path = flight_path_service.snapshot()
        if not path.get("is_set"):
            return
        try:
            from app.db.session import AsyncSessionLocal
            from app.services.flight_path import fetch_drop_rows

            async with AsyncSessionLocal() as db:
                history = await fetch_drop_rows(
                    db, map_id=path.get("map_id")
                )
                if path.get("match_id"):
                    current = await fetch_drop_rows(
                        db,
                        match_id=path.get("match_id"),
                        map_id=path.get("map_id"),
                    )
                else:
                    current = history
            await flight_path_service.check_drop_clashes(
                current, history_rows=history
            )
        except Exception as e:
            logger.warning(f"Phase 31 drop-clash check failed: {e}")

    async def _phase31_wiring_ticker() -> None:
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await director_service.propose_phase_suggestions_on_change()
                except Exception:
                    pass
                try:
                    if event_bus._running:
                        await _phase31_drop_clash_check()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    app.state.phase31_ticker_task = asyncio.create_task(_phase31_wiring_ticker())
    # Phase 13: the demo simulators broadcast hardcoded squad names into the
    # live WS frames (kill feed, standings, director state, agent logs), so
    # they run ONLY with explicit DEMO_MODE=1. Default: disabled — the honest
    # state with no game feed is empty data, never fake squads.
    if DEMO_MODE:
        simulation_task = asyncio.create_task(match_simulator())
        chatter_task = asyncio.create_task(agent_chatter_simulator())
        logger.info("Demo simulators started (DEMO_MODE=1).")
    else:
        simulation_task = None
        chatter_task = None
        logger.info("Demo simulators disabled (DEMO_MODE is off).")
    
    logger.info("✅ AEGIS AI Ready")
    
    yield
    
    logger.info("AEGIS AI Shutting down...")
    _op = getattr(app.state, "observer_pilot", None)
    if _op is not None:
        try:
            _op.stop()
        except Exception:
            pass
    _mfm = getattr(app.state, "multi_feed_monitor", None)
    if _mfm is not None:
        try:
            await _mfm.stop()
        except Exception:
            pass
    _dl = getattr(app.state, "director_learning", None)
    if _dl is not None:
        try:
            _dl.stop()
        except Exception:
            pass
    _ol = getattr(app.state, "outcome_labeler", None)
    if _ol is not None:
        try:
            _ol.stop()
        except Exception:
            pass
    _p27t = getattr(app.state, "phase27_analysis_task", None)
    if _p27t:
        _p27t.cancel()
    _ld = getattr(app.state, "live_director", None)
    if _ld is not None:
        try:
            _ld.stop()
        except Exception:
            pass
    _vi = getattr(app.state, "vision_intelligence", None)
    if _vi is not None:
        try:
            _vi.stop()
        except Exception:
            pass
    _ls = getattr(app.state, "live_stats", None)
    if _ls is not None:
        try:
            _ls.stop()
        except Exception:
            pass
    try:
        phase_tracker.stop()
        match_clock.stop()
    except Exception:
        pass
    if simulation_task:
        simulation_task.cancel()
    if chatter_task:
        chatter_task.cancel()
    if director_hold_task:
        director_hold_task.cancel()
    _p31t = getattr(app.state, "phase31_ticker_task", None)
    if _p31t:
        _p31t.cancel()
    try:
        event_bus.unsubscribe("phase31_rotation_intake")
    except Exception:
        pass
    _p25t = getattr(app.state, "phase25_ticker_task", None)
    if _p25t:
        _p25t.cancel()
    try:
        await event_bus.disconnect()
    except Exception:
        pass
    # Phase 7 / W3: clean cluster deregistration (Redis key deleted; TTL is
    # the backstop if the process dies ungracefully).
    try:
        await node_registry.stop()
    except Exception:
        pass

app = FastAPI(title="AEGIS AI", version="2.0.0", lifespan=lifespan)

# CORS honors settings.CORS_ORIGINS (env CORS_ORIGINS). allow_credentials is
# only safe with explicit origins — browsers reject it with "*", so it is
# disabled automatically when the origin list is the wildcard.
_cors_origins = settings.CORS_ORIGINS
_cors_allow_credentials = not (len(_cors_origins) == 1 and _cors_origins[0] == "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
)
# Phase 2: rate limiting on mutating endpoints (5/min auth, 60/min broadcast/scrims/content).
app.add_middleware(RateLimitMiddleware)

# Teams vertical slice (Phase 2): public reads, admin writes. Mounted here
# rather than through app/api/v1/router.py (deleted) — teams is the only
# domain extracted from main.py so far.
app.include_router(teams_router, prefix="/api/v1", tags=["Teams"])

# Phase 13 / W1: players CRUD + tournament config (public reads, admin writes).
# Mounted BEFORE the legacy inline /api/v1/players + /api/v1/tournaments
# demo endpoints below, so the real routers always win route matching.
app.include_router(players_router, prefix="/api/v1", tags=["Players"])
app.include_router(tournament_router, prefix="/api/v1", tags=["Tournament"])

# Phase 5: automated live streaming (operator-gated start/stop, authenticated status).
app.include_router(stream_router, prefix="/api/v1", tags=["Stream"])

# Phase 6: director control API (role-gated; see app/api/v1/director).
app.include_router(director_router, prefix="/api/v1", tags=["Director"])

# Phase 27 (W4): director learning API (role-gated; see
# app/api/v1/director_learning).
app.include_router(
    director_learning_router, prefix="/api/v1", tags=["Director Learning"]
)

# Phase 29 (W4): observer auto-pilot API.
app.include_router(
    observer_pilot_router, prefix="/api/v1", tags=["Observer Pilot"]
)

# Phase 7 / W3: cluster introspection API (read-only; any authenticated user).
app.include_router(cluster_router, prefix="/api/v1", tags=["Cluster"])

# Phase 10: replay + highlights, feed adapter, broadcast flow, observer visuals.
app.include_router(replay_router, prefix="/api/v1", tags=["Replay"])
app.include_router(feed_router, prefix="/api/v1", tags=["Feed"])
app.include_router(preshow_router, prefix="/api/v1", tags=["Preshow"])
app.include_router(ceremony_router, prefix="/api/v1", tags=["Ceremony"])
app.include_router(match_point_router, prefix="/api/v1", tags=["Match Point"])
app.include_router(zone_router, prefix="/api/v1", tags=["Zone"])
app.include_router(rundown_router, prefix="/api/v1", tags=["Rundown"])
app.include_router(pip_router, prefix="/api/v1", tags=["PiP"])
# Phase 12: observer-team slot mapping.
app.include_router(observers_router, prefix="/api/v1", tags=["Observers"])
# Phase 11: analysis product.
app.include_router(analysis_router, prefix="/api/v1", tags=["Analysis"])
app.include_router(dossiers_router, prefix="/api/v1", tags=["Dossiers"])
# Phase 16: tournament-scoped user management (admin-only).
app.include_router(users_router, prefix="/api/v1", tags=["Users"])
# Phase 17 (W4): video feed status / thumbnails / on-air selection.
# Phase 17 W2: the same router also serves the MJPEG program feed
# GET /api/v1/video/feed/{slot} for the broadcast output video layer.
app.include_router(video_router, prefix="/api/v1", tags=["Video"])
# W3: broadcast audio pipeline — TTS commentary, game audio, mixer.
app.include_router(audio_router, prefix="/api/v1", tags=["Audio"])
# Phase 19: AI storyteller — data stories for broadcast graphics.
app.include_router(stories_router, prefix="/api/v1", tags=["Stories"])

# Phase 22: anti-cheat review surface (admin/director) + vision
# intelligence subscriber status.
app.include_router(anti_cheat_router, prefix="/api/v1", tags=["Anti-Cheat"])
app.include_router(
    vision_intelligence_router, prefix="/api/v1", tags=["Vision Intelligence"]
)
# Phase 20: Global Predictive — live WWCD% prediction cards (30s cadence).
app.include_router(predictions_router, prefix="/api/v1", tags=["Predictions"])
app.include_router(analytics_extra_router, prefix="/api/v1", tags=["Analytics"])
# Phase 24 (W3): flight path / clock / phase + POI reads.
app.include_router(flight_router, prefix="/api/v1", tags=["Flight"])
app.include_router(poi_router, prefix="/api/v1", tags=["POI"])
# Phase 24 (W6): live standings from the real event stream.
app.include_router(live_standings_router, prefix="/api/v1", tags=["Standings"])

@app.get("/")
async def root():
    return JSONResponse({
        "message": "Welcome to AEGIS AI Backend",
        "docs": "/docs",
        "status_endpoint": "/api/v1/system/status"
    })

# ==========================================
# AUTH ENDPOINTS
# ==========================================
@app.post("/api/v1/auth/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Operator login. Two credential sources, checked in order:

    1. Phase 1 configured operator credential (ADMIN_USERNAME/ADMIN_PASSWORD
       from settings) → JWT with role "admin".
    2. Phase 6 DB-backed users (email or username + bcrypt password) →
       JWT carrying the user's DB role. Phase 16 adds ``team_id`` and
       ``tournament_id`` claims for scoped accounts, and deactivated
       accounts get an explicit 401 "account deactivated". This is how the
       seeded first admin (ADMIN_EMAIL/ADMIN_PASSWORD) logs in.

    Returns a JWT (HS256, ACCESS_TOKEN_EXPIRE_MINUTES expiry) usable as
    `Authorization: Bearer <token>` on protected routes, or as `?token=` on
    the /ws/live control channel. Headless clients can alternatively use a
    static X-API-Key from ADMIN_API_KEYS.
    """
    # 1) Configured operator credential — unchanged Phase 1 behavior.
    if verify_admin_login(form_data.username, form_data.password):
        token = security_manager.create_access_token(
            user_id="operator",
            username=settings.ADMIN_USERNAME,
            is_superuser=True,
            scopes=["admin"],
            role="admin",
        )
        return JSONResponse({"access_token": token, "token_type": "bearer"})
    # 2) DB-backed users with roles (Phase 6; extended Phase 16).
    # Deactivated accounts fail closed with an explicit message — checked
    # BEFORE the password so staff know the account itself is shut off.
    # (See _is_deactivated_db_user for the deliberate user-enumeration note.)
    if await _is_deactivated_db_user(form_data.username):
        return JSONResponse({"error": "account deactivated."}, status_code=401)
    db_user = await _authenticate_db_user(form_data.username, form_data.password)
    if db_user is not None:
        role = (db_user.role or "viewer").strip().lower()
        if role not in VALID_ROLES:
            role = "viewer"
        token = security_manager.create_access_token(
            user_id=str(db_user.id),
            username=db_user.username,
            is_superuser=bool(db_user.is_superuser) or role == "admin",
            scopes=[role],
            role=role,
            # Phase 16: team/tournament scoping claims (getattr-guarded so
            # logins keep working against a DB that predates migration 010).
            team_id=getattr(db_user, "team_id", None),
            tournament_id=getattr(db_user, "tournament_id", None),
        )
        return JSONResponse({"access_token": token, "token_type": "bearer"})
    if not settings.ADMIN_PASSWORD:
        return JSONResponse(
            {"error": "Password login is not configured (set ADMIN_PASSWORD)."},
            status_code=503,
        )
    return JSONResponse({"error": "Invalid credentials."}, status_code=401)

# ==========================================
# WEBSOCKET ENDPOINTS
# ==========================================
@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    # Control-channel auth: browsers can't set WS headers, so the client
    # passes ?token=<jwt-or-api-key>. Passive subscription stays public;
    # mutating actions are gated inside handle_action().
    # Phase 32 (K-H1 fix): the token is RE-VALIDATED on every action frame
    # (authenticate_websocket checks the DB user's live is_active flag),
    # never frozen at connect time — a user deactivated mid-connection
    # gets an "unauthorized" ack on their next action instead of keeping
    # broadcast control until JWT expiry.
    raw_token = websocket.query_params.get("token")
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue
            try:
                payload = json.loads(data)
                ws_user = await authenticate_websocket(
                    websocket,
                    token=raw_token,
                )
                await handle_action(payload, user=ws_user)
            except Exception as e:
                logger.error(f"Failed to handle WS action: {e}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        
@app.websocket("/ws/monitor")
async def websocket_monitor(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ==========================================
# REST API ENDPOINTS
# ==========================================
@app.get("/api/v1/system/status")
async def get_system_status():
    # C6 BREAKING CHANGE: this endpoint used to serve the fabricated
    # MATCH_STATE["system_health"] numbers to UNAUTHENTICATED callers.
    # It now returns only real minimal health (status / version /
    # demo_mode / operator modes / event-bus state). The fabricated
    # "health" block is present ONLY in DEMO_MODE, marked
    # "source": "demo". Known caller: frontend/app/broadcast/page.tsx
    # reads only `modes`, which is unchanged.
    return JSONResponse(_system_status_payload())

@app.get("/api/v1/players/mvp")
async def get_mvp():
    # Phase 21 (W2): DEMO endpoint — gated behind DEMO_MODE and clearly
    # labelled as demo. The score is the SINGLE canonical PRD formula
    # (mvp_calculator.compute_mvp_score); the old competing demo formula
    # and the kills-derived damage estimate were deleted. The demo
    # dataset carries only team kills, so per-player damage / revives /
    # survival / headshots / clutches are unknown: damage is reported
    # as null (never estimated) and the missing stats contribute 0 to
    # the canonical score. The production MVP path is the persisted one:
    # MatchService.record_player_stats → GET
    # /api/v1/matches/{id}/player-stats (rating / mvp_score).
    if not DEMO_MODE:
        return _demo_unavailable()
    from app.agents.analytics_agents.mvp_calculator import compute_mvp_score

    players_data = []
    for team in MATCH_STATE["teams"]:
        for player in team["players"]:
            per_player_kills = team["kills"] // 4
            players_data.append({
                "id": f"{team['id']}_{player}",
                "name": player,
                "team": team["name"],
                "role": "Assaulter",
                "kills": per_player_kills,
                "damage": None,
                "mvp_score": compute_mvp_score(
                    kills=per_player_kills,
                    damage=None,
                    revives=0,
                    survival_seconds=0,
                    clutches=0,
                    headshots=0,
                ),
                "is_demo": True,
                "source": "demo",
            })
    if not players_data:
        return JSONResponse({"error": "No players found"}, status_code=404)
    mvp = max(players_data, key=lambda x: x["mvp_score"])
    return JSONResponse({"data": mvp, "demo": True})

@app.get("/api/v1/matches")
async def get_matches():
    # Phase 13: hardcoded demo match card — gated behind DEMO_MODE.
    if not DEMO_MODE:
        return _demo_unavailable()
    return JSONResponse({"data": MATCH_STATE["matches"]})

@app.get("/api/v1/matches/{match_id}/player-stats")
async def get_match_player_stats_endpoint(match_id: str):
    """Per-player stat lines for one match.

    Feeds the MVP / Best IGL / Finals MVP graphics templates
    (frontend PlayerStat contract). Returns ONLY what a game-stats feed
    has genuinely recorded via MatchService.record_player_stats — the
    table is empty until such a feed exists, so the boards render their
    honest empty states. Per-player damage, ratings, knocks etc. are
    NEVER derived from team totals here (see docs/GRAPHICS.md honesty
    rule). Any DB/schema failure degrades to an empty list, never a 500.
    """
    try:
        from app.services import match_service as _ms
        from app.db.models.player import Player
        from app.db.models.team import Team

        MatchPlayerStats = _ms.MatchPlayerStats  # noqa: F841 - documents source
        async with get_db_context() as db:
            svc = _ms.MatchService(db)
            stats = await svc.get_match_player_stats(match_id)
            player_ids = [s.player_id for s in stats]
            players = {}
            if player_ids:
                res = await db.execute(
                    select(Player).where(Player.id.in_(player_ids))
                )
                players = {p.id: p for p in res.scalars().all()}
            team_ids = {s.team_id for s in stats} | {
                p.team_id for p in players.values() if p.team_id
            }
            teams = {}
            if team_ids:
                res = await db.execute(
                    select(Team).where(Team.id.in_(team_ids))
                )
                teams = {t.id: t for t in res.scalars().all()}
            out = []
            for s in stats:
                p = players.get(s.player_id)
                t = teams.get(s.team_id)
                out.append(
                    {
                        "player_id": s.player_id,
                        "player_name": (p.ign or p.name) if p else s.player_id,
                        "team_id": s.team_id,
                        "team_tag": (t.tag or t.name) if t else s.team_id,
                        "team_color": (
                            (t.colors or {}).get("primary") if t else None
                        ),
                        "finishes": s.kills,
                        # Honest damage (Phase 21): the persisted value
                        # exactly as recorded from the agent/feed layer;
                        # None (unknown) serialises as null → the board
                        # renders "—". Never estimated from kills.
                        "damage": s.damage_dealt,
                        "survival_seconds": s.survived_time_seconds,
                        "knocks": None,  # not in the feed schema yet
                        # Phase 21 (T5b): healing + grenade counters from
                        # the agent utility trackers (record_player_stats).
                        "healing": getattr(s, "healing", 0) or 0,
                        "grenades_used": getattr(s, "grenades_used", 0) or 0,
                        # Canonical MVP score (single producer:
                        # mvp_calculator via MatchService). `rating`
                        # is the long-standing alias the boards read;
                        # `mvp_score` is the explicit field.
                        "rating": s.mvp_score,
                        "mvp_score": s.mvp_score,
                        "headshots": s.headshots,
                        "revives": s.revives_performed,
                        "clutches": getattr(s, "clutches", 0),
                        "is_mvp": bool(getattr(s, "is_mvp", False)),
                        "igl_rating": None,  # needs a dedicated IGL feed
                        "finals_mvp_rating": None,
                        "is_igl": bool(p and (p.role or "").upper() == "IGL"),
                        "avg_points": None,  # tournament-wide feed only
                        "wwcd": None,  # joined from team frames client-side
                        "top_5s": None,
                        "avg_finishes": None,
                    }
                )
        return JSONResponse({"data": {"match_id": match_id, "players": out}})
    except Exception as e:  # honest degrade: empty, never invented
        logger.warning(
            "player-stats unavailable", match_id=match_id, error=str(e)
        )
        return JSONResponse({"data": {"match_id": match_id, "players": []}})

@app.get("/api/v1/matches/live")
async def get_live_match():
    # Phase 13: hardcoded demo match card — gated behind DEMO_MODE.
    if not DEMO_MODE:
        return _demo_unavailable()
    return JSONResponse({"data": MATCH_STATE["matches"][0]})

@app.get("/api/v1/tournaments")
async def get_tournaments():
    # Phase 13: hardcoded demo tournament card — gated behind DEMO_MODE.
    if not DEMO_MODE:
        return _demo_unavailable()
    return JSONResponse({"data": [{"id": "tour1", "name": "BGIS 2024", "status": "LIVE"}]})

@app.get("/api/v1/analytics/standings")
async def get_standings():
    # Phase 13: scores the hardcoded demo dataset — gated behind DEMO_MODE.
    if not DEMO_MODE:
        return _demo_unavailable()
    sorted_teams = sorted(MATCH_STATE["teams"], key=lambda x: x["total_pts"], reverse=True)
    return JSONResponse({"data": sorted_teams})

@app.get("/api/v1/analytics/kill_feed")
@app.get("/api/v1/analytics/kill-feed")
async def get_kill_feed():
    # Phase 13: MATCH_STATE["kill_feed"] is filled only by the demo match
    # simulator — gated behind DEMO_MODE.
    if not DEMO_MODE:
        return _demo_unavailable()
    return JSONResponse({"data": list(MATCH_STATE["kill_feed"])})

@app.get("/api/v1/analytics/summary")
async def get_analytics_summary():
    # Phase 13: aggregates the hardcoded demo dataset — gated behind
    # DEMO_MODE and labelled demo. Phase 21 (W2): the kills-derived
    # damage estimate was deleted — the demo dataset carries no real
    # damage, so total_damage is honestly null (production damage
    # flows only from persisted agent/feed stats, see player-stats).
    if not DEMO_MODE:
        return _demo_unavailable()
    total_kills = sum(t["kills"] for t in MATCH_STATE["teams"])
    teams_alive = sum(1 for t in MATCH_STATE["teams"] if t["alive"])
    return JSONResponse({
        "data": {
            "total_kills": total_kills,
            "total_damage": None,
            "avg_kills_per_team": round(total_kills / max(1, len(MATCH_STATE["teams"])), 1),
            "most_kills_team": max(MATCH_STATE["teams"], key=lambda x: x["kills"])["tag"],
            "zone_phase": 4,
            "teams_alive": teams_alive,
            "match_time_seconds": 945,
            "is_demo": True,
            "source": "demo",
        },
        "demo": True,
    })

@app.post("/api/v1/broadcast/switch")
async def switch_camera(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    team_id = data.get("team_id")
    if team_id:
        decision = {
            "action": "SWITCH_POV",
            "target_team": team_id,
            "reason": {"event_type": "manual_override", "description": "Manual override from dashboard"},
            "timestamp": time.time()
        }
        MATCH_STATE["director_state"] = decision
        await manager.broadcast({"type": "director_state", "data": decision})
        await manager.broadcast({"type": "scene_switch", "data": decision})
        return JSONResponse({"status": "success", "message": f"Switched to {team_id}"})
    return JSONResponse({"status": "error", "message": "Missing team_id"}, status_code=400)

@app.post("/api/v1/broadcast/replay")
async def trigger_replay(request: Request, _user: TokenPayload = Depends(require_admin)):
    """Trigger an EVENT replay through the real replay stack (H4).

    This used to return "Replay triggered" while doing nothing. Now it
    plays, via app/services/replay (replay_service.play_mark /
    play_clip, which re-emit the segment as WS ``replay.play``):
      * the mark named by ``mark_id`` in the JSON body, or
      * the ``from_ts``/``to_ts`` epoch-second window in the body, or
      * the most recent saved replay mark (the frontend's body-less
        Replay button).
    With no mark and no window there is nothing real to play: an honest
    409 error, never a fake success.
    """
    body: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = {}

    mark_id = body.get("mark_id")
    from_ts = body.get("from_ts")
    to_ts = body.get("to_ts")
    try:
        if mark_id:
            clip = await replay_service.play_mark(str(mark_id))
        elif from_ts is not None and to_ts is not None:
            clip = await replay_service.play_clip(float(from_ts), float(to_ts))
        else:
            marks = replay_service.list_marks()
            if not marks:
                return JSONResponse(
                    {
                        "status": "error",
                        "message": (
                            "No replay available: no saved replay marks exist "
                            "yet and no time window was provided. Capture a "
                            "mark first (POST /api/v1/replay/capture) — "
                            "nothing was played."
                        ),
                    },
                    status_code=409,
                )
            clip = await replay_service.play_mark(marks[0].id)
    except KeyError:
        return JSONResponse(
            {
                "status": "error",
                "message": f"No replay mark with id {mark_id!r}; nothing was played.",
            },
            status_code=404,
        )
    except (TypeError, ValueError) as e:
        return JSONResponse(
            {"status": "error", "message": f"Invalid replay window: {e}"},
            status_code=400,
        )
    return JSONResponse(
        {
            "status": "success",
            "message": "Replay played",
            "data": clip.model_dump(),
        }
    )

@app.get("/api/v1/broadcast/status")
async def get_broadcast_status():
    return JSONResponse({"data": MATCH_STATE["director_state"]})

@app.post("/api/v1/broadcast/production-mode")
async def set_production_mode(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    for key, value in data.items():
        if key in MATCH_STATE["modes_state"]:
            MATCH_STATE["modes_state"][key] = value
    await manager.broadcast({"type": "modes_state", "data": MATCH_STATE["modes_state"]})
    return JSONResponse({"status": "success", "modes": MATCH_STATE["modes_state"]})

@app.get("/api/v1/commentary/latest")
async def get_latest_commentary():
    return JSONResponse({"data": list(MATCH_STATE["commentary_log"])})

@app.get("/api/v1/highlights")
async def get_highlights():
    """Highlights from the real replay stack (H4).

    This used to always return {"data": []}. It now serves
    replay_service.get_highlights() — scored moments computed from the
    replay buffer's recorded events. When the buffer has produced no
    qualifying events the list is honestly empty and the payload says
    why. Known caller: frontend/app/commentary/page.tsx (reads `data`).
    In DEMO_MODE the buffer is fed by the demo simulator, so the block
    is marked "source": "demo".
    """
    try:
        highlights = replay_service.get_highlights(limit=50)
    except Exception as e:
        logger.warning("highlights unavailable", error=str(e))
        return JSONResponse(
            {
                "data": [],
                "source": "replay_buffer",
                "detail": f"Highlights unavailable: {e}",
            },
            status_code=503,
        )
    payload: dict = {
        "data": [h.model_dump() for h in highlights],
        "source": "demo" if DEMO_MODE else "replay_buffer",
    }
    if not highlights:
        payload["detail"] = (
            "No highlights yet: the replay buffer has no qualifying "
            "recorded events. Highlights are computed from real match "
            "events only and are never synthesised."
        )
    return JSONResponse(payload)

@app.get("/api/v1/admin/system")
async def get_admin_system(_user: TokenPayload = Depends(require_admin)):
    # C6: same honest payload as /api/v1/system/status — the fabricated
    # health block appears only in DEMO_MODE, marked "source": "demo".
    return JSONResponse(_system_status_payload())

@app.get("/api/v1/admin/agents")
@app.get("/api/v1/agents")
async def get_admin_agents(_user: TokenPayload = Depends(require_admin)):
    # C6: agents_state is hardcoded demo data. DEMO_MODE off → honest
    # empty (there is no real per-agent telemetry registry); on → the
    # demo rows, each marked "source": "demo".
    if not DEMO_MODE:
        return JSONResponse({
            "data": [],
            "source": "none",
            "detail": (
                "No real agent telemetry is available: the agent list is "
                "demo data and is only served with DEMO_MODE=1."
            ),
        })
    return JSONResponse({
        "data": _demo_marked(MATCH_STATE["agents_state"]),
        "source": "demo",
    })

# --- NEW REST ENDPOINTS ---
@app.get("/api/v1/devices")
async def get_devices(_user: TokenPayload = Depends(require_admin)):
    # C6: DEVICES is a hardcoded demo list. Same gating as agents above.
    if not DEMO_MODE:
        return JSONResponse({
            "data": [],
            "source": "none",
            "detail": (
                "No real device registry is connected: the device list "
                "is demo data and is only served with DEMO_MODE=1."
            ),
        })
    return JSONResponse({
        "data": _demo_marked(MATCH_STATE["devices"]),
        "source": "demo",
    })

@app.get("/api/v1/agent-assignments")
async def get_agent_assignments(_user: TokenPayload = Depends(require_admin)):
    # C6: assignments point at the demo device list, so they are served
    # only in DEMO_MODE (marked demo); otherwise an honest empty map.
    if not DEMO_MODE:
        return JSONResponse({
            "data": {},
            "source": "none",
            "detail": (
                "Agent assignments target the demo device list and are "
                "only served with DEMO_MODE=1."
            ),
        })
    return JSONResponse({
        "data": _demo_marked(MATCH_STATE["agent_assignments"]),
        "source": "demo",
    })

@app.get("/api/v1/agent-logs")
async def get_agent_logs(_user: TokenPayload = Depends(require_admin)):
    # Agent logs are written only by the DEMO_MODE chatter simulator;
    # with DEMO_MODE off the buffer is empty (honest). In DEMO_MODE the
    # block is marked demo.
    payload: dict = {"data": list(MATCH_STATE["agent_logs"])}
    if DEMO_MODE:
        payload["source"] = "demo"
    return JSONResponse(payload)

@app.post("/api/v1/scrims/start")
async def start_scrim(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    ack = await handle_action({"action": "start_scrim", **data}, user=_user)
    # H4: mirror the action's real outcome instead of unconditional
    # success. start_scrim mutates scrim_state, so its ack is success;
    # any error ack is surfaced as-is.
    if ack and ack.get("status") != "success":
        return JSONResponse(
            {"status": "error", "message": ack.get("message", "Scrim was not started.")},
            status_code=400,
        )
    return JSONResponse({"status": "success", "scrim": MATCH_STATE["scrim_state"]})

@app.post("/api/v1/content/generate-recap")
async def generate_recap(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    ack = await handle_action({"action": "generate_recap", **data}, user=_user)
    # H4: generate_recap is a DEMO_MODE-only simulation. With DEMO_MODE
    # off the action errors and so does this endpoint (503) — it must
    # not claim a render job exists.
    if not ack or ack.get("status") != "success":
        return JSONResponse(
            {
                "status": "error",
                "message": (ack or {}).get(
                    "message", "Recap was not generated."
                ),
            },
            status_code=503,
        )
    return JSONResponse({
        "status": "success",
        "source": "demo",
        "jobs": _demo_marked(list(MATCH_STATE["render_jobs"])),
    })