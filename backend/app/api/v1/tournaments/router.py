from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def tournaments_health():
    return {"status": "ok", "module": "tournaments"}
