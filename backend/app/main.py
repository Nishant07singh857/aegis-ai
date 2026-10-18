import asyncio
import json
import random
import time
import uuid
import structlog
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
from app.core.security import security_manager
from app.api.deps import (
    TokenPayload,
    authenticate_websocket,
    principal_role,
    require_admin,
    verify_admin_login,
)
from app.api.v1.teams.demo_data import DEMO_TEAMS
from app.api.v1.teams.router import router as teams_router
from app.api.v1.stream.router import router as stream_router
from app.api.v1.director.router import router as director_router
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
    "matches": [{"id": "m1", "title": "BGIS Semifinals - Match 4", "map": "Erangel", "status": "LIVE"}],
    "kill_feed": [],
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
    "commentary_log": [],
    # --- NEW STATE ---
    "devices": DEVICES,
    "agent_assignments": {
        "vision": {"device_id": "gpu1", "device_name": "Node-GPU-1", "team_id": "t1", "team_name": "SouL"},
        "commentary": {"device_id": "gpu2", "device_name": "Node-GPU-2", "team_id": None, "team_name": None},
        "director": {"device_id": "cpu1", "device_name": "Node-CPU-1", "team_id": None, "team_name": None},
        "fight": {"device_id": "gpu1", "device_name": "Node-GPU-1", "team_id": None, "team_name": None},
        "economy": {"device_id": "cpu1", "device_name": "Node-CPU-1", "team_id": None, "team_name": None},
    },
    "agent_logs": [],
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
    "render_jobs": [
        {"id": "j1", "title": "Soul Wipe - YouTube Shorts Format", "status": "RENDERING", "progress": 72, "format": "YouTube Shorts"},
        {"id": "j2", "title": "Mortal Clutch - TikTok Format", "status": "PENDING", "progress": 0, "format": "TikTok"},
        {"id": "j3", "title": "Match 4 - Cinematic Recap", "status": "COMPLETED", "progress": 100, "format": "Full Broadcast"},
    ],
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
        await websocket.send_json({"type": "standings", "data": MATCH_STATE["teams"]})
        await websocket.send_json({"type": "kill_feed_init", "data": MATCH_STATE["kill_feed"]})
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
        await websocket.send_json({"type": "system_health", "data": MATCH_STATE["system_health"]})
        await websocket.send_json({"type": "agents_state", "data": MATCH_STATE["agents_state"]})
        await websocket.send_json({"type": "modes_state", "data": MATCH_STATE["modes_state"]})
        # New state
        await websocket.send_json({"type": "devices_state", "data": MATCH_STATE["devices"]})
        await websocket.send_json({"type": "agent_assignments", "data": MATCH_STATE["agent_assignments"]})
        await websocket.send_json({"type": "agent_logs", "data": MATCH_STATE["agent_logs"]})
        await websocket.send_json({"type": "scrim_state", "data": MATCH_STATE["scrim_state"]})
        await websocket.send_json({"type": "render_jobs", "data": MATCH_STATE["render_jobs"]})
        await websocket.send_json({"type": "social_config", "data": MATCH_STATE["social_config"]})
        await websocket.send_json({"type": "training_models", "data": MATCH_STATE["training_models"]})

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

# ==========================================
# GLOBAL SERVICES
# ==========================================
event_bus = EventBus()
director_engine = DirectorAIEngine()
commentary_engine = get_commentary_engine()
simulation_task = None
chatter_task = None


