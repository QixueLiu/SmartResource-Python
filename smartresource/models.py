from typing import Literal
from pydantic import BaseModel, Field

class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    kind: Literal["novel", "video", "mixed", "other"] = "novel"
    enabled: bool = True
    search_url_template: str | None = None
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None

class SiteOut(SiteIn):
    id: int
