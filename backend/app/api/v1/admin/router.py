from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def admin_health():
    return {"status": "ok", "module": "admin"}