def _active_agent_count() -> int:
    """Agents THIS process reports as running (same source as the Control
    Room 'Agents' card). Pushed into the Redis node hash on every cluster
    heartbeat so the cluster view shows per-node load."""
    try:
        return sum(1 for a in MATCH_STATE.get("agents_state", []) if a.get("status") == "active")
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
    if principal_role(user) not in ("admin", "operator"):
        await manager.broadcast({
            "type": "action_ack",
            "data": {
                "action": action,
                "status": "unauthorized",
                "message": "Insufficient role: this control action requires operator or admin.",
            },
        })
        logger.warning(f"Rejected WS action from insufficient role: {action} user={user.username}")
        return

    if action == "assign_agent":
        agent_id = data.get("agent_id")
        device_id = data.get("device_id")
        team_id = data.get("team_id")

        device = next((d for d in MATCH_STATE["devices"] if d["id"] == device_id), None)
        team = next((t for t in MATCH_STATE["teams"] if t["id"] == team_id), None)

        if agent_id and device:
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
            
            await manager.broadcast({"type": "agent_assignments", "data": MATCH_STATE["agent_assignments"]})
            await manager.broadcast({"type": "devices_state", "data": MATCH_STATE["devices"]})
            await manager.broadcast({
                "type": "action_ack",
                "data": {"action": "assign_agent", "status": "success", "agent_id": agent_id, "device": device["name"]}
            })

    elif action == "lock_pov":
        team_id = data.get("team_id")
        team = next((t for t in MATCH_STATE["teams"] if t["id"] == team_id), None)
        if team:
            decision = {
                "action": "LOCK_POV",
                "target_team": team_id,
                "current_pov": team_id,
                "reason": {"event_type": "manual_lock", "description": f"Director locked to {team['name']}"},
                "timestamp": time.time()
            }
            MATCH_STATE["director_state"] = decision
            await manager.broadcast({"type": "director_state", "data": decision})
            await manager.broadcast({"type": "action_ack", "data": {"action": "lock_pov", "status": "success", "team": team["name"]}})

    elif action == "start_scrim":
        lobby_id = f"BGIS-{random.randint(1000, 9999)}"
        map_name = data.get("map", "Erangel")
        MATCH_STATE["scrim_state"] = {
            "active": True,
            "lobby_id": lobby_id,
            "teams": [{"name": t["name"], "tag": t["tag"], "kills": 0, "placement": 0, "pts": 0} for t in MATCH_STATE["teams"][:8]],
            "map": map_name,
            "status": "lobby_open"
        }
        await manager.broadcast({"type": "scrim_state", "data": MATCH_STATE["scrim_state"]})
        await manager.broadcast({"type": "action_ack", "data": {"action": "start_scrim", "status": "success", "lobby_id": lobby_id}})

    elif action == "generate_recap":
        fmt = data.get("format", "YouTube Shorts")
        job_id = str(uuid.uuid4())[:6]
        new_job = {"id": job_id, "title": f"Match 4 Recap — {fmt}", "status": "RENDERING", "progress": 0, "format": fmt}
        MATCH_STATE["render_jobs"].append(new_job)
        await manager.broadcast({"type": "render_jobs", "data": MATCH_STATE["render_jobs"]})
        await manager.broadcast({"type": "action_ack", "data": {"action": "generate_recap", "status": "success", "job_id": job_id}})
        # Simulate progress
        asyncio.create_task(simulate_render_progress(job_id))

    elif action == "restart_node":
        node_name = data.get("node")
        for d in MATCH_STATE["devices"]:
            if d["name"] == node_name or d["id"] == data.get("node_id"):
                d["status"] = "restarting"
        await manager.broadcast({"type": "devices_state", "data": MATCH_STATE["devices"]})
        asyncio.create_task(simulate_node_restart(node_name or data.get("node_id")))

    elif action == "toggle_node":
        node_id = data.get("node_id")
        for d in MATCH_STATE["devices"]:
            if d["id"] == node_id:
                d["status"] = "offline" if d["status"] == "online" else "online"
                await manager.broadcast({"type": "devices_state", "data": MATCH_STATE["devices"]})
                await manager.broadcast({"type": "action_ack", "data": {"action": "toggle_node", "status": "success", "node": d["name"], "new_status": d["status"]}})
                break

    elif action == "test_post":
        platform = data.get("plat", data.get("platform", "Unknown"))
        await manager.broadcast({"type": "action_ack", "data": {"action": "test_post", "status": "success", "platform": platform}})

    elif action == "toggle_autopost":
        platform = data.get("platform")
        if platform and platform in MATCH_STATE["social_config"]:
            current = MATCH_STATE["social_config"][platform]["auto_post"]
            MATCH_STATE["social_config"][platform]["auto_post"] = not current
            await manager.broadcast({"type": "social_config", "data": MATCH_STATE["social_config"]})
            await manager.broadcast({"type": "action_ack", "data": {"action": "toggle_autopost", "platform": platform, "auto_post": not current}})

    elif action == "upload_sponsor":
        await manager.broadcast({"type": "action_ack", "data": {"action": "upload_sponsor", "status": "success", "file": "Sponsor_Asset.png"}})

    elif action == "revoke_key":
        new_key = f"sk_live_{''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=24))}"
        await manager.broadcast({"type": "action_ack", "data": {"action": "revoke_key", "status": "success", "new_key": new_key}})

    elif action == "stop_training":
        model_name = data.get("model")
        for m in MATCH_STATE["training_models"]:
            if m["name"] == model_name:
                m["status"] = "Stopped"
                m["active"] = False
        await manager.broadcast({"type": "training_models", "data": MATCH_STATE["training_models"]})
        await manager.broadcast({"type": "action_ack", "data": {"action": "stop_training", "status": "success", "model": model_name}})

    elif action == "post_now":
        platform = data.get("platform")
        await manager.broadcast({"type": "action_ack", "data": {"action": "post_now", "status": "success", "platform": platform}})

    elif action == "create_tournament":
        name = data.get("name", "New Tournament")
        await manager.broadcast({"type": "action_ack", "data": {"action": "create_tournament", "status": "success", "name": name}})

    elif action == "clip_highlight":
        clip_id = data.get("clip_id")
        await manager.broadcast({"type": "action_ack", "data": {"action": "clip_highlight", "status": "success", "clip_id": clip_id}})

    elif action == "generate_all_highlights":
        await manager.broadcast({"type": "action_ack", "data": {"action": "generate_all_highlights", "status": "success"}})

    elif action == "toggle_broadcaster_seat":
        seat = data.get("seat")
        await manager.broadcast({"type": "action_ack", "data": {"action": "toggle_broadcaster_seat", "status": "success", "seat": seat}})

    elif action == "test_webhook":
        await manager.broadcast({"type": "action_ack", "data": {"action": "test_webhook", "status": "success"}})

