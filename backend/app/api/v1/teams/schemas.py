from pydantic import BaseModel


class TeamHealthResponse(BaseModel):
    status: str
    module: str

