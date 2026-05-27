from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def players_health():
    return {"status": "ok", "module": "players"}

