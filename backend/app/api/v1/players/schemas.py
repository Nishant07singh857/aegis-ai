from pydantic import BaseModel


class PlayerHealthResponse(BaseModel):
    status: str
    module: str

