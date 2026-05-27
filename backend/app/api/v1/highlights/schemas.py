from pydantic import BaseModel


class HighlightHealthResponse(BaseModel):
    status: str
    module: str

