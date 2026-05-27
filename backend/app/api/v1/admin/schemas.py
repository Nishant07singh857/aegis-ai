from pydantic import BaseModel


class AdminHealthResponse(BaseModel):
    status: str
    module: str

