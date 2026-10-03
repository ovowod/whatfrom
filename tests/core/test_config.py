# tests/core/test_config.py
import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

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


@pytest.mark.parametrize("key", ["model", "messages", "response_format"])
def test_extra_body_cannot_override_what_the_code_decides(key):
    with pytest.raises(ValidationError, match=key):
        Settings(_env_file=None, recommend_llm_extra_body={key: "x"})


@pytest.mark.parametrize("raw", ['["reasoning_effort"]', "{not json"])
def test_extra_body_from_the_environment_must_be_a_json_object(monkeypatch, raw):
    monkeypatch.setenv("WHATFROM_PLAN_LLM_EXTRA_BODY", raw)

    with pytest.raises((ValidationError, SettingsError)):
        Settings(_env_file=None)


def test_extra_body_is_read_from_the_environment_as_json(monkeypatch):
    monkeypatch.setenv("WHATFROM_PLAN_LLM_EXTRA_BODY", '{"reasoning_effort": "none"}')

    assert Settings(_env_file=None).plan_llm_extra_body == {"reasoning_effort": "none"}
