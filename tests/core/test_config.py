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


@pytest.mark.parametrize("name", ["BASE_URL", "MODEL", "EXTRA_BODY"])
def test_an_empty_stage_setting_counts_as_unset(monkeypatch, name):
    """.env.example의 주석만 지운 줄(`KEY=`)이 시작을 막거나 빈 endpoint로 가면 안 된다."""
    monkeypatch.setenv(f"WHATFROM_PLAN_LLM_{name}", "")

    assert getattr(Settings(_env_file=None), f"plan_llm_{name.lower()}") is None


def test_an_empty_stage_api_key_stays_empty(monkeypatch):
    """API 키만 빈 값이 뜻을 갖는다. 인증 header를 보내지 않는다."""
    monkeypatch.setenv("WHATFROM_PLAN_LLM_API_KEY", "")

    assert Settings(_env_file=None).plan_llm_api_key == ""


@pytest.mark.parametrize("stage", ["PLAN", "RECOMMEND"])
def test_a_stage_api_accepts_anthropic(monkeypatch, stage):
    monkeypatch.setenv(f"WHATFROM_{stage}_LLM_API", "anthropic")

    assert getattr(Settings(_env_file=None), f"{stage.lower()}_llm_api") == "anthropic"


def test_an_empty_stage_api_counts_as_unset(monkeypatch):
    monkeypatch.setenv("WHATFROM_RECOMMEND_LLM_API", "")

    assert Settings(_env_file=None).recommend_llm_api is None


def test_an_unknown_stage_api_stops_startup():
    with pytest.raises(ValidationError, match="recommend_llm_api"):
        Settings(_env_file=None, recommend_llm_api="anthropc")


@pytest.mark.parametrize(
    "extra",
    [
        {"system": "x"},
        {"max_tokens": 10},
        {"output_config": {"format": {"type": "json_schema"}}},
    ],
)
def test_an_anthropic_extra_body_cannot_override_what_the_code_decides(extra):
    with pytest.raises(ValidationError, match="extra body"):
        Settings(_env_file=None, recommend_llm_api="anthropic", recommend_llm_extra_body=extra)


@pytest.mark.parametrize("value", [None, 42, "low", ["effort"]])
def test_an_anthropic_output_config_must_be_an_object(value):
    with pytest.raises(ValidationError, match="output_config"):
        Settings(
            _env_file=None,
            recommend_llm_api="anthropic",
            recommend_llm_extra_body={"output_config": value},
        )


def test_an_anthropic_extra_body_may_set_thinking_and_effort():
    extra = {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}

    config = Settings(_env_file=None, plan_llm_api="anthropic", plan_llm_extra_body=extra)

    assert config.plan_llm_extra_body == extra


def test_an_openai_compatible_stage_may_still_send_max_tokens():
    """Anthropic 전용 금지 키는 OpenAI 호환 단계에 적용하지 않는다."""
    config = Settings(_env_file=None, recommend_llm_extra_body={"max_tokens": 10})

    assert config.recommend_llm_extra_body == {"max_tokens": 10}
