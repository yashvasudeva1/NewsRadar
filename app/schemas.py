from datetime import datetime
from pydantic import BaseModel, Field


class InterestCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    weight: float = Field(default=1.0, ge=0.1, le=10.0)


class InterestUpdate(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(default=None, ge=0.1, le=10.0)


class ArticleOut(BaseModel):
    id: int
    title: str
    description: str
    url: str
    author: str
    image_url: str
    language: str
    categories: list[str]
    published_at: datetime | None
    source_domain: str
    relevance_score: float
    matched_keywords: list[str]
    is_saved: bool
    is_official: bool
    is_research_news: bool = False

    model_config = {"from_attributes": True}


class ResearchItemOut(BaseModel):
    id: int
    external_id: str
    item_type: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    source: str
    source_domain: str
    venue: str
    doi: str
    paper_id: str
    landing_url: str
    pdf_url: str
    published_at: datetime | None
    updated_at: datetime | None
    citation_count: int
    relevance_score: float
    matched_keywords: list[str]
    is_saved: bool

    model_config = {"from_attributes": True}
