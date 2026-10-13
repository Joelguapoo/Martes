from datetime import datetime

from pydantic import BaseModel, HttpUrl


class Source(BaseModel):
    title: str
    site: str | None = None
    url: HttpUrl
    published_at: str | None = None
    accessed_at: datetime
    snippet: str


class ResearchResult(BaseModel):
    query: str
    summary: str
    facts: list[str]
    limitations: list[str]
    sources: list[Source]
