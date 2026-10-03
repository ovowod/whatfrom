# src/whatfrom/core/config.py
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 덧붙일 JSON이 덮어쓰면 안 되는 요청 키. 코드가 정하는 값이다.
RESERVED_LLM_BODY_KEYS = frozenset({"model", "messages", "response_format"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WHATFROM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom"
    test_database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom_test"
    embedder: str = "fake"

    # pool 기본값은 SQLAlchemy 기본값과 같다. 추천 경로는 DB 구간에서만 session을 잡아
    # 동시 추천 수(32)보다 작아도 바로 막히지 않는다. 부하 측정 뒤 다시 본다.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 30.0
    # API 요청의 SQL 하나가 넘으면 안 되는 시간. 검색과 tag 조회는 ms 단위다.
    # CLI batch(collect, index)에는 걸지 않는다.
    db_statement_timeout_ms: int = 5000

    # 임베딩도 OpenAI 호환 /v1/embeddings 사용 (ollama pull bge-m3 등 로컬 포함)
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "bge-m3"
    embedding_api_key: str = ""

    # LLM은 OpenAI 호환 /v1 엔드포인트 사용 (openai, ollama, moonshot)
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.moonshot.ai/v1"
    llm_model: str = "kimi-k3"
    # 추론 모델은 느리다. kimi-k3로 실측하니 이 작업에 59초 걸렸다.
    # 임베딩(1초 미만)과 같은 값을 쓸 수 없어 분리한다.
    llm_timeout_seconds: float = 120.0
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WHATFROM_LLM_API_KEY", "MOONSHOT_API_KEY"),
    )

    # 단계별 LLM 설정(spec F14). 조건 추출 단계(plan)와 추천 단계(recommend)가 다른
    # 공급자와 모델을 쓸 수 있다. None(설정하지 않음)이면 위의 공통 설정을 쓴다.
    # API 키는 빈 값과 None을 구분한다. 빈 값이면 인증 header를 보내지 않는다 — 인증이
    # 필요 없는 endpoint로 다른 공급자의 키가 나가지 않게 한다.
    plan_llm_base_url: str | None = None
    plan_llm_model: str | None = None
    plan_llm_api_key: str | None = None
    plan_llm_extra_body: dict | None = None
    recommend_llm_base_url: str | None = None
    recommend_llm_model: str | None = None
    recommend_llm_api_key: str | None = None
    recommend_llm_extra_body: dict | None = None

    @field_validator(
        "plan_llm_base_url",
        "plan_llm_model",
        "plan_llm_extra_body",
        "recommend_llm_base_url",
        "recommend_llm_model",
        "recommend_llm_extra_body",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        """빈 값은 설정하지 않은 것으로 본다. 빈 값이 뜻을 갖는 것은 API 키뿐이다."""
        return None if value == "" else value

    @field_validator("plan_llm_extra_body", "recommend_llm_extra_body")
    @classmethod
    def _keep_reserved_keys(cls, value: dict | None) -> dict | None:
        """응답 형식을 바꾸는 키(stream, n 등)는 막지 않는다. 넣으면 첫 호출이 실패한다."""
        reserved = sorted(RESERVED_LLM_BODY_KEYS & set(value or {}))
        if reserved:
            raise ValueError(f"extra body cannot set {', '.join(reserved)}")
        return value

    # 동시에 수락하는 추천 작업 수(스펙 F11-a). 스레드 풀(기본 40)보다 작게 둬 /health 같은
    # 동기 요청이 쓸 스레드를 남긴다. 최적값으로 검증한 값이 아니라 시작값이다.
    max_concurrent_recommendations: int = 32
    # 거절 응답의 Retry-After(초). 한 요청 시간의 중앙값에 가깝게 둔다.
    recommend_retry_after_seconds: int = 60


settings = Settings()
