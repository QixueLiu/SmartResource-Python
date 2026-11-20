from typing import Literal
from pydantic import BaseModel, Field

SiteKind = Literal["novel", "video", "mixed", "other"]


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=500)
    kind: SiteKind = "other"
    enabled: bool = True
    search_url_template: str | None = None
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None
    toc_item_selector: str | None = None
    toc_title_selector: str | None = None
    toc_link_selector: str | None = None


class SitePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    kind: SiteKind | None = None
    enabled: bool | None = None
    search_url_template: str | None = None
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None
    toc_item_selector: str | None = None
    toc_title_selector: str | None = None
    toc_link_selector: str | None = None


class UrlImport(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    kind: SiteKind = "other"


class CompareCandidate(BaseModel):
    site_id: int = Field(gt=0)
    url: str = Field(min_length=8, max_length=1500)


class CompareRequest(BaseModel):
    candidates: list[CompareCandidate] = Field(min_length=1, max_length=8)


class AnalyzeRequest(BaseModel):
    # Optional same-domain chapter index/details page. If absent, visit base URL.
    page_url: str | None = Field(default=None, max_length=1500)


class ScanRequest(BaseModel):
    # When IDs are omitted, scan up to 'limit' sites needing setup.
    site_ids: list[int] = Field(default_factory=list, max_length=200)
    limit: int = Field(default=100, ge=1, le=200)


class ReaderRequest(BaseModel):
    site_id: int = Field(gt=0)
    url: str = Field(min_length=8, max_length=1500)
