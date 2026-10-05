import json

import httpx2
import pytest

from whatfrom.core.config import Settings
from whatfrom.core.contracts import Recommendation, SearchPlan
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.llm import (
    AnthropicProvider,
    FakeLLMProvider,
    LLMCall,
    OpenAICompatibleProvider,
    get_provider,
    strict_json_schema,
)

VALID_CONTENT = '{"image": "python:3.13-slim", "reason": "glibc", "alternatives": []}'


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
    assert set(schema["properties"]) == {"image", "reason", "alternatives"}


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


def _capture(seen: list):
    """요청을 단계(응답 schema 이름)별로 seen에 남기고, 그 단계에 맞는 응답을 준다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        stage = body["response_format"]["json_schema"]["name"]
        seen.append(
            {
                "stage": stage,
                "url": str(request.url),
                "auth": request.headers.get("Authorization"),
                "body": body,
            }
        )
        content = PLAN_CONTENT if stage == "search_plan" else VALID_CONTENT
        return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


def _staged(config: Settings, seen: list):
    client = httpx2.Client(transport=httpx2.MockTransport(_capture(seen)))
    return get_provider("openai_compatible", config=config, client=client)


def _call_both(provider) -> None:
    provider.plan("sys", "q")
    provider.recommend("sys", "p")


def test_each_stage_uses_its_own_model_and_falls_back_to_the_common_one():
    config = Settings(_env_file=None, llm_model="common-model", plan_llm_model="plan-model")
    seen: list = []

    _call_both(_staged(config, seen))

    models = {call["stage"]: call["body"]["model"] for call in seen}
    assert models == {"search_plan": "plan-model", "recommendation": "common-model"}


def test_each_stage_uses_its_own_endpoint_and_key():
    config = Settings(
        _env_file=None,
        llm_base_url="http://common.invalid/v1",
        WHATFROM_LLM_API_KEY="common-key",
        plan_llm_base_url="http://plan.invalid/v1",
        plan_llm_api_key="plan-key",
    )
    seen: list = []

    _call_both(_staged(config, seen))

    calls = {call["stage"]: (call["url"], call["auth"]) for call in seen}
    assert calls == {
        "search_plan": ("http://plan.invalid/v1/chat/completions", "Bearer plan-key"),
        "recommendation": ("http://common.invalid/v1/chat/completions", "Bearer common-key"),
    }


def test_an_empty_stage_key_sends_no_auth_instead_of_the_common_key():
    """인증이 필요 없는 endpoint로 다른 공급자의 키가 나가지 않게 한다."""
    config = Settings(_env_file=None, WHATFROM_LLM_API_KEY="common-key", recommend_llm_api_key="")
    seen: list = []

    _call_both(_staged(config, seen))

    auths = {call["stage"]: call["auth"] for call in seen}
    assert auths == {"search_plan": "Bearer common-key", "recommendation": None}


def test_a_stage_extra_body_is_merged_into_that_stage_request_only():
    config = Settings(_env_file=None, recommend_llm_extra_body={"reasoning_effort": "low"})
    seen: list = []

    _call_both(_staged(config, seen))

    efforts = {call["stage"]: call["body"].get("reasoning_effort") for call in seen}
    assert efforts == {"search_plan": None, "recommendation": "low"}


def test_without_stage_settings_both_stages_send_the_same_request_as_before():
    config = Settings(
        _env_file=None,
        llm_base_url="http://common.invalid/v1",
        llm_model="common-model",
        WHATFROM_LLM_API_KEY="common-key",
    )
    seen: list = []

    _call_both(_staged(config, seen))

    for call in seen:
        assert call["url"] == "http://common.invalid/v1/chat/completions"
        assert call["auth"] == "Bearer common-key"
        assert set(call["body"]) == {"model", "messages", "response_format"}
        assert call["body"]["model"] == "common-model"


USAGE = {
    "prompt_tokens": 1200,
    "completion_tokens": 300,
    "completion_tokens_details": {"reasoning_tokens": 250},
}


def _recorded(handler, method: str = "recommend") -> list[LLMCall]:
    calls: list[LLMCall] = []
    provider = _provider(handler, on_call=calls.append)
    try:
        getattr(provider, method)("sys", "prompt")
    except RemoteCallError:
        pass
    return calls


def test_a_successful_call_records_its_stage_attempts_and_token_usage():
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = {"message": {"content": PLAN_CONTENT}}
        return httpx2.Response(200, json={"choices": [message], "usage": USAGE})

    [call] = _recorded(handler, "plan")

    assert (call.stage, call.attempts, call.ok, call.error) == ("plan", 1, True, None)
    assert (call.input_tokens, call.output_tokens, call.reasoning_tokens) == (1200, 300, 250)


def test_a_call_that_gives_up_still_leaves_one_record_with_every_attempt():
    [call] = _recorded(lambda _: httpx2.Response(429))

    assert (call.stage, call.attempts, call.ok) == ("recommend", 3, False)
    assert "429" in call.error
    assert call.input_tokens is None


def test_a_response_that_fails_the_schema_still_records_its_token_usage():
    """응답은 왔으니 비용이 들었다. 검증 실패로 기록에서 빠지면 비용이 적게 잡힌다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        message = {"message": {"content": '{"wrong": true}'}}
        return httpx2.Response(200, json={"choices": [message], "usage": USAGE})

    [call] = _recorded(handler)

    assert (call.ok, call.input_tokens, call.output_tokens) == (False, 1200, 300)


