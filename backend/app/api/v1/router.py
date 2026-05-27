# backend/app/api/v1/router.py
"""
AEGIS AI - API Router
All version 1 API endpoints
"""

from fastapi import APIRouter

from .tournaments.router import router as tournament_router
from .matches.router import router as match_router
from .broadcasts.router import router as broadcast_router
from .analytics.router import router as analytics_router
from .teams.router import router as team_router
from .players.router import router as player_router
from .highlights.router import router as highlight_router
from .admin.router import router as admin_router

api_router = APIRouter()

# Mount all route groups
api_router.include_router(tournament_router, prefix="/tournaments", tags=["Tournaments"])
api_router.include_router(match_router, prefix="/matches", tags=["Matches"])
api_router.include_router(broadcast_router, prefix="/broadcasts", tags=["Broadcasts"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(team_router, prefix="/teams", tags=["Teams"])
api_router.include_router(player_router, prefix="/players", tags=["Players"])
api_router.include_router(highlight_router, prefix="/highlights", tags=["Highlights"])
api_router.include_router(admin_router, prefix="/admin", tags=["Admin"])
