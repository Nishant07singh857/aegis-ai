from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def broadcasts_health():
    return {"status": "ok", "module": "broadcasts"}