async def simulate_render_progress(job_id: str):
    """Simulate a render job progressing from 0 to 100%."""
    for progress in range(5, 101, 5):
        await asyncio.sleep(0.5)
        for job in MATCH_STATE["render_jobs"]:
            if job["id"] == job_id:
                job["progress"] = progress
                if progress >= 100:
                    job["status"] = "COMPLETED"
        await manager.broadcast({"type": "render_jobs", "data": MATCH_STATE["render_jobs"]})

async def simulate_node_restart(node_identifier: str):
    """Simulate a node restarting and coming back online."""
    await asyncio.sleep(3)
    for d in MATCH_STATE["devices"]:
        if d["name"] == node_identifier or d["id"] == node_identifier:
            d["status"] = "online"
            d["load"] = random.randint(10, 30)
    await manager.broadcast({"type": "devices_state", "data": MATCH_STATE["devices"]})
    await manager.broadcast({"type": "action_ack", "data": {"action": "restart_node", "status": "success", "node": node_identifier}})

# ==========================================
# SIMULATOR TASK — Match Events
# ==========================================
async def match_simulator():
    """Generates realistic match events and feeds them to the real Director AI."""
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
            if len(MATCH_STATE["commentary_log"]) > 10:
                MATCH_STATE["commentary_log"].pop(0)
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
        if len(MATCH_STATE["kill_feed"]) > 10:
            MATCH_STATE["kill_feed"].pop(0)
            
        MATCH_STATE["system_health"]["events_per_sec"] = random.randint(100, 150)
        MATCH_STATE["system_health"]["cpu_usage"] = random.randint(40, 60)
        
        # Update device loads dynamically
        for d in MATCH_STATE["devices"]:
            if d["status"] == "online":
                d["load"] = min(99, max(5, d["load"] + random.randint(-5, 8)))
            
        await manager.broadcast({"type": "kill", "data": kill_event})
        await manager.broadcast({"type": "standings", "data": MATCH_STATE["teams"]})
        await manager.broadcast({"type": "system_health", "data": MATCH_STATE["system_health"]})
        await manager.broadcast({"type": "devices_state", "data": MATCH_STATE["devices"]})
        
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
        }
        
        MATCH_STATE["agent_logs"].append(log_entry)
        if len(MATCH_STATE["agent_logs"]) > 50:
            MATCH_STATE["agent_logs"].pop(0)
        
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


