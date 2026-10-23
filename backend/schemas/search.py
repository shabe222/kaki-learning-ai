from pydantic import BaseModel
from typing import Optional


class SearchRequest(BaseModel):
    query: str
    user_id: Optional[int] = None
    resource_type: Optional[str] = None
    limit: int = 10
