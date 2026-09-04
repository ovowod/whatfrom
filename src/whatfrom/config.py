# src/whatfrom/config.py
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WHATFROM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom"
    test_database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom_test"
    embedder: str = "fake"

    # LLM은 OpenAI 호환 /v1 엔드포인트로만 말한다. 벤더 이름을 설정에 넣지 않는다 —
    # base_url을 바꾸면 vLLM·Ollama·Kimi·호스팅 API가 전부 같은 코드로 붙는다.
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.moonshot.ai/v1"
    llm_model: str = "kimi-k3"
    # 키 이름만은 발급처를 따른다 — 사용자의 다른 프로젝트가 이미 이 이름을 쓴다.
    # validation_alias를 주면 env_prefix가 적용되지 않으므로 중립 이름을 전체로 적는다.
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WHATFROM_LLM_API_KEY", "MOONSHOT_API_KEY"),
    )


settings = Settings()
