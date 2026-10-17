"""AEGIS AI HTTP API layer.

`app.api.deps` holds the shared FastAPI dependencies (JWT/API-key auth,
DB sessions). `app.api.v1` holds the versioned routers — Phase 2 ships the
teams vertical slice; other domains were removed as stubs and will return
as real slices.
"""
