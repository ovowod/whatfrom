import json

import httpx2
import pytest

from whatfrom.httpclient import RemoteCallError
from whatfrom.llm import OpenAICompatibleProvider

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
