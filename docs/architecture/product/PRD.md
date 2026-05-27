# AEGIS AI
## Autonomous AI Esports Operating System for BGMI

Built for: Battlegrounds Mobile India (BGMI)

---

# Product Requirements Document (PRD)

Version: 1.0  
Document Type: Enterprise Product Requirements Document  
Prepared For: AI-Native Esports Production Platform  
Prepared By: Product & Systems Architecture Team

---

## TABLE OF CONTENTS

1. Executive Summary  
2. Vision & Mission  
3. Problem Statement  
4. Product Goals  
5. User Personas  
6. Functional Requirements  
7. Non-Functional Requirements  
8. End-to-End System Architecture  
9. Hardware Infrastructure  
10. Spectator Infrastructure  
11. Multi-Agent AI System  
12. Event Bus Architecture  
13. Computer Vision Pipeline  
14. Fight Detection Engine  
15. Autonomous Observer System  
16. Player POV Switching System  
17. Real-Time Broadcast Direction  
18. Commentary AI System  
19. Emotion & Narrative Engine  
20. Overlay & Graphics Engine  
21. Real-Time Points Table  
22. Player Analytics Engine  
23. Utility Tracking System  
24. MVP & Ranking Engine  
25. Prediction & Probability Engine  
26. Coaching & Tactical Intelligence  
27. Highlight & Replay System  
28. Heatmap & Tactical Mapping  
29. Fan Engagement Systems  
30. AI Referee System  
31. Automatic & Manual Control Modes  
32. Data Architecture  
33. AI Training Pipeline  
34. Reinforcement Learning Systems  
35. Deployment Architecture  
36. Scalability Strategy  
37. Security & Reliability  
38. Monitoring & Observability  
39. Development Roadmap  
40. Business Opportunities  
41. Risk Analysis  
42. Future Expansion  
43. Final Product Vision

---

## 1. EXECUTIVE SUMMARY

AEGIS AI is a fully autonomous AI-powered esports production, analytics, commentary, coaching, and broadcasting platform designed specifically for BGMI tournaments, scrims, creators, and esports organizations.

The platform combines:

- Multi-agent AI systems
- Computer vision
- Real-time analytics
- Autonomous observing
- AI commentary
- Tactical coaching
- Highlight generation
- Narrative storytelling
- Team intelligence memory
- Automated production workflows

The system behaves like:

- esports observer team
- production crew
- analyst desk
- commentator
- tactical coach
- replay operator
- stats analyst
- content editor

All working together in real time.

---

## 2. PRODUCT VISION

Create the world's first AI-native esports operating system for BGMI that can:

- autonomously observe matches
- intelligently switch spectator feeds
- generate live commentary
- analyze teams and players
- produce live points tables
- track utilities and healing
- generate highlights automatically
- build long-term team tactical memory
- provide coaching insights
- create cinematic esports broadcasts
- continuously improve through training

---

## 3. CORE GOALS

### Primary Goals

✅ Autonomous observing  
✅ Real-time fight detection  
✅ AI-powered production direction  
✅ Live leaderboard updates  
✅ Team intelligence tracking  
✅ AI commentary  
✅ Automatic highlights  
✅ Post-match reports  
✅ Coaching analytics  
✅ Human-like broadcast behavior  
✅ Tactical prediction systems  
✅ Multi-agent coordination

---

## 4. TARGET USERS

| User | Purpose |
| --- | --- |
| Tournament organizers | Automated production |
| Scrim hosts | Auto points + analytics |
| Esports creators | Highlights + commentary |
| Professional teams | Tactical analysis |
| Coaches | Team reports |
| Analysts | Match insights |
| Viewers | Better esports experience |
| Organizations | Player scouting |

---

## 5. COMPLETE SYSTEM ARCHITECTURE

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         BGMI CUSTOM ROOM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

16 Spectator Devices
(Mobile / Tablet / Emulator)

        ↓ Live Feeds

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         VIDEO CAPTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OBS / RTMP / Screen Capture

        ↓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       AI PROCESSING LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

40+ AI Agents

- Team Agents
- Fight Agents
- Observer Agents
- Commentary Agents
- Prediction Agents
- Overlay Agents
- Coach Agents
- Narrative Agents
- Highlight Agents
- Utility Agents

        ↓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         EVENT BUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Redis / Kafka

        ↓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       MASTER DIRECTOR AI
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Controls:
- feed switching
- commentary timing
- overlays
- highlights
- storylines
- cinematic flow

        ↓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        OUTPUT SYSTEMS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Live Broadcast
