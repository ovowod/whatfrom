# tests/core/test_config.py
from whatfrom.core.config import Settings


def test_llm_api_key_accepts_the_vendor_env_name(monkeypatch):
    monkeypatch.delenv("WHATFROM_LLM_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-from-vendor-name")

    assert Settings(_env_file=None).llm_api_key == "sk-from-vendor-name"


def test_llm_api_key_prefers_the_neutral_env_name(monkeypatch):
    monkeypatch.setenv("WHATFROM_LLM_API_KEY", "sk-neutral")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-vendor")

    assert Settings(_env_file=None).llm_api_key == "sk-neutral"


def test_llm_api_key_defaults_to_empty_when_unset(monkeypatch):
    """로컬 Ollama처럼 키가 필요 없는 백엔드도 있다."""
    monkeypatch.delenv("WHATFROM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

    assert Settings(_env_file=None).llm_api_key == ""
