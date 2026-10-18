# backend/app/cluster/__init__.py
"""AEGIS AI — distributed cluster support (Phase 7 / W3).

The node registry keeps every backend process visible to every other
process through Redis hashes at ``aegis:nodes:{node_id}``. See
``app.cluster.registry`` for the lifecycle contract and docs/CLUSTER.md
for how to run a real multi-node deployment.
"""

from .registry import NodeRegistry, node_registry

__all__ = ["NodeRegistry", "node_registry"]