Realtime Dashboard
Highlights
Reports
Analytics
Clips
Overlays
```

---

## 6. PHYSICAL HARDWARE SETUP

### Main AI Server (Recommended Production Specs)

| Hardware | Recommendation |
| --- | --- |
| GPU | RTX 4090 |
| CPU | Ryzen 9 / Threadripper |
| RAM | 64GB–128GB |
| Storage | NVMe SSD |
| OS | Ubuntu Linux |

Purpose:

- run AI inference
- OCR
- databases
- event system
- commentary
- fight detection
- overlays

---

## Spectator Devices

These devices ONLY provide spectator feeds.

| Device | Role |
| --- | --- |
| Tablet 1 | Team Soul |
| Tablet 2 | GodLike |
| Tablet 3 | Revenant |
| Tablet 4 | Map Observer |
| Emulator 5 | Final Zone |

Each device:

- logs into BGMI
- joins spectator slot
- streams screen feed

---

## OBS Broadcast System

Purpose:

- live stream production
- feed switching
- overlays
- recording
- replays

Connected through:

- OBS WebSocket

---

## 7. MATCH START FLOW

### Step 1 — Create Custom Room

Tournament room created. Players join.

### Step 2 — Spectator Devices Join

Each spectator device:

- opens BGMI
- logs into spectator account
- joins room
- spectates assigned team

Example:

| Device | Watching |
| --- | --- |
| Spec 1 | Team Soul |
| Spec 2 | GodLike |
| Spec 3 | Revenant |

### Step 3 — Video Feeds Start

Each device streams:

- screen capture
- RTMP feed
- OBS source

To the AI processing server.

### Step 4 — AI Agents Activate

Master controller starts:

- Team agents
- Fight agents
- OCR system
- Commentary AI
- Overlay engine
- Clip recorder

Automatically.

---

## 8. MULTI-AGENT AI ARCHITECTURE

| Agent Type | Count |
| --- | --- |
| Team Tracking Agents | 16 |
| Fight Detection Agents | 6 |
| Observer Director Agents | 4 |
| Prediction Agents | 4 |
| Commentary Agents | 3 |
| Narrative Agents | 2 |
| Highlight Agents | 2 |
| Overlay Agents | 2 |
| Coach Agents | 2 |
| Utility Tracking Agents | 2 |
| Master Coordinator | 1 |

Total ≈ 40+ AI agents

---

## 9. TEAM INTELLIGENCE AGENTS

Each team gets one dedicated AI agent.

Example:

| Agent | Team |
| --- | --- |
| Agent 1 | Team Soul |
| Agent 2 | GodLike |
| Agent 3 | Revenant |

### Team Agent Responsibilities

Tracks:

✅ rotations  
✅ fight style  
✅ utility usage  
✅ healing  
✅ positioning  
✅ vehicle usage  
✅ aggression level  
✅ revive timing  
✅ team spacing  
✅ clutch history

### Tactical Team Memory

AI stores long-term behavioral data.

Example:

```json
{
  "team": "Soul",
  "rotation_style": "early edge",
  "fight_style": "aggressive",
  "preferred_compounds": ["Gatka", "Pochinki"]
}
```

Over time AI learns:

- habits
- strategies
- weaknesses
- tendencies

---

## 10. COMPUTER VISION ENGINE

### Purpose

Allow AI to visually understand BGMI.

### Technologies

- OpenCV
- YOLOv8
- PaddleOCR
- ByteTrack

### Detects

| Feature | Method |
| --- | --- |
| kill feed | OCR |
| HP bars | CV |
| grenades | YOLO |
| smokes | YOLO |
| vehicles | YOLO |
| minimap | CV |
| player names | OCR |
| alive count | OCR |
| healing | animation detection |
| utility | inventory tracking |

---

## 11. EVENT SYSTEM

Every detection becomes a structured event.

Example:

```json
{
  "event": "fight_detected",
  "teams": ["Soul", "GodLike"],
  "location": "Pochinki",
  "fight_score": 92
}
```

Published to:

- Redis
- Kafka

---

## 12. FIGHT DETECTION ENGINE

### Signals Used

| Signal | Meaning |
| --- | --- |
| HP drops | active combat |
| kill feed | engagement |
| grenades | utility fight |
| smokes | tactical push |
| close proximity | possible fight |
| gunshot audio | nearby combat |

### Fight Score Formula

```python
fight_score = (
    knocks * 10 +
    low_hp_players * 5 +
    grenades * 4 +
    final_zone_bonus * 15
)
```

Higher score = more important fight.

---

## 13. MASTER DIRECTOR AI

### Purpose

Acts like esports production director.

### Responsibilities

- choose best feed
- prioritize fights
- control cinematic flow
- manage observing behavior
- trigger replays
- trigger highlights
- synchronize commentary

### Human-Like Observing Rules

```python
minimum_scene_time = 10  # seconds
```

```python
only_switch_if_new_fight_significantly_better = True
```

AI avoids:

- random switching
- chaotic observing

AI prioritizes:

- clutch fights
- rivalries
- final zones
- emotional moments

---

## 14. OBS AUTOMATION SYSTEM

### Purpose

Automatically switch feeds and overlays.

### Workflow

```text
Fight Detected
      ↓
