# backend/app/api/v1/cluster/schemas.py
"""Pydantic schemas for the cluster introspection API (Phase 7 / W3)."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class NodeInfo(BaseModel):
    """One node as seen in the live cluster view."""

    node_id: str = Field(description="Per-process node identity (uuid4 hex, or NODE_ID override)")
    hostname: str = Field(description="Host this node runs on (operational, not sensitive)")
    roles: List[str] = Field(description="Roles this node serves: api, ai-worker, streamer")
    started_at: str = Field(description="Node process start time, ISO-8601 UTC")
    version: str = Field(description="Build version (GIT_SHA env or app VERSION)")
    heartbeat_age_s: float = Field(description="Seconds since this node's last heartbeat")
    status: Literal["healthy", "degraded", "stale"] = Field(
        description="healthy <30s, degraded 30-45s, stale >45s"
    )
    agent_count: int = Field(description="Agents this node reports as running")
    standalone: bool = Field(
        default=False,
        description="True when this entry is the local standalone fallback (Redis unreachable)",
    )
