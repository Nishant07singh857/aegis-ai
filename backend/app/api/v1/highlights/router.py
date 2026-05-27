from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def highlights_health():
    return {"status": "ok", "module": "highlights"}