Director AI chooses best feed
      ↓
OBS WebSocket command
      ↓
Live broadcast switches
```

### Features

✅ automatic feed switching  
✅ dynamic overlays  
✅ replay scenes  
✅ recording automation  
✅ transition management

---

## 15. REALTIME POINTS TABLE SYSTEM

### Purpose

Update tournament standings in real time.

### Tracks

- placement points
- kill points
- total points
- alive teams
- WWCD

Example table updated every second.

---

## 16. PLAYER ANALYTICS SYSTEM

Tracks every player.

Example metrics: kills, damage, healing, revives, smokes, grenades, knocks, survival.

---

## 17. UTILITY TRACKING SYSTEM

Tracks smokes, grenades, molotovs, healing, boosts.

---

## 18. MVP ENGINE

```python
mvp_score = kills * 5 + damage * 0.05 + revives * 3 + clutch_bonus
```

Outputs: Match MVP, Best Support, Best Assaulter, Best Sniper, Best Clutch Player.

---

## 19. COMMENTARY AI SYSTEM

Modes: Hype, Analyst, Hindi, Casual, Cinematic.  
Voice: ElevenLabs / TTS models.

---

## 20. STORYLINE ENGINE

Tracks rivalries, revenge arcs, comeback stories, star players, underdog runs.

---

## 21. HEATMAP & STRATEGY SYSTEM

Generates tactical heatmaps, rotation maps, danger zones, preferred compounds.

---

## 22. AI COACH SYSTEM

Detects: bad rotations, risky pushes, utility misuse, spacing errors, late zone entries.

---

## 23. CLUTCH PREDICTION ENGINE

Predicts fight outcomes and clutch probabilities from HP/utility/positioning etc.

---

## 24. TEAM CHEMISTRY ANALYSIS

Measures synchronized pushes, revive timing, spacing discipline, utility coordination.

---

## 25. HIGHLIGHT GENERATION ENGINE

Automatically detects squad wipes, clutches, grenade kills, final circles, etc.

Workflow:

```text
Fight detected → Timestamp saved → Replay exported → Clip generated
```

---

## 26. POST MATCH REPORT SYSTEM

Generates match summary, team reports, and match timeline.

---

## 27. LIVE OVERLAY SYSTEM

Displays MVP, danger zone, leaderboard, top damage dealer, fight probability, utility counts.

---

## 28. FAN ENGAGEMENT SYSTEM

Live polls, fantasy points, AI predictions, trivia, engagement widgets.

---

## 29. AI REFEREE SYSTEM

Detects suspicious teaming, exploits, unusual behavior, rule violations.

---

## 30. DIGITAL TWIN SYSTEM

Creates replayable structured match state; searchable historical match data.

---

## 31. CONTINUOUS TRAINING SYSTEM

```text
Recorded Matches → Frame Extraction → Event Extraction → Structured Dataset → Model Training → Improved AI
```

Learning: CV training, supervised learning, RL, RLHF, NLP fine-tuning.

---

## 32. DATABASE DESIGN

PostgreSQL tables: matches, teams, players, fights, utilities, predictions, clips, commentary, rotations, heatmaps.

---

## 33. SOFTWARE STACK

Backend: FastAPI  
Frontend: Next.js  
Vision: YOLOv8 + PaddleOCR + ByteTrack + OpenCV  
Queue: Redis/Kafka  
DB: PostgreSQL  
LLM: OpenAI/Gemini  
Voice: ElevenLabs  
Broadcast: OBS

---

## 34. DEVELOPMENT ROADMAP

Phase 1 — MVP (4–6 weeks): kill feed OCR, live leaderboard, basic overlays  
Phase 2 — Observer AI (6–8 weeks): fight detection, OBS switching, replay system  
Phase 3 — Multi-Agent (2–3 months): 16 team agents, tactical memory, utility tracking  
Phase 4 — Commentary & Highlights (2 months): AI commentary, automatic clips  
Phase 5 — Advanced AI (3–6 months): predictive observing, coaching, storyline, emotion engine

---

## 35. FINAL PRODUCT VISION

AEGIS AI becomes autonomous esports infrastructure for BGMI tournaments, scrims, creators, orgs, coaches, analysts: observing, directing, commentary, analytics, highlights, coaching, predictions, and narratives with minimal human production.
