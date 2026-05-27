from pydantic import BaseModel


class BroadcastHealthResponse(BaseModel):
    status: str
    module: str