# ==========================================
# FASTAPI APP & LIFESPAN
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("AEGIS AI Starting...")
    
    try:
        await init_db()
    except Exception as e:
        logger.warning(f"Database initialization failed, continuing with in-memory state: {e}")

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
    await _seed_first_admin()
        
    try:
        await event_bus.connect()
    except Exception as e:
        logger.warning(f"EventBus connection failed, continuing with in-memory broadcasting: {e}")

    # Phase 10: replay buffer fills from the bus; feed adapter activates
    # (after connect so attach() sees a running bus).
    replay_service.buffer.subscribe_to(event_bus)
    await feed_service.attach(event_bus)

    # Phase 7 / W3: register this process in the distributed cluster registry
    # (Redis-backed, standalone fallback when Redis is unreachable).
    await node_registry.start(agent_count_provider=_active_agent_count)
        
    global simulation_task, chatter_task
    simulation_task = asyncio.create_task(match_simulator())
    chatter_task = asyncio.create_task(agent_chatter_simulator())
    
    logger.info("✅ AEGIS AI Ready")
    
    yield
    
    logger.info("AEGIS AI Shutting down...")
    if simulation_task:
        simulation_task.cancel()
    if chatter_task:
        chatter_task.cancel()
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

# Phase 5: automated live streaming (operator-gated start/stop, authenticated status).
app.include_router(stream_router, prefix="/api/v1", tags=["Stream"])

