# src/whatfrom/core/contracts.py
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    section_title: str
    content: str
    source_url: str


class Platform(BaseModel):
    """image_variants 한 행. 뭉개지 않는다.

    아키텍처 이름만으로는 식별되지 않는다 — python:3.13의 amd64는 linux 하나와
    커널 버전이 다른 windows 둘, 합쳐 세 번 나온다. 크기도 400MB와 2.4GB로
    갈린다. 하나로 접으면 어느 쪽이 살아남는지가 순서에 달리게 된다.
    """

    os: str
    architecture: str
    arch_variant: str
    os_version: str
    size_bytes: int
    digest: str


class Candidate(BaseModel):
    image: str  # "repository:tag"
    repository: str
    tag: str
    digest: str | None = None
    platforms: list[Platform] = Field(default_factory=list)
    last_pushed_at: datetime | None = None
    source_url: str
    collected_at: datetime
    evidence: list[Evidence] = Field(default_factory=list)


class Recommendation(BaseModel):
    """LLM이 채우는 유일한 구조체. image는 반드시 후보 중 하나여야 한다.

    extra="forbid"는 두 가지를 한다: LLM이 지어낸 필드를 거부하고,
    model_json_schema()가 additionalProperties: false를 내보내
    OpenAI 호환 서버의 strict json_schema 모드가 받아들이는 스키마가 된다.
    """

    model_config = ConfigDict(extra="forbid")

    image: str
    reason: str
    dockerfile: str
    alternatives: list[str] = Field(default_factory=list)


class RecommendResponse(BaseModel):
    question: str
    recommendation: Recommendation | None
    candidates: list[Candidate]
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)
