from pydantic import BaseModel
from typing import Optional


class VideoGenerateRequest(BaseModel):
    topic: str
    duration: int = 5
    ratio: str = "16:9"
    resolution: str = "720p"
    mode: str = "quick"
    user_id: Optional[int] = None
