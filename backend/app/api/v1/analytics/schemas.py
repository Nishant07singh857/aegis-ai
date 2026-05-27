from pydantic import BaseModel


class AnalyticsHealthResponse(BaseModel):
    status: str
    module: str

