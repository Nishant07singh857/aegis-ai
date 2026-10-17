# backend/app/__init__.py
"""
AEGIS AI - Autonomous Esports Operating System (backend)

The ``app`` package is the FastAPI backend for AEGIS AI: an autonomous
system that observes BGMI (Battlegrounds Mobile India) matches,
broadcasts them, and generates commentary.

Package layout
--------------
- ``agents``: autonomous agents (observer, director, commentator, ...)
  that coordinate over the event bus in ``core``.
- ``api``: HTTP API, currently versioned routers under ``api/v1``.
- ``core``: cross-cutting services — config, logging, event bus,
  rate limiting, middleware.
- ``db``: SQLAlchemy models and Alembic migrations.
- ``exceptions``: shared exception hierarchy (import from here, don't
  redefine per-module error types).
- ``ml``: machine-learning components — fight detection, commentary
  pipelines, and player profiling (Elo ratings, k-means clustering,
  playstyle classification).
- ``services``: domain services used by the API and agents.
- ``vision``: video ingestion and real-time detectors (kill feed OCR,
  minimap reader, player tracking, ...).

Importing this package has no side effects and does not require
optional heavy dependencies (torch, cv2) — subpackages that need them
guard their imports locally.
"""

__all__: list = []