# Phase 6: director control API (role-gated; see app/api/v1/director).
app.include_router(director_router, prefix="/api/v1", tags=["Director"])

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
# Phase 11: analysis product.
app.include_router(analysis_router, prefix="/api/v1", tags=["Analysis"])
app.include_router(dossiers_router, prefix="/api/v1", tags=["Dossiers"])

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
       JWT carrying the user's DB role. This is how the seeded first admin
       (ADMIN_EMAIL/ADMIN_PASSWORD) logs in.

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
    # 2) DB-backed users with roles.
    db_user = await _authenticate_db_user(form_data.username, form_data.password)
    if db_user is not None:
        role = (db_user.role or "viewer").strip().lower()
        if role not in ("admin", "operator", "viewer"):
            role = "viewer"
        token = security_manager.create_access_token(
            user_id=str(db_user.id),
            username=db_user.username,
            is_superuser=bool(db_user.is_superuser) or role == "admin",
            scopes=[role],
            role=role,
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
    ws_user = authenticate_websocket(
        websocket,
        token=websocket.query_params.get("token"),
    )
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue
            try:
                payload = json.loads(data)
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
    return JSONResponse({
        "status": "online",
        "health": MATCH_STATE["system_health"],
        "modes": MATCH_STATE["modes_state"],
        "event_bus": "connected" if event_bus._running else "disconnected",
    })

@app.get("/api/v1/players")
async def get_players():
    players_data = []
    for team in MATCH_STATE["teams"]:
        for player in team["players"]:
            players_data.append({
                "id": f"{team['id']}_{player}",
                "name": player,
                "team": team["name"],
                "role": "Assaulter",
                "kills": team["kills"] // 4,
                "damage": team["kills"] * 100,
                "mvp_score": (team["kills"] * 10) + 50
            })
    return JSONResponse({"data": players_data})

@app.get("/api/v1/players/mvp")
async def get_mvp():
    players_data = []
    for team in MATCH_STATE["teams"]:
        for player in team["players"]:
            players_data.append({
                "id": f"{team['id']}_{player}",
                "name": player,
                "team": team["name"],
                "role": "Assaulter",
                "kills": team["kills"] // 4,
                "damage": team["kills"] * 100,
                "mvp_score": (team["kills"] * 10) + 50
            })
    if not players_data:
        return JSONResponse({"error": "No players found"}, status_code=404)
    mvp = max(players_data, key=lambda x: x["mvp_score"])
    return JSONResponse({"data": mvp})

@app.get("/api/v1/matches")
async def get_matches():
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
                        "damage": s.damage_dealt,
                        "survival_seconds": s.survived_time_seconds,
                        "knocks": None,  # not in the feed schema yet
                        "rating": s.mvp_score,
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
    return JSONResponse({"data": MATCH_STATE["matches"][0]})

@app.get("/api/v1/tournaments")
async def get_tournaments():
    return JSONResponse({"data": [{"id": "tour1", "name": "BGIS 2024", "status": "LIVE"}]})

@app.get("/api/v1/analytics/standings")
async def get_standings():
    sorted_teams = sorted(MATCH_STATE["teams"], key=lambda x: x["total_pts"], reverse=True)
    return JSONResponse({"data": sorted_teams})

@app.get("/api/v1/analytics/kill_feed")
@app.get("/api/v1/analytics/kill-feed")
async def get_kill_feed():
    return JSONResponse({"data": MATCH_STATE["kill_feed"]})

@app.get("/api/v1/analytics/summary")
async def get_analytics_summary():
    total_kills = sum(t["kills"] for t in MATCH_STATE["teams"])
    teams_alive = sum(1 for t in MATCH_STATE["teams"] if t["alive"])
    return JSONResponse({
        "data": {
            "total_kills": total_kills,
            "total_damage": total_kills * 120,
            "avg_kills_per_team": round(total_kills / max(1, len(MATCH_STATE["teams"])), 1),
            "most_kills_team": max(MATCH_STATE["teams"], key=lambda x: x["kills"])["tag"],
            "zone_phase": 4,
            "teams_alive": teams_alive,
            "match_time_seconds": 945
        }
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
async def trigger_replay(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({"status": "success", "message": "Replay triggered"})

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
    return JSONResponse({"data": MATCH_STATE["commentary_log"]})

@app.get("/api/v1/highlights")
async def get_highlights():
    return JSONResponse({"data": []})

@app.get("/api/v1/admin/system")
async def get_admin_system(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({
        "status": "online",
        "health": MATCH_STATE["system_health"],
        "modes": MATCH_STATE["modes_state"],
        "event_bus": "connected" if event_bus._running else "disconnected",
    })

@app.get("/api/v1/admin/agents")
@app.get("/api/v1/agents")
async def get_admin_agents(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({"data": MATCH_STATE["agents_state"]})

# --- NEW REST ENDPOINTS ---
@app.get("/api/v1/devices")
async def get_devices(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({"data": MATCH_STATE["devices"]})

@app.get("/api/v1/agent-assignments")
async def get_agent_assignments(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({"data": MATCH_STATE["agent_assignments"]})

@app.get("/api/v1/agent-logs")
async def get_agent_logs(_user: TokenPayload = Depends(require_admin)):
    return JSONResponse({"data": MATCH_STATE["agent_logs"]})

@app.post("/api/v1/scrims/start")
async def start_scrim(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    await handle_action({"action": "start_scrim", **data}, user=_user)
    return JSONResponse({"status": "success", "scrim": MATCH_STATE["scrim_state"]})

@app.post("/api/v1/content/generate-recap")
async def generate_recap(request: Request, _user: TokenPayload = Depends(require_admin)):
    data = await request.json()
    await handle_action({"action": "generate_recap", **data}, user=_user)
    return JSONResponse({"status": "success", "jobs": MATCH_STATE["render_jobs"]})