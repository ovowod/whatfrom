# src/whatfrom/config.py
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WHATFROM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom"
    test_database_url: str = "postgresql+psycopg://whatfrom:whatfrom@localhost:5432/whatfrom_test"
    embedder: str = "fake"

    # LLM은 OpenAI 호환 /v1 엔드포인트 사용 (openai, ollama, moonshot)
    llm_provider: str = "fake"
    llm_base_url: str = "https://api.moonshot.ai/v1"
    llm_model: str = "kimi-k3"
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("WHATFROM_LLM_API_KEY", "MOONSHOT_API_KEY"),
    )


settings = Settings()
