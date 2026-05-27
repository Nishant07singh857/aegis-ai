"""
AEGIS AI — FULL PRODUCTION INTEGRATION v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This is the MASTER script that wires ALL modules together:

  [Video/Camera Feed]
       ↓
  [YOLO Vision AI]  → detects Killfeed box
       ↓
  [EasyOCR]         → reads real player names from box
       ↓
  [Event Extractor] → converts to structured GameEvent
       ↓
  [Redis Event Bus] → broadcasts to ALL subscribers
       ↓
  ┌────────────────────────────────────────────────┐
  │  [Director AI]   → decides which team to show  │
  │  [Team Agents]   → track each team's status    │
  │  [Commentary AI] → generates Hinglish voice    │
  └────────────────────────────────────────────────┘
       ↓
  [OBS Manager]     → switches camera / triggers replay

Works on: Mac (MPS), Windows (CUDA), Linux (CUDA/CPU)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import cv2
import sys
import time
from pathlib import Path
from ultralytics import YOLO
import structlog

# Fix import path for running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.event_bus import EventBus, EventPriority
from app.production.obs_manager import OBSManager
from app.vision.post_processors.event_extractor import EventExtractor
from app.vision.detectors.kill_feed_ocr import KillFeedOCR
from app.intelligence.director_ai import DirectorAIEngine, BroadcastEvent, EventPriority as DirPriority
from app.intelligence.commentary_engine import CommentaryEngine

logger = structlog.get_logger("aegis.main")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TEAM REGISTRY (Simulating 16 BGIS Teams)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BGIS_TEAMS = {
    "SoulMortal": "SOUL", "ClutchGod": "SOUL", "Viper_Soul": "SOUL",
    "GodLJonathan": "GODL", "Zgod": "GODL", "Mavi": "GODL",
    "Sc0ut": "IND", "Mortal": "IND", "Neyoo": "IND",
    "Owais": "OR", "Menace": "OR", "Smokie": "OR",
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIMPLE TEAM STATE (lightweight agent per team)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TeamAgent:
    """Lightweight agent tracking one team's live state."""
    def __init__(self, team_id: str):
        self.team_id = team_id
        self.kills = 0
        self.knocks = 0
        self.alive_count = 4
        self.is_fighting = False
        self.last_event_time = 0.0
        self.priority_score = 0.0

    def on_kill(self, killer: str, victim: str):
        self.kills += 1
        self.is_fighting = True
        self.last_event_time = time.time()
        self.priority_score = min(100.0, self.priority_score + 25.0)
        print(f"   [Agent:{self.team_id}] Kill recorded! Total: {self.kills} kills")

    def on_knock(self, killer: str, victim: str):
        self.knocks += 1
        self.is_fighting = True
        self.last_event_time = time.time()
        self.priority_score = min(100.0, self.priority_score + 15.0)
        print(f"   [Agent:{self.team_id}] Knock recorded! Total: {self.knocks} knocks")

    def decay_priority(self):
        """Slowly reduce priority if no recent action."""
        if time.time() - self.last_event_time > 5.0:
            self.priority_score = max(0.0, self.priority_score - 2.0)
            self.is_fighting = False

    def to_broadcast_event(self) -> BroadcastEvent:
        import uuid
        prio = DirPriority.HIGH if self.is_fighting else DirPriority.LOW
        return BroadcastEvent(
            id=str(uuid.uuid4())[:8],
            team_id=self.team_id,
            type="fight" if self.is_fighting else "looting",
            priority=prio,
            confidence=0.92 if self.is_fighting else 0.70,
            emotion_score=self.priority_score,
            timestamp=time.time(),
            description=f"{self.team_id}: {self.kills}K/{self.knocks}Kn"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VISION AGENT — runs YOLO + OCR on each frame
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def vision_agent(
    event_bus: EventBus,
    extractor: EventExtractor,
    team_agents: dict,
    video_source=None
):
    base_dir = Path(__file__).parent.parent
    model_path = base_dir / "dataset" / "yolo_runs" / "aegis_bgmi_v1" / "weights" / "best.pt"
    default_video = base_dir / "dataset" / "videos" / "test_clip.mp4"

    source = video_source if video_source is not None else str(default_video)

    print(f"\n[Vision Agent] 👁️  Loading YOLOv8 from {model_path.name}...")
    model = YOLO(str(model_path))

    print(f"[Vision Agent] 🎥 Connecting to source: {source}")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        print(f"[Vision Agent] ❌ Cannot open source: {source}")
        return

    ocr = KillFeedOCR()
    frame_count = 0
    simulated_kill_sent = False
    print("[Vision Agent] ✅ Live processing started!\n")

    while cap.isOpened() and frame_count < 200:
        ret, frame = cap.read()
        if not ret:
            break

        # YOLO inference
        results = model(frame, verbose=False, imgsz=640, conf=0.45)
        kill_detected = False

        for result in results:
            for box in result.boxes:
                cls_name = model.names[int(box.cls[0])]
                bbox = box.xyxy[0].tolist()

                # ── REAL OCR on Killfeed box ─────────────────────────
                if cls_name == "killfeed":
                    kill_entry = ocr.read_from_frame(frame, bbox)
                    if kill_entry and kill_entry.is_valid:
                        kill_detected = True
                        team_id = BGIS_TEAMS.get(kill_entry.killer_name, "UNKNOWN")

                        print("\n[Vision→OCR] 🔥 REAL KILL DETECTED!")
                        print(f"   Killer: {kill_entry.killer_name} (Team {team_id})")
                        print(f"   Victim: {kill_entry.victim_name}")
                        print(f"   Type:   {kill_entry.kill_type.upper()}")
                        print(f"   OCR raw text: '{kill_entry.raw_text}'")

                        # Update team agent
                        if team_id in team_agents:
                            if kill_entry.kill_type == "knock":
                                team_agents[team_id].on_knock(kill_entry.killer_name, kill_entry.victim_name)
                            else:
                                team_agents[team_id].on_kill(kill_entry.killer_name, kill_entry.victim_name)

                        # Push to Redis Event Bus
                        event_data = {
                            "killer_name": kill_entry.killer_name,
                            "victim_name": kill_entry.victim_name,
                            "weapon": kill_entry.weapon,
                            "kill_type": kill_entry.kill_type,
                            "team_id": team_id,
                            "confidence": kill_entry.confidence,
                        }
                        await event_bus.publish(
                            event_type="kill_event",
                            data=event_data,
                            priority=EventPriority.CRITICAL,
                            source=f"vision_agent_{team_id}"
                        )
                        
        # SIMULATION FALLBACK: If the video has no real kills, simulate one for the demo
        if frame_count == 50 and not simulated_kill_sent and not kill_detected:
            simulated_kill_sent = True
            print("\n[Vision Agent] 💡 No real kills found yet. Simulating a kill for demonstration...")
            team_agents["SOUL"].on_knock("SoulMortal", "GodLJonathan")
            
            event_data = {
                "killer_name": "SoulMortal",
                "victim_name": "GodLJonathan",
                "weapon": "M416",
                "kill_type": "knock",
                "team_id": "SOUL",
                "confidence": 0.99,
            }
            await event_bus.publish(
                event_type="kill_event",
                data=event_data,
                priority=EventPriority.CRITICAL,
                source="vision_agent_SOUL"
            )

        frame_count += 1
        await asyncio.sleep(0.01)

    cap.release()
    print("\n[Vision Agent] 🛑 Finished processing.")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DIRECTOR + COMMENTARY AGENT — subscribes to Redis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def director_commentary_agent(
    event_bus: EventBus,
    obs_manager: OBSManager,
    team_agents: dict,
    director: DirectorAIEngine,
    commentary: CommentaryEngine,
):
    print("[Director Agent] 🎬 Listening on Redis Event Bus...")

    event_queue = asyncio.Queue()

    async def on_event(record):
        await event_queue.put(record.event.data)

    event_bus.subscribe("director_agent", on_event, event_types=["kill_event"])

    while True:
        try:
            event_data = await asyncio.wait_for(event_queue.get(), timeout=0.5)

            team_id = event_data.get("team_id", "UNKNOWN")
            kill_type = event_data.get("kill_type", "knock")

            print(f"\n[Director Agent] 📥 Event received: Team {team_id} had a {kill_type.upper()}")

            # Collect all team states for Director AI evaluation
            broadcast_events = [
                agent.to_broadcast_event()
                for agent in team_agents.values()
            ]

            # Decay inactive teams
            for agent in team_agents.values():
                agent.decay_priority()

            # Director AI decides: switch camera?
            decision = director.process_tick(broadcast_events)
            if decision:
                target = decision["target_team"]
                reason = decision["reason"]["event_type"]
                print(f"[Director AI] 🎯 SWITCH CAMERA → Team {target} (Reason: {reason})")

                await obs_manager.handle_director_event({
                    "action": "SWITCH_POV",
                    "target_team": target,
                })

                # Generate commentary
                commentary_text = await commentary.generate(kill_type, event_data)
                if commentary_text:
                    print(f"\n[Commentary AI] 🎙️  \"{commentary_text}\"\n")
            else:
                if director.decision_log:
                    last = director.decision_log[-1]
                    print(f"[Director AI] ⏸️  Hold shot ({last.get('explanation', '')})")

        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def main(video_source=None):
    print("\n" + "=" * 60)
    print("  🚀 AEGIS AI — FULL PRODUCTION PIPELINE v2.0")
    print("  Vision → OCR → Redis → Agents → Director → OBS")
    print("=" * 60 + "\n")

    # 1. Redis Event Bus
    event_bus = EventBus()
    await event_bus.connect()
    print("[System] ✅ Redis Event Bus connected\n")

    # 2. Team Agents (one per team)
    unique_teams = set(BGIS_TEAMS.values())
    team_agents = {team_id: TeamAgent(team_id) for team_id in unique_teams}
    print(f"[System] ✅ {len(team_agents)} Team Agents initialized: {list(team_agents.keys())}\n")

    # 3. Director AI
    director = DirectorAIEngine()
    print("[System] ✅ Director AI initialized\n")

    # 4. Commentary Engine
    commentary = CommentaryEngine()
    print("[System] ✅ Commentary Engine initialized\n")

    # 5. OBS Manager (simulation mode if OBS not running)
    obs_manager = OBSManager()
    print("[System] ✅ OBS Manager initialized (will skip if OBS not open)\n")

    # 6. Event Extractor
    extractor = EventExtractor()
    extractor.set_player_team_map(BGIS_TEAMS)

    print("-" * 60)
    print("  All systems online. Starting live pipeline...")
    print("-" * 60 + "\n")

    # Run Vision + Director concurrently
    director_task = asyncio.create_task(
        director_commentary_agent(event_bus, obs_manager, team_agents, director, commentary)
    )
    await vision_agent(event_bus, extractor, team_agents, video_source)

    # Cleanup
    director_task.cancel()
    try:
        await director_task
    except asyncio.CancelledError:
        pass

    await event_bus.disconnect()

    # Final stats
    print("\n" + "=" * 60)
    print("  📊 SESSION SUMMARY")
    print("=" * 60)
    for team_id, agent in team_agents.items():
        if agent.kills > 0 or agent.knocks > 0:
            print(f"  Team {team_id}: {agent.kills} kills, {agent.knocks} knocks")
    print(f"\n  Director decisions: {len(director.decision_log)}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    # Pass 0 for webcam, or a video path, or leave empty for default test_clip.mp4
    asyncio.run(main())
