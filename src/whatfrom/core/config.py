# src/whatfrom/core/config.py
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WHATFROM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom"
    test_database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom_test"
    embedder: str = "fake"

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

    # 동시에 수락하는 추천 작업 수(스펙 F11-a). 스레드 풀(기본 40)보다 작게 둬 /health 같은
    # 동기 요청이 쓸 스레드를 남긴다. 최적값으로 검증한 값이 아니라 시작값이다.
    max_concurrent_recommendations: int = 32
    # 거절 응답의 Retry-After(초). 한 요청 시간의 중앙값에 가깝게 둔다.
    recommend_retry_after_seconds: int = 60


settings = Settings()
