"""Director API — mounted at /api/v1/director by app/main.py."""

from .router import router

__all__ = ["router"]
