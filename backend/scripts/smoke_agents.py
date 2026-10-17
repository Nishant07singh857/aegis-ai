#!/usr/bin/env python3
"""AEGIS AI - Agent runtime smoke test (Phase 1: agent runtime repair).

Verifies that the AgentOrchestrator can initialize ALL agents without
crashing, that every agent starts/stops through the BaseAgent lifecycle,
and that one tick/update cycle runs per agent.

Dependency-light by design:
  - No server, no network, no paid APIs.
  - `structlog` is stubbed (logging only; not installed in this env).
  - `app.core.event_bus` is stubbed with an in-memory fake implementing
    the real interface (the real EventBus requires Redis).
  - `app.config.settings` is stubbed (the real one needs pydantic-settings).

Everything else under test is REAL: all agent classes, BaseAgent
lifecycle, AgentOrchestrator phases, AgentRegistry.

Exit code: 0 if every agent initializes + ticks OK, 1 otherwise.
"""

import asyncio
import sys
import time
import types
from enum import IntEnum
from types import SimpleNamespace

BACKEND_DIR = "/home/hatch/workspace/aegis-ai/backend"
sys.path.insert(0, BACKEND_DIR)

# --------------------------------------------------------------------------
# Stub: structlog (logging only)
# --------------------------------------------------------------------------


class _NoOpLogger:
    def bind(self, *args, **kwargs):
        return self

    def __getattr__(self, name):
        def _log(*args, **kwargs):
            return None

        return _log


_structlog_stub = types.ModuleType("structlog")
_structlog_stub.get_logger = lambda *a, **k: _NoOpLogger()
sys.modules["structlog"] = _structlog_stub

# --------------------------------------------------------------------------
# Stub: app.core.event_bus (in-memory fake of the real interface)
# --------------------------------------------------------------------------


class EventPriority(IntEnum):
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    BACKGROUND = 5


class _FakeEvent:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.data = data


class EventRecord:
    """Minimal stand-in for the real EventRecord (has .event.data)."""

    def __init__(self, event_type, data):
        self.event = _FakeEvent(event_type, data)


class FakeEventBus:
    """In-memory event bus implementing the real EventBus interface."""

    def __init__(self):
        self._subscribers = {}  # event_type -> {subscriber_id: callback}
        self.published = 0

    def subscribe(self, subscriber_id, callback, event_types):
        for et in event_types:
            self._subscribers.setdefault(et, {})[subscriber_id] = callback
        return subscriber_id

    def unsubscribe(self, subscriber_id):
        for subs in self._subscribers.values():
            subs.pop(subscriber_id, None)
        return True

    async def publish(
        self,
        event_type,
        data,
        priority=EventPriority.MEDIUM,
        match_id=None,
        tournament_id=None,
        source="system",
        correlation_id=None,
        metadata=None,
    ):
        record = EventRecord(event_type, data or {})
        for callback in list(self._subscribers.get(event_type, {}).values()):
            result = callback(record)
            if asyncio.iscoroutine(result):
                await result
        self.published += 1
        return "fake-event-id"


_event_bus_stub = types.ModuleType("app.core.event_bus")
_event_bus_stub.EventBus = FakeEventBus
_event_bus_stub.EventRecord = EventRecord
_event_bus_stub.EventPriority = EventPriority
sys.modules["app.core.event_bus"] = _event_bus_stub

# --------------------------------------------------------------------------
# Stub: app.config (settings only; real config needs pydantic-settings)
# --------------------------------------------------------------------------

_config_stub = types.ModuleType("app.config")
_config_stub.settings = SimpleNamespace(
    MAX_TEAM_AGENTS=16,
    MIN_SCENE_TIME_SECONDS=10,
    MAX_SWITCHES_PER_MINUTE=8,
)
sys.modules["app.config"] = _config_stub

# --------------------------------------------------------------------------
# Real imports under test
# --------------------------------------------------------------------------

from app.agents.orchestrator import AgentOrchestrator  # noqa: E402
from app.agents.base_agent import BaseAgent, AgentConfig, AgentState  # noqa: E402


def make_teams(n=4):
    teams = []
    for i in range(1, n + 1):
        name = f"Smoke Team {i}"
        teams.append(
            {
                "team_id": f"smoke_team_{i:02d}",
                "team_name": name,
                "short_name": f"ST{i}",
                "player_names": [f"{name} Player {j}" for j in range(1, 5)],
            }
        )
    return teams


async def main():
    started = time.time()
    results = []  # (agent_id, ok, detail)

    bus = FakeEventBus()
    orch = AgentOrchestrator(bus)
    teams = make_teams(4)

    # ---- Phase: initialize ----
    try:
        await orch.initialize_all_agents(
            teams, match_id="smoke_match", tournament_id="smoke_tourney"
        )
        init_ok = True
        init_detail = f"{len(orch.agents)} agents registered"
    except Exception as e:  # noqa: BLE001
        init_ok = False
        init_detail = f"{type(e).__name__}: {e}"
    results.append(("__orchestrator_init__", init_ok, init_detail))
    print(f"[init] {'OK' if init_ok else 'FAILED'}: {init_detail}")
    if not init_ok:
        print("\nSMOKE RESULT: FAILED (initialization crashed)")
        return 1

    # ---- Phase: start ----
    try:
        await orch.start_all_agents()
        start_ok = True
        start_detail = f"{orch.state.active_agents}/{orch.state.total_agents} active"
    except Exception as e:  # noqa: BLE001
        start_ok = False
        start_detail = f"{type(e).__name__}: {e}"
    results.append(("__orchestrator_start__", start_ok, start_detail))
    print(f"[start] {'OK' if start_ok else 'FAILED'}: {start_detail}")

    # ---- Phase: one tick per agent ----
    for agent_id in sorted(orch.agents.keys()):
        agent = orch.agents[agent_id]
        try:
            if not isinstance(agent, BaseAgent):
                raise TypeError(
                    f"not a BaseAgent (got {type(agent).__name__})"
                )
            await asyncio.wait_for(agent.tick(), timeout=15)
            if agent.state not in (AgentState.RUNNING, AgentState.DEGRADED):
                raise RuntimeError(f"unexpected state after tick: {agent.state}")
            results.append((agent_id, True, f"tick OK ({type(agent).__name__})"))
        except Exception as e:  # noqa: BLE001
            results.append(
                (agent_id, False, f"{type(e).__name__}: {e}")
            )

    # ---- Phase: pause/resume/stop ----
    try:
        await orch.pause_all()
        await orch.resume_all()
        await orch.stop_all_agents()
        results.append(("__orchestrator_stop__", True, "paused/resumed/stopped cleanly"))
    except Exception as e:  # noqa: BLE001
        results.append(("__orchestrator_stop__", False, f"{type(e).__name__}: {e}"))

    # ---- Report ----
    print("\n" + "=" * 72)
    print("PER-AGENT SMOKE RESULTS")
    print("=" * 72)
    failed = 0
    for agent_id, ok, detail in results:
        status = "OK    " if ok else "FAILED"
        if not ok:
            failed += 1
        print(f"  [{status}] {agent_id}: {detail}")
    print("=" * 72)
    total = len(results)
    elapsed = time.time() - started
    print(f"SUMMARY: {total - failed}/{total} OK, {failed} FAILED ({elapsed:.1f}s)")

    if orch.state.startup_errors:
        print("startup_errors captured by orchestrator:")
        for err in orch.state.startup_errors:
            print(f"  - {err}")

    if failed:
        print("\nSMOKE RESULT: FAILED")
        return 1
    print("\nSMOKE RESULT: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
