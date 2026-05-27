from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def matches_health():
    return {"status": "ok", "module": "matches"}
