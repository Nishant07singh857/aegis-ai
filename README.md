# Auto-generated placeholder for README.md
> NOTE (Phase 33 low batch, 2026-10-26): this file is a scaffold-era
> placeholder, NOT the current setup guide — it has not been rewritten
> wholesale here. Do not follow the run plan below verbatim: the
> inline corrections mark the lines verified false against current code.
> For the supported streaming topology see `docs/STREAMING.md` §0; for
> current code counts see `docs/PROJECT_INVENTORY.md` (headline table).
Aegis AI - Initialization and Run Plan
The AEGIS AI project is an Autonomous Esports Operating System. After analyzing the project structure, it consists of three main components:

Backend (/backend): A FastAPI application using PyTorch, WebSockets, and a match simulator. It provides REST APIs and live WebSocket feeds for the esports game state.
Frontend (/frontend): A Next.js application that serves as the main dashboard (director panel, analytics, system health). It connects to the backend via WebSockets (hook: `frontend/lib/useLiveSocket.ts` — there is no `use-aegis-ws.ts` file).
Overlays: CORRECTION — the top-level `/overlays` directory is empty in this tree; it is not a second Next.js app. OBS overlay pages live as routes inside the frontend (`/overlays/...` under `frontend/app`).
To completely initialize and run this project, we need to execute the following steps:

Proposed Changes / Steps
1. Initialize the Backend
The backend uses Poetry for dependency management. We need to:

Install dependencies: cd backend && poetry install
Run the FastAPI server: cd backend && poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
Note: CORRECTION — the backend does NOT start sending mock game events on boot. The match simulator (`match_simulator` in `backend/app/main.py`) runs ONLY when `DEMO_MODE=1`; by default (`DEMO_MODE` off) demo data is disabled and endpoints honestly report that no live feed is connected.
2. Initialize the Frontend (Dashboard)
The main frontend is a Next.js app.

Install dependencies: cd frontend && npm install
Run the Next.js dev server: cd frontend && npm run dev (Runs on port 3000 by default)
3. Initialize the Overlays
The overlays system is another Next.js app.

Install dependencies: cd overlays && npm install
Run the Next.js dev server on a different port (e.g., 3001) to avoid conflicts with the frontend: cd overlays && npm run dev -- -p 3001
4. Integration Verification
Open the Dashboard at http://localhost:3000/.
Verify the WebSockets are connecting to ws://localhost:8000/ws/live.
Confirm that live game events (kill feed, fights, standings) are streaming in real-time.
User Review Required
Does this execution plan align with your goal of running and initializing the project? Once you approve, I will automatically execute these steps in the background using terminal commands, get the services running, and provide you with a Walkthrough of how to view the live dashboard.