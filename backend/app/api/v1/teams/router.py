from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def teams_health():
    return {"status": "ok", "module": "teams"}

