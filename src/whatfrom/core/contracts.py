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
    """후보 태그 하나.

    version·distribution·distro_codename·variant는 태그 이름과 같은 이미지의 다른
    태그에서 규칙으로 읽은 값이다(collect.derive). 모르면 None이다.
    version은 image_tags.language_version이다. python이 아닌 이미지에도 쓰므로
    계약에서는 version이라 부른다.
    distribution과 distro_codename은 태그의 Linux 이미지 기준이다. Windows 이미지도
    함께 담은 python:3.14의 distribution은 debian이다. Windows 이미지만 담은 태그만
    windows다. 플랫폼별 OS는 platforms가 보여 준다.
    """

    image: str  # "repository:tag"
    repository: str
    tag: str
    digest: str | None = None
    platforms: list[Platform] = Field(default_factory=list)
    last_pushed_at: datetime | None = None
    source_url: str
    collected_at: datetime
    evidence: list[Evidence] = Field(default_factory=list)
    version: str | None = None
    distribution: str | None = None
    distro_codename: str | None = None
    variant: str | None = None


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


class SearchPlan(BaseModel):
    """질문에서 뽑은 검색 조건 (스펙 §7). LLM #1이 채우고 백엔드가 SQL로 옮긴다.

    답이 아니라 검색 조건이다. 이미지 이름을 담지 않으므로 LLM이 이미지를 지어낼
    경로가 늘지 않는다. repository는 수집된 목록과 대조해 없으면 버린다.

    extra="forbid"의 이유는 Recommendation과 같다. 지어낸 필드를 거부하고,
    strict json_schema가 받아들이는 스키마(additionalProperties: false)를 만든다.
    """

    model_config = ConfigDict(extra="forbid")

    repository: str | None = None
    version_prefix: str | None = None
    architectures: list[str] = Field(default_factory=list)
    # 이 중 하나여야 하는 배포판이나 코드네임. "bookworm이어야 한다".
    distributions: list[str] = Field(default_factory=list)
    exclude_distributions: list[str] = Field(default_factory=list)
    # 질문이 숫자로 준 크기 상한. "작을수록"은 조건이 아니다.
    max_size_mb: float | None = None


class RecommendResponse(BaseModel):
    question: str
    recommendation: Recommendation | None
    candidates: list[Candidate]
    # 스펙 §8의 정상 경로가 아닌 단계로 답했는가. 추천이 있어도 True일 수 있다
    # (검색 조건 추출 실패, 조건 완화). 이유는 notes에 있다.
    degraded: bool = False
    notes: list[str] = Field(default_factory=list)
    # 질문을 어떻게 해석했는지. 추출에 실패했거나 부르지 않았으면 None이다.
    plan: SearchPlan | None = None
