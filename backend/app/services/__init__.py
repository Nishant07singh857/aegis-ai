# backend/app/services/__init__.py
"""
AEGIS AI - Application Services Layer

Thin, real service classes over the SQLAlchemy models. Each service takes an
async DB session and exposes the domain operations the API layer needs.
"""

from .match_service import MatchService

__all__ = ["MatchService"]
