from pydantic import BaseModel


class TournamentHealthResponse(BaseModel):
    status: str
    module: str