def test_a_response_without_usage_records_unknown_token_counts():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"choices": [{"message": {"content": VALID_CONTENT}}]})

    [call] = _recorded(handler)

    assert call.ok is True
    assert (call.input_tokens, call.output_tokens, call.reasoning_tokens) == (None, None, None)


ANTHROPIC_USAGE = {
    "input_tokens": 1500,
    "output_tokens": 400,
    "output_tokens_details": {"thinking_tokens": 320},
}


def _anthropic_reply(text: str, *, stop_reason: str = "end_turn", usage: dict | None = None):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": ANTHROPIC_USAGE if usage is None else usage,
    }


def _capture_any(seen: list, reply=None):
    """두 API의 요청을 모두 seen에 남긴다. Anthropic 요청에는 reply(body)를 돌려준다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "headers": request.headers, "body": body})
        if str(request.url).endswith("/messages"):
            payload = reply(body) if reply else _anthropic_reply(VALID_CONTENT)
            return httpx2.Response(200, json=payload)
        stage = body["response_format"]["json_schema"]["name"]
        content = PLAN_CONTENT if stage == "search_plan" else VALID_CONTENT
        return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


def _anthropic_config(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "llm_base_url": "http://common.invalid/v1",
        "recommend_llm_api": "anthropic",
        "recommend_llm_base_url": "http://anthropic.invalid/v1",
        "recommend_llm_model": "claude-test",
        "recommend_llm_api_key": "anthropic-key",
    }
    return Settings(**(values | overrides))


def _anthropic_staged(config: Settings, seen: list, reply=None, on_call=None):
    client = httpx2.Client(transport=httpx2.MockTransport(_capture_any(seen, reply)))
    return get_provider("openai_compatible", config=config, client=client, on_call=on_call)


def test_a_stage_set_to_anthropic_calls_the_messages_api_and_the_other_stage_does_not():
    seen: list = []
    provider = _anthropic_staged(_anthropic_config(), seen)

    provider.plan("sys", "q")
    result = provider.recommend("system prompt", "user prompt")

    urls = [call["url"] for call in seen]
    assert urls == [
        "http://common.invalid/v1/chat/completions",
        "http://anthropic.invalid/v1/messages",
    ]
    assert result.image == "python:3.13-slim"


def test_an_anthropic_request_carries_its_headers_system_output_limit_and_schema():
    seen: list = []

    _anthropic_staged(_anthropic_config(), seen).recommend("system prompt", "user prompt")

    [call] = seen
    assert call["headers"]["x-api-key"] == "anthropic-key"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in call["headers"]
    body = call["body"]
    assert body["model"] == "claude-test"
    assert body["system"] == "system prompt"
    assert body["messages"] == [{"role": "user", "content": "user prompt"}]
    assert body["max_tokens"] == 16_000
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": strict_json_schema(Recommendation)}
    }


def test_an_empty_anthropic_key_sends_no_api_key_header():
    seen: list = []

    _anthropic_staged(_anthropic_config(recommend_llm_api_key=""), seen).recommend("s", "p")

    [call] = seen
    assert call["url"].endswith("/messages")
    assert "x-api-key" not in call["headers"]


def test_an_anthropic_call_records_its_usage_with_thinking_as_reasoning():
    calls: list[LLMCall] = []

    _anthropic_staged(_anthropic_config(), [], on_call=calls.append).recommend("s", "p")

    [call] = calls
    assert (call.stage, call.attempts, call.ok) == ("recommend", 1, True)
    assert (call.input_tokens, call.output_tokens, call.reasoning_tokens) == (1500, 400, 320)


def test_an_anthropic_call_without_thinking_tokens_records_none_for_reasoning():
    calls: list[LLMCall] = []

    def reply(body: dict) -> dict:
        return _anthropic_reply(VALID_CONTENT, usage={"input_tokens": 10, "output_tokens": 5})

    _anthropic_staged(_anthropic_config(), [], reply, calls.append).recommend("s", "p")

    assert (calls[0].input_tokens, calls[0].output_tokens, calls[0].reasoning_tokens) == (
        10,
        5,
        None,
    )


def test_thinking_and_effort_from_extra_body_stay_beside_the_schema():
    seen: list = []
    extra = {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}

    _anthropic_staged(_anthropic_config(recommend_llm_extra_body=extra), seen).recommend("s", "p")

    body = seen[0]["body"]
    assert body["thinking"] == {"type": "between_tools"}
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": strict_json_schema(Recommendation)},
    }


def _anthropic_calls(reply) -> tuple[list[LLMCall], Exception | None]:
    """reply(body)를 돌려주는 Anthropic 추천 호출 하나의 기록과 오류."""
    calls: list[LLMCall] = []
    provider = _anthropic_staged(_anthropic_config(), [], reply, calls.append)
    try:
        provider.recommend("s", "p")
    except RemoteCallError as exc:
        return calls, exc
    return calls, None


def test_a_thinking_block_before_the_answer_is_skipped():
    def reply(body: dict) -> dict:
        answer = _anthropic_reply(VALID_CONTENT)
        answer["content"].insert(0, {"type": "thinking", "thinking": "hmm", "signature": "x"})
        return answer

    calls, error = _anthropic_calls(reply)

    assert error is None
    assert calls[0].ok


@pytest.mark.parametrize(
    ("stop_reason", "reason"), [("max_tokens", "truncated"), ("refusal", "refused")]
)
def test_a_cut_off_or_refused_answer_fails_with_its_reason_and_keeps_its_usage(stop_reason, reason):
    calls, error = _anthropic_calls(
        lambda body: _anthropic_reply('{"image": "py', stop_reason=stop_reason)
    )

    assert reason in str(error)
    [call] = calls
    assert (call.ok, call.input_tokens, call.output_tokens) == (False, 1500, 400)
    assert reason in call.error


@pytest.mark.parametrize(
    "content",
    [
        [],
        None,
        ["not a block"],
        [{"type": "text", "text": None}],
    ],
)
def test_an_answer_of_the_wrong_shape_breaks_the_response_contract(content):
    """응답 계약 위반은 모두 RemoteCallError여야 기록이 남고 호출자가 저하 사다리를 탄다."""
    calls, error = _anthropic_calls(
        lambda body: {"content": content, "stop_reason": "end_turn", "usage": ANTHROPIC_USAGE}
    )

    assert "malformed messages response" in str(error)
    assert not calls[0].ok


@pytest.mark.parametrize("status", [429, 529])
def test_a_rate_limited_or_overloaded_anthropic_api_is_retried(status):
    """529는 Anthropic의 overloaded_error다(Claude API errors 문서)."""
    calls: list[LLMCall] = []
    replies = iter(
        [httpx2.Response(status), httpx2.Response(200, json=_anthropic_reply(VALID_CONTENT))]
    )
    provider = AnthropicProvider(
        base_url="http://anthropic.invalid/v1",
        model="claude-test",
        api_key="k",
        client=httpx2.Client(transport=httpx2.MockTransport(lambda request: next(replies))),
        sleep=lambda _: None,
        on_call=calls.append,
    )

    provider.recommend("s", "p")

    assert (calls[0].attempts, calls[0].ok) == (2, True)
