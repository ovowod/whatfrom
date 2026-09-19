# src/whatfrom/eval/goldenset.py
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class GoldenSetError(ValueError):
    """골든셋 파일이 스키마를 만족하지 못한다."""


class Conditions(BaseModel):
    """추천 이미지가 지켜야 할 아키텍처·배포판·버전·크기 조건. 조건 일치율을 채점한다.

    질문에 적힌 조건과 다를 수 있다. torch에 musl 휠이 없다는 지식에서 나온 alpine
    제외처럼 문서가 근거인 조건도 있다. 검색 조건 추출의 정답은 ExpectedPlan이다.
    """

    model_config = ConfigDict(extra="forbid")

    architectures: list[str] = Field(default_factory=list)
    exclude_distributions: list[str] = Field(default_factory=list)
    version_prefix: str | None = None
    max_size_mb: float | None = None

    @property
    def declared(self) -> bool:
        """조건이 하나라도 있으면 이 문항을 조건 일치율의 분모에 포함한다."""
        return bool(
            self.architectures
            or self.exclude_distributions
            or self.version_prefix is not None
            or self.max_size_mb is not None
        )


class ExpectedPlan(BaseModel):
    """질문에 명시된 검색 조건. 검색 조건 추출(LLM #1)의 정답이다.

    Conditions와 다르다. 추출기는 질문에 적힌 것만 뽑아야 하므로, 문서 지식에서 나온
    조건은 여기에 적지 않는다. 적지 않은 필드는 비어 있다는 뜻이고 그대로 비교한다.
    모델이 질문에 없는 크기 상한을 만들어내면 실패로 잡아야 하기 때문이다.
    리포지토리는 적지 않는다. requires_repositories가 정답이다.
    """

    model_config = ConfigDict(extra="forbid")

    version_prefix: str | None = None
    architectures: list[str] = Field(default_factory=list)
    distributions: list[str] = Field(default_factory=list)
    exclude_distributions: list[str] = Field(default_factory=list)
    max_size_mb: float | None = None


class Rationale(BaseModel):
    """정답·오답 판정의 설명과 출처를 저장한다.

    sources에는 http(s) URL을 하나 이상 적어야 한다. 로더는 URL의 형식만 검사한다.
    주소가 실제로 열리는지, 내용이 판정을 뒷받침하는지는 별도 검토가 필요하다.
    """

    model_config = ConfigDict(extra="forbid")

    note: str
    sources: list[str] = Field(min_length=1)

    @field_validator("sources")
    @classmethod
    def http_urls(cls, sources: list[str]) -> list[str]:
        # 빈 목록만 막으면 [""]나 아무 문자열로 근거 요건을 우회할 수 있다.
        for source in sources:
            parsed = urlparse(source.strip())
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"출처가 http(s) URL이 아니다: {source!r}")
        return sources


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    # 채점에 필요한 리포지터리. 하나라도 색인되지 않았으면 문항을 미측정으로 분류한다.
    requires_repositories: list[str] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    # 정답으로 인정할 이미지 목록. 추천 이미지가 이 목록에 있으면 정확도로 집계한다.
    accept: list[str] = Field(min_length=1)
    # 명시적 오답. 지표에는 들어가지 않고 리포트에 경보로 표시된다.
    reject: list[str] = Field(default_factory=list)
    conditions: Conditions = Field(default_factory=Conditions)
    # 기본값을 두지 않는다. 빠뜨린 문항이 조용히 "조건 없음"으로 채점되면 안 된다.
    expected_plan: ExpectedPlan
    expected_sections: list[str] = Field(default_factory=list)
    rationale: Rationale


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    # 인용한 출처를 마지막으로 확인한 날. latest·LTS·최신 패치·휠 제공 여부는
    # 시간이 지나면 낡는데, 낡았다는 사실이 어디에도 남지 않으면 알 수 없다.
    verified_on: date | None = None
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "GoldenSet":
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(f"중복된 문항 id: {case.id}")
            seen.add(case.id)
        return self


def load_goldenset(path: Path) -> GoldenSet:
    """YAML 구문·스키마 오류는 GoldenSetError로 변환하고 파일 접근 오류는 그대로 전파한다."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"{path}를 파싱하지 못했다: {exc}") from exc

    try:
        return GoldenSet.model_validate(raw)
    except ValidationError as exc:
        raise GoldenSetError(f"{path}가 스키마를 만족하지 못한다:\n{exc}") from exc
