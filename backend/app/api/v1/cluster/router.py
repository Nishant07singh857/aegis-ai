# backend/app/api/v1/cluster/router.py
"""Cluster API router — mounted at /api/v1 by app/main.py.

Read-only introspection of the distributed node registry
(``app.cluster.registry``).

  * GET  /cluster/nodes — any authenticated user (viewer included). Live
    nodes from Redis, or a single honest ``standalone=True`` entry for this
    process when Redis is unreachable. No secrets anywhere: node_id and
    hostname are operational metadata, not credentials.

Status codes: 401 unauthenticated.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from app.api.deps import TokenPayload, get_current_user
from app.cluster.registry import node_registry

from .schemas import NodeInfo

router = APIRouter(prefix="/cluster", tags=["Cluster"])


@router.get("/nodes", response_model=List[NodeInfo])
async def list_cluster_nodes(
    _user: TokenPayload = Depends(get_current_user),
) -> List[NodeInfo]:
    """Live cluster membership. Any authenticated user, viewers included."""
    nodes = await node_registry.list_nodes()
    return [NodeInfo(**entry) for entry in nodes]
