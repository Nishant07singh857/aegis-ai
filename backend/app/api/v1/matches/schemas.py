from pydantic import BaseModel


class MatchHealthResponse(BaseModel):
    status: str
    module: str
