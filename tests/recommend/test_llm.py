import json

import httpx2
import pytest

from whatfrom.core.contracts import Recommendation, SearchPlan
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.llm import FakeLLMProvider, OpenAICompatibleProvider, strict_json_schema

VALID_CONTENT = (
    '{"image": "python:3.13-slim", "reason": "glibc", '
    '"dockerfile": "FROM python:3.13-slim", "alternatives": []}'
)


def _provider(handler, **kwargs) -> OpenAICompatibleProvider:
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    kwargs.setdefault("sleep", lambda _: None)  # 테스트에서 백오프로 잠들지 않는다
    return OpenAICompatibleProvider(
        base_url="http://llm.invalid/v1", model="test-model", api_key="k", client=client, **kwargs
    )


def test_provider_posts_to_chat_completions_with_bearer_auth_and_model():
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"choices": [{"message": {"content": VALID_CONTENT}}]})

    result = _provider(handler).recommend("sys", "prompt")

    assert seen["url"] == "http://llm.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert result.image == "python:3.13-slim"


def test_strict_schema_lists_every_property_as_required():
    """OpenAI strict 모드는 properties와 required가 같아야 하고 아니면 400을 낸다.

    Pydantic은 기본값이 있는 필드를 required에서 빼므로 alternatives가 빠진다.
    Kimi와 vLLM은 관대해서 통과시키지만 OpenAI는 거부한다.
    """
    schema = strict_json_schema(Recommendation)

    assert set(schema["required"]) == set(schema["properties"])
    assert "alternatives" in schema["required"]
    # Pydantic 기본 동작과 다르다는 것 자체를 확인해 둔다.
    assert "alternatives" not in Recommendation.model_json_schema()["required"]


def test_strict_schema_does_not_mutate_the_model():
    """스키마를 두 번 만들어도 원본 모델의 기본 스키마는 그대로여야 한다."""
    strict_json_schema(Recommendation)

    assert "alternatives" not in Recommendation.model_json_schema()["required"]


def test_provider_requests_a_json_schema_forbidding_extra_fields():
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"choices": [{"message": {"content": VALID_CONTENT}}]})

    _provider(handler).recommend("sys", "prompt")
    schema = seen["body"]["response_format"]["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"image", "reason", "dockerfile", "alternatives"}


def test_provider_raises_llm_error_when_the_server_ignores_the_schema():
    """json_schema를 무시하고 산문을 뱉는 서버가 실제로 있다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": "제 생각에는 slim이 좋겠습니다."}}]}
        )

    with pytest.raises(RemoteCallError, match="did not match Recommendation schema"):
        _provider(handler).recommend("sys", "prompt")


def test_provider_raises_llm_error_on_a_malformed_envelope():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"unexpected": "shape"})

    with pytest.raises(RemoteCallError, match="malformed completion response"):
        _provider(handler).recommend("sys", "prompt")


PLAN_CONTENT = (
    '{"repository": "python", "version_prefix": "3.12", "architectures": ["arm64"], '
    '"distributions": [], "exclude_distributions": ["alpine"], "max_size_mb": null}'
)


def test_plan_requests_a_strict_search_plan_schema():
    """검색 조건도 추천과 같은 방식으로 JSON 스키마를 강제해 받는다."""
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"choices": [{"message": {"content": PLAN_CONTENT}}]})

    plan = _provider(handler).plan("sys", "prompt")

    json_schema = seen["body"]["response_format"]["json_schema"]
    assert json_schema["name"] == "search_plan"
    assert json_schema["schema"]["additionalProperties"] is False
    assert set(json_schema["schema"]["required"]) == set(json_schema["schema"]["properties"])
    assert plan == SearchPlan(
        repository="python",
        version_prefix="3.12",
        architectures=["arm64"],
        exclude_distributions=["alpine"],
    )


def test_plan_raises_llm_error_when_the_response_is_not_a_search_plan():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": '{"image": "python:3"}'}}]}
        )

    with pytest.raises(RemoteCallError, match="did not match SearchPlan schema"):
        _provider(handler).plan("sys", "prompt")


def test_fake_provider_returns_an_empty_plan_unless_configured():
    """plan을 신경 쓰지 않는 테스트가 예전과 같은 후보를 받도록, 기본은 조건 없음이다."""
    assert FakeLLMProvider().plan("sys", "prompt") == SearchPlan()
    configured = SearchPlan(repository="node")
    assert FakeLLMProvider(plan=configured).plan("sys", "prompt") == configured


def test_fake_provider_can_fail_only_the_plan():
    provider = FakeLLMProvider(plan_error=RemoteCallError("boom"))

    with pytest.raises(RemoteCallError, match="boom"):
        provider.plan("sys", "prompt")
    assert provider.plan_calls == [("sys", "prompt")]
